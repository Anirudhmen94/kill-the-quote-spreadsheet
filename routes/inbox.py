"""Inbox: vendor replies arrive (simulated or uploaded), get read, get extracted."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from core import clarify, extractor, ingest, llm, snapshots, storage, vendor_sim
from core.web import error_fragment, hx_redirect, load_or_404, now_iso, render

router = APIRouter()


def _vendor(state: dict, vendor_id: str) -> dict:
    for v in state["vendors"]:
        if v["vendor_id"] == vendor_id:
            return v
    raise HTTPException(404, "vendor not found")


@router.get("/rfx/{rfx_id}/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, rfx_id: str):
    state = load_or_404(rfx_id)
    return render(request, "inbox.html", state=state, active="inbox")


@router.post("/rfx/{rfx_id}/simulate", response_class=HTMLResponse)
def simulate(request: Request, rfx_id: str):
    state = load_or_404(rfx_id)
    try:
        vendor_sim.simulate_replies(state)
        vids = [v["vendor_id"] for v in state.get("vendors", []) if v.get("files")]
        snapshots.bump_vendor_data_version(state, "vendor_simulated", affected_vendor_ids=vids)
        storage.save_state(rfx_id, state)
    except Exception as e:
        return error_fragment(f"Could not generate vendor replies: {e}")
    return hx_redirect(f"/rfx/{rfx_id}/inbox")


@router.post("/rfx/{rfx_id}/upload", response_class=HTMLResponse)
async def upload(request: Request, rfx_id: str, vendor_id: str = Form(""), new_vendor_name: str = Form(""), files: list[UploadFile] = File(...)):
    state = load_or_404(rfx_id)
    if not files or all(not f.filename for f in files):
        return error_fragment("Choose at least one file.", 400)
    if vendor_id:
        vendor = _vendor(state, vendor_id)
    else:
        name = new_vendor_name.strip() or f"Vendor {len(state['vendors']) + 1}"
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
            return error_fragment(f"Unsupported file type: {f.filename}. Use xlsx, pdf, docx, jpg/png, eml/txt or csv.", 400)
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
    return hx_redirect(f"/rfx/{rfx_id}/inbox")


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/extract", response_class=HTMLResponse)
def extract(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    if not vendor["files"]:
        vendor["error"] = "No files to read."
        return render(request, "partials/vendor_card.html", state=state, v=vendor)
    log = state.setdefault("ai_log", [])
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
        log = state.setdefault("ai_log", [])
        vendor["status"] = "extracting"
        vendor["error"] = None
        vendor["texts"] = {}
        new_ext = extractor.extract_vendor(state, vendor, storage.get_bytes, log=log)
        # Optimistic lock: if another job advanced the version past our base with a
        # newer extraction of THIS vendor, do not clobber — but a bump from a
        # *different* vendor completing is expected and we still apply our result
        # then bump again.
        current = snapshots.current_version(state)
        vendor["extraction"] = new_ext
        vendor["status"] = "extracted"
        vendor["extracted_at"] = now_iso()
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
        # Ignore unused current for now (kept for future race diagnostics)
        _ = current
    except llm.AINotConfigured as e:
        vendor["status"] = "received" if not was_extracted else "extracted"
        if was_extracted:
            vendor["extraction"] = prior_extraction
            vendor["texts"] = prior_texts or vendor.get("texts") or {}
        vendor["error"] = str(e)
    except Exception as e:
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
    if all(v.get("status") == "extracted" for v in state["vendors"] if v.get("files")):
        state["status"] = "compared"
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
    if draft["points"]:
        state["outbox"].append({"kind": "clarification", "to": vendor.get("email") or "", "vendor_id": vendor_id, "vendor_name": vendor["name"], "subject": draft["subject"], "body": draft["body"], "sent_at": now_iso(), "delivery": "stubbed (no SMTP)"})
        storage.save_state(rfx_id, state)
    return render(request, "partials/clarification.html", state=state, v=vendor, draft=draft)


@router.post("/rfx/{rfx_id}/vendor/{vendor_id}/remove", response_class=HTMLResponse)
def remove_vendor(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    storage.delete_urls([f["url"] for f in vendor["files"]])
    state["vendors"] = [v for v in state["vendors"] if v["vendor_id"] != vendor_id]
    snapshots.bump_vendor_data_version(state, "vendor_deleted", affected_vendor_ids=[vendor_id])
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/inbox")
