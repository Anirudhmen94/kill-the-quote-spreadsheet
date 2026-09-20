"""Inbox: vendor replies arrive (simulated or uploaded), get read, get extracted."""
from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from core import clarify, demo_ops, extractor, ingest, llm, snapshots, storage, vendor_sim
from core.web import error_fragment, hx_redirect, load_or_404, now_iso, render

router = APIRouter()

# Serialise state merges when parallel extracts finish (LLM work stays concurrent).
_extract_save_lock = threading.Lock()


def _vendor(state: dict, vendor_id: str) -> dict:
    for v in state["vendors"]:
        if v["vendor_id"] == vendor_id:
            return v
    raise HTTPException(404, "vendor not found")


@router.get("/rfx/{rfx_id}/email", response_class=HTMLResponse)
def email_page(request: Request, rfx_id: str):
    state = load_or_404(rfx_id)
    return render(request, "email.html", state=state, active="email", data_status_context="inbox")


@router.get("/rfx/{rfx_id}/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, rfx_id: str):
    """Legacy alias → Email (incoming tab)."""
    q = ("?" + request.url.query) if request.url.query else ""
    return RedirectResponse(f"/rfx/{rfx_id}/email{q}#inbox", status_code=303)


@router.post("/rfx/{rfx_id}/simulate", response_class=HTMLResponse)
def simulate(request: Request, rfx_id: str, then: str | None = None):
    state = load_or_404(rfx_id)
    # guard_destructive allows first Simulate (no files); blocks overwrite under demo mode.
    ok, msg = demo_ops.guard_destructive(state, "regenerate_replies")
    if not ok:
        return error_fragment(msg + " Use Interview reset for a clean messy seed.", 403)
    try:
        vendor_sim.simulate_replies(state)
        vids = [v["vendor_id"] for v in state.get("vendors", []) if v.get("files")]
        snapshots.bump_vendor_data_version(state, "vendor_simulated", affected_vendor_ids=vids)
        storage.save_state(rfx_id, state)
    except Exception as e:
        return error_fragment(f"Could not generate vendor replies: {e}")
    if then == "extract" and llm.is_configured():
        return hx_redirect(f"/rfx/{rfx_id}/email?extract=1")
    return hx_redirect(f"/rfx/{rfx_id}/email")


@router.post("/rfx/{rfx_id}/upload", response_class=HTMLResponse)
async def upload(request: Request, rfx_id: str, new_vendor_name: str = Form(""), files: list[UploadFile] = File(...)):
    """Create a new vendor from name + files. Attach-to-existing was removed from the UI."""
    state = load_or_404(rfx_id)
    if not files or all(not f.filename for f in files):
        return error_fragment("Choose at least one file.", 400)
    name = (new_vendor_name or "").strip()
    if not name:
        return error_fragment("Enter a vendor name.", 400)
    vendor = vendor_sim.empty_vendor({"vendor_id": "v" + uuid.uuid4().hex[:5], "name": name, "city": "", "email": "", "format": "upload"})
    state["vendors"].append(vendor)
    for f in files:
        if not f.filename:
            continue
        data = await f.read()
        if len(data) > 25 * 1024 * 1024:
            return error_fragment(f"{f.filename} is larger than 25 MB.", 400)
        kind = ingest.kind_from_name(f.filename)
        if kind == "unknown":
            return error_fragment(
                f"Unsupported file type: {f.filename}. "
                "Use Excel (.xlsx/.xls), PDF, Word (.docx), images (.png/.jpg/.webp/.gif), email (.eml/.txt/.msg), or CSV.",
                400,
            )
        fid = uuid.uuid4().hex[:8]
        url = storage.put_bytes(f"rfx/{rfx_id}/files/{vendor['vendor_id']}/{fid}-{f.filename}", data, ingest.guess_content_type(f.filename))
        vendor["files"].append({"file_id": fid, "name": f.filename, "kind": kind, "content_type": ingest.guess_content_type(f.filename), "url": url, "size": len(data), "role": "quote"})
    vendor["status"] = "received"
    vendor["extraction"] = None  # new evidence invalidates the old reading
    vendor["received_at"] = now_iso()
    state["status"] = "responses_received"
    file_ids = [f["file_id"] for f in vendor["files"]]
    snapshots.bump_vendor_data_version(
        state,
        "vendor_uploaded",
        affected_vendor_ids=[vendor["vendor_id"]],
        affected_file_ids=file_ids,
    )
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/email")




def _run_one_extract(rfx_id: str, vendor_id: str, state_snap: dict, vendor_snap: dict) -> dict:
    """LLM extract for one vendor using frozen snapshots (no shared mutable state)."""
    log: list = []
    vendor = copy.deepcopy(vendor_snap)
    vendor["status"] = "extracting"
    vendor["error"] = None
    vendor["texts"] = {}
    try:
        new_ext = extractor.extract_vendor(state_snap, vendor, storage.get_bytes, log=log)
        return {
            "vendor_id": vendor_id,
            "ok": True,
            "extraction": new_ext,
            "texts": vendor.get("texts") or {},
            "log": log,
            "error": None,
        }
    except llm.AINotConfigured as e:
        return {"vendor_id": vendor_id, "ok": False, "error": str(e), "log": log, "ai_missing": True}
    except Exception as e:
        return {
            "vendor_id": vendor_id,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "log": log,
            "ai_missing": False,
        }


@router.post("/rfx/{rfx_id}/extract-all", response_class=HTMLResponse)
def extract_all(request: Request, rfx_id: str):
    """Read all pending vendor replies in parallel (max 5 workers). Merges under a lock."""
    state = load_or_404(rfx_id)
    pending = [
        v for v in state.get("vendors", [])
        if v.get("files") and v.get("status") not in ("extracted",)
    ]
    if not pending:
        return hx_redirect(f"/rfx/{rfx_id}/email")
    if not llm.is_configured():
        return error_fragment("ANTHROPIC_API_KEY is not set. Extraction refuses to invent output.", 400)

    # Snapshot inputs once; mark extracting so the UI can show Processing.
    for v in pending:
        v["status"] = "extracting"
        v["error"] = None
    storage.save_state(rfx_id, state)

    jobs = []
    for v in pending:
        snap_state = {
            "id": state["id"],
            "rfx": copy.deepcopy(state["rfx"]),
            "fx": copy.deepcopy(state.get("fx") or {}),
        }
        jobs.append((v["vendor_id"], snap_state, copy.deepcopy(v)))

    results = []
    with ThreadPoolExecutor(max_workers=min(5, len(jobs))) as pool:
        futs = {
            pool.submit(_run_one_extract, rfx_id, vid, st, vv): vid
            for vid, st, vv in jobs
        }
        for fut in as_completed(futs):
            results.append(fut.result())

    with _extract_save_lock:
        state = load_or_404(rfx_id)
        log = state.setdefault("ai_log", [])
        for res in results:
            vendor = _vendor(state, res["vendor_id"])
            was_extracted = vendor.get("status") == "extracted" and bool(vendor.get("extraction"))
            prior_extraction = vendor.get("extraction")
            prior_texts = vendor.get("texts")
            log.extend(res.get("log") or [])
            if res.get("ok"):
                vendor["extraction"] = res["extraction"]
                vendor["texts"] = res.get("texts") or {}
                vendor["status"] = "extracted"
                vendor["extracted_at"] = now_iso()
                vendor["error"] = None
                snapshots.bump_vendor_data_version(
                    state,
                    "vendor_extracted",
                    affected_vendor_ids=[res["vendor_id"]],
                    affected_file_ids=[f["file_id"] for f in vendor.get("files", [])],
                )
            else:
                if was_extracted and prior_extraction is not None:
                    vendor["extraction"] = prior_extraction
                    vendor["texts"] = prior_texts or {}
                    vendor["status"] = "extracted"
                    vendor["error"] = f"Re-read failed; keeping previous. {res.get('error')}"
                else:
                    vendor["status"] = "error"
                    vendor["error"] = res.get("error") or "extract failed"
        if all(v.get("status") == "extracted" for v in state["vendors"] if v.get("files")):
            state["status"] = "compared"
        storage.save_state(rfx_id, state)

    return hx_redirect(f"/rfx/{rfx_id}/email")


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/extract", response_class=HTMLResponse)
def extract(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    if not vendor["files"]:
        vendor["error"] = "No files to read."
        return render(request, "partials/vendor_card.html", state=state, v=vendor)
    was_extracted = vendor.get("status") == "extracted" and bool(vendor.get("extraction"))
    prior_extraction = vendor.get("extraction")
    prior_texts = vendor.get("texts")
    base_version = snapshots.current_version(state)
    try:
        vendor["status"] = "extracting"
        vendor["error"] = None
        vendor["texts"] = {}  # re-read from the files every time; never reuse a stale transcription
        # Persist extracting status so other pages can show Processing
        storage.save_state(rfx_id, state)
        # Reload to pick up concurrent bumps (optimistic check later)
        state = load_or_404(rfx_id)
        vendor = _vendor(state, vendor_id)
        vendor["status"] = "extracting"
        vendor["error"] = None
        vendor["texts"] = {}
        call_log: list = []
        new_ext = extractor.extract_vendor(state, vendor, storage.get_bytes, log=call_log)
        texts_snap = dict(vendor.get("texts") or {})
        with _extract_save_lock:
            # Reload so parallel sibling extracts are not overwritten.
            state = load_or_404(rfx_id)
            vendor = _vendor(state, vendor_id)
            state.setdefault("ai_log", []).extend(call_log)
            vendor["extraction"] = new_ext
            vendor["texts"] = texts_snap
            vendor["status"] = "extracted"
            vendor["extracted_at"] = now_iso()
            vendor["error"] = None
            vendor["prior_extractions"] = vendor.get("prior_extractions") or []
            if was_extracted and prior_extraction is not None:
                vendor["prior_extractions"].append(
                    {"at": now_iso(), "extraction": prior_extraction, "vendor_data_version": base_version}
                )
                if len(vendor["prior_extractions"]) > 5:
                    vendor["prior_extractions"] = vendor["prior_extractions"][-5:]
                reason = "vendor_reprocessed"
            else:
                reason = "vendor_extracted"
            snapshots.bump_vendor_data_version(
                state,
                reason,
                affected_vendor_ids=[vendor_id],
                affected_file_ids=[f["file_id"] for f in vendor.get("files", [])],
            )
            if all(v.get("status") == "extracted" for v in state["vendors"] if v.get("files")):
                state["status"] = "compared"
            storage.save_state(rfx_id, state)
        return render(request, "partials/vendor_card.html", state=state, v=vendor)
    except llm.AINotConfigured as e:
        with _extract_save_lock:
            state = load_or_404(rfx_id)
            vendor = _vendor(state, vendor_id)
            vendor["status"] = "received" if not was_extracted else "extracted"
            if was_extracted:
                vendor["extraction"] = prior_extraction
                vendor["texts"] = prior_texts or vendor.get("texts") or {}
            vendor["error"] = str(e)
            storage.save_state(rfx_id, state)
    except Exception as e:
        with _extract_save_lock:
            state = load_or_404(rfx_id)
            vendor = _vendor(state, vendor_id)
            # Preserve prior successful extraction as historical/current unless user re-reads successfully
            if was_extracted and prior_extraction is not None:
                vendor["extraction"] = prior_extraction
                vendor["texts"] = prior_texts or {}
                vendor["status"] = "extracted"
                vendor["last_failed_attempt"] = {"at": now_iso(), "error": f"{type(e).__name__}: {e}"}
                vendor["error"] = f"Re-read failed; keeping previous successful extraction. {type(e).__name__}: {e}"
            else:
                vendor["status"] = "error"
                vendor["error"] = f"{type(e).__name__}: {e}"
            storage.save_state(rfx_id, state)
    return render(request, "partials/vendor_card.html", state=state, v=vendor)


@router.get("/rfx/{rfx_id}/vendor/{vendor_id}/card", response_class=HTMLResponse)
def vendor_card(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    return render(request, "partials/vendor_card.html", state=state, v=_vendor(state, vendor_id))


@router.get("/rfx/{rfx_id}/vendor/{vendor_id}/text/{file_id}", response_class=HTMLResponse)
def vendor_text(request: Request, rfx_id: str, vendor_id: str, file_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    f = next((f for f in vendor["files"] if f["file_id"] == file_id), None)
    if not f:
        raise HTTPException(404)
    t = vendor.get("texts", {}).get(file_id)
    if t is None:
        try:
            data = storage.get_bytes(f["url"])
            t = ingest.file_to_text(f["name"], data, f["kind"], log=state.setdefault("ai_log", [])) if data else {"text": "", "method": "missing"}
            vendor.setdefault("texts", {})[file_id] = t
            storage.save_state(rfx_id, state)
        except Exception as e:
            t = {"text": "", "method": "failed", "error": str(e)}
    return render(request, "partials/text_drawer.html", state=state, v=vendor, f=f, t=t)


def _outbox_clarification(state: dict, vendor_id: str, outbox_id: str) -> dict:
    for m in state.get("outbox") or []:
        if m.get("id") == outbox_id and m.get("kind") == "clarification" and m.get("vendor_id") == vendor_id:
            return m
    raise HTTPException(404, "clarification draft not found")


def _draft_view(item: dict) -> dict:
    delivery = item.get("delivery") or "draft"
    sent = bool(item.get("sent_at")) and delivery != "draft"
    return {
        "subject": item.get("subject") or "",
        "body": item.get("body") or "",
        "points": item.get("points") or [],
        "delivery": delivery,
        "sent": sent,
    }


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/clarify", response_class=HTMLResponse)
def clarify_vendor(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    if not vendor.get("extraction"):
        return error_fragment("Extract this vendor's response first.", 400)
    try:
        draft = clarify.draft_clarification(state, vendor, log=state.setdefault("ai_log", []))
    except llm.AINotConfigured as e:
        return error_fragment(str(e), 400)
    except Exception as e:
        return error_fragment(f"Could not draft the email: {e}")
    outbox_id = None
    if draft["points"]:
        outbox_id = uuid.uuid4().hex[:10]
        item = {
            "id": outbox_id,
            "kind": "clarification",
            "to": vendor.get("email") or "",
            "vendor_id": vendor_id,
            "vendor_name": vendor["name"],
            "subject": draft["subject"],
            "body": draft["body"],
            "points": list(draft.get("points") or []),
            "created_at": now_iso(),
            "sent_at": None,
            "delivery": "draft",
        }
        state.setdefault("outbox", []).append(item)
        vendor["clarification_draft_id"] = outbox_id
        storage.save_state(rfx_id, state)
        draft = _draft_view(item)
    return render(
        request,
        "partials/clarification.html",
        state=state,
        v=vendor,
        draft=draft,
        outbox_id=outbox_id,
    )


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/clarify/save", response_class=HTMLResponse)
def save_clarification(
    request: Request,
    rfx_id: str,
    vendor_id: str,
    outbox_id: str = Form(...),
    subject: str = Form(""),
    body: str = Form(""),
):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    item = _outbox_clarification(state, vendor_id, outbox_id)
    if item.get("sent_at") and item.get("delivery") != "draft":
        return error_fragment("This clarification was already stub-sent.", 400)
    item["subject"] = (subject or "").strip() or item.get("subject") or ""
    item["body"] = body if body is not None else (item.get("body") or "")
    item["updated_at"] = now_iso()
    item["delivery"] = "draft"
    storage.save_state(rfx_id, state)
    return render(
        request,
        "partials/clarification.html",
        state=state,
        v=vendor,
        draft=_draft_view(item),
        outbox_id=outbox_id,
        flash="Saved. Edits are stored on the outbox draft; Send (stub) will use this text.",
    )


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/clarify/send", response_class=HTMLResponse)
def send_clarification(
    request: Request,
    rfx_id: str,
    vendor_id: str,
    outbox_id: str = Form(...),
    subject: str = Form(""),
    body: str = Form(""),
):
    """Stub-send: persist current form text onto the outbox item and mark delivery stubbed."""
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    item = _outbox_clarification(state, vendor_id, outbox_id)
    item["subject"] = (subject or "").strip() or item.get("subject") or ""
    item["body"] = body if body is not None else (item.get("body") or "")
    item["updated_at"] = now_iso()
    item["sent_at"] = now_iso()
    item["delivery"] = "stubbed (no SMTP)"
    storage.save_state(rfx_id, state)
    return render(
        request,
        "partials/clarification.html",
        state=state,
        v=vendor,
        draft=_draft_view(item),
        outbox_id=outbox_id,
        flash="Stub-sent. Outbox now holds the edited subject and body (no real SMTP).",
    )


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/remove", response_class=HTMLResponse)
def remove_vendor(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    storage.delete_urls([f["url"] for f in vendor["files"]])
    state["vendors"] = [v for v in state["vendors"] if v["vendor_id"] != vendor_id]
    snapshots.bump_vendor_data_version(state, "vendor_deleted", affected_vendor_ids=[vendor_id])
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/email")
