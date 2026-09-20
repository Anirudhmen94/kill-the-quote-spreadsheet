"""Comparison grid, evidence drawer, reviews, award page and exports."""
from __future__ import annotations

import io
import re

import pymupdf
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse

from core import award_actions, awardability, demo_ops, edge_callouts, engine, exceptions as exc_mod, export, freeze, ingest, snapshots, storage
from core.web import error_fragment, hx_redirect, load_or_404, now_iso, render

router = APIRouter()


def _vendor(state: dict, vendor_id: str) -> dict:
    for v in state["vendors"]:
        if v["vendor_id"] == vendor_id:
            return v
    raise HTTPException(404, "vendor not found")


def _context_lines(text: str, snippet: str, radius: int = 3) -> list[dict]:
    """Anchored text lines around the one containing the snippet."""
    if not text:
        return []
    lines = text.splitlines()
    target = ingest.normalize_ws(snippet)
    hit = None
    for i, ln in enumerate(lines):
        if target and target in ingest.normalize_ws(ln):
            hit = i
            break
    if hit is None and target:
        # fall back to the first numeric token of the snippet
        nums = re.findall(r"\d[\d,\.]*", snippet)
        for i, ln in enumerate(lines):
            if nums and nums[0] in ln:
                hit = i
                break
    if hit is None:
        return []
    return [{"text": ln, "hit": i == hit} for i, ln in enumerate(lines) if hit - radius <= i <= hit + radius]


@router.get("/rfx/{rfx_id}/compare", response_class=HTMLResponse)
def compare_page(request: Request, rfx_id: str):
    state = load_or_404(rfx_id)
    cmp = awardability.enrich_state_comparison(state) if any(v.get("extraction") for v in state["vendors"]) else engine.build_comparison(state)
    counts = snapshots.processing_counts(state)
    callouts = edge_callouts.edge_callouts(cmp) if cmp.get("vendors") else []
    if any(v.get("extraction") for v in state["vendors"]):
        snapshots.create_calculation_snapshot(
            state,
            "comparison",
            parameters={"provisional": bool(counts["processing"])},
            result={
                "vendor_count": len(cmp["vendors"]),
                "line_count": cmp["line_count"],
                "exclusion_summary": cmp.get("exclusion_summary"),
                "gates_summary": (cmp.get("gates") or {}).get("summary"),
            },
        )
        storage.save_state(rfx_id, state)
    from routes.ask import SUGGESTED

    ready = any(v.get("extraction") for v in state["vendors"])
    prompts = demo_ops.DEMO_PROMPTS if demo_ops.is_demo_mode(state) else SUGGESTED
    return render(
        request,
        "compare.html",
        state=state,
        cmp=cmp,
        counts=counts,
        callouts=callouts,
        exclusion_summary=cmp.get("exclusion_summary"),
        gates=cmp.get("gates"),
        active="compare",
        suggested=prompts,
        ready=ready,
    )


@router.get("/rfx/{rfx_id}/evidence/{vendor_id}/{line_no}", response_class=HTMLResponse)
def evidence(request: Request, rfx_id: str, vendor_id: str, line_no: int):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    cmp = engine.build_comparison(state)
    line = next((ln for ln in cmp["lines"] if ln["line_no"] == line_no), None)
    if not line:
        raise HTTPException(404)
    cell = line["cells"][vendor_id]
    ev = cell.get("evidence") or {}
    file = None
    if ev:
        file = next((f for f in vendor["files"] if f["file_id"] == ev.get("file_id")), None) or next((f for f in vendor["files"] if f["name"] == ev.get("file_name")), None)
    text = vendor.get("texts", {}).get(file["file_id"], {}).get("text", "") if file else ""
    ctx = _context_lines(text, ev.get("snippet", "")) if ev else []
    # other vendor rows the model mapped to this line (duplicates / alternates)
    others = [q for q in (vendor.get("extraction") or {}).get("line_quotes", []) if q.get("line_no") == line_no]
    return render(request, "partials/evidence_drawer.html", state=state, v=vendor, line=line, cell=cell, ev=ev, file=file, ctx=ctx, others=others, fx=state["fx"])


@router.get("/rfx/{rfx_id}/evidence-image/{vendor_id}/{file_id}")
def evidence_image(rfx_id: str, vendor_id: str, file_id: str, q: str = "", page: int = 0):
    """Render the PDF page containing the snippet with the match highlighted."""
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    f = next((f for f in vendor["files"] if f["file_id"] == file_id), None)
    if not f or f["kind"] != "pdf":
        raise HTTPException(404)
    data = storage.get_bytes(f["url"])
    if not data:
        raise HTTPException(404)
    doc = pymupdf.open(stream=data, filetype="pdf")
    needles = [q.strip()]
    if len(q) > 40:
        needles += [q[:40].strip(), q[-40:].strip()]
    nums = re.findall(r"\d[\d,\.]*\d", q)
    needles += nums[:2]
    target_page, rects = None, []
    for pg in doc:
        for n in needles:
            if not n:
                continue
            r = pg.search_for(n)
            if r:
                target_page, rects = pg, r
                break
        if target_page is not None:
            break
    if target_page is None:
        target_page = doc[min(page, len(doc) - 1)]
    for r in rects:
        target_page.draw_rect(r + (-2, -2, 2, 2), color=(1, 0.6, 0), width=1.5)
        target_page.draw_rect(r + (-2, -2, 2, 2), color=None, fill=(1, 0.85, 0.2), fill_opacity=0.35)
    pix = target_page.get_pixmap(matrix=pymupdf.Matrix(1.7, 1.7))
    png = pix.tobytes("png")
    doc.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/rfx/{rfx_id}/review/{vendor_id}/{line_no}", response_class=HTMLResponse)
def review(request: Request, rfx_id: str, vendor_id: str, line_no: int, action: str = Form(...), value_inr: str = Form(""), note: str = Form("")):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    if action not in ("accept", "override", "clear"):
        return error_fragment("Unknown action", 400)
    state["reviews"] = [r for r in state.get("reviews", []) if not (r["vendor_id"] == vendor_id and r["line_no"] == line_no)]
    if action != "clear":
        val = None
        if action == "accept":
            cmp = engine.build_comparison({**state, "reviews": []})
            cell = next(ln for ln in cmp["lines"] if ln["line_no"] == line_no)["cells"].get(vendor_id, {})
            if cell.get("unit_inr") is None:
                return error_fragment("This cell has no per-piece price to accept. Use 'Override' with the confirmed INR value, or leave it excluded.", 400)
        if action == "override":
            try:
                val = float(value_inr.replace(",", ""))
            except ValueError:
                return error_fragment("Enter a numeric INR per-piece value to override.", 400)
        if not note.strip():
            return error_fragment("A note is required so the audit log explains the decision.", 400)
        state["reviews"].append({"vendor_id": vendor_id, "vendor_name": vendor["name"], "line_no": line_no, "action": action, "value_inr": val, "note": note.strip(), "at": now_iso()})
        from core import scenario as _scenario
        _scenario.append_buyer_review_log(state, {"source": "evidence", "vendor_id": vendor_id, "vendor_name": vendor["name"], "line_no": line_no, "action": action, "value_inr": val, "note": note.strip()})
        reason = "review_accepted" if action == "accept" else "review_overridden"
        snapshots.bump_vendor_data_version(state, reason, affected_vendor_ids=[vendor_id])
    else:
        snapshots.bump_vendor_data_version(state, "review_cleared", affected_vendor_ids=[vendor_id])
    storage.save_state(rfx_id, state)
    resp = HTMLResponse("")
    resp.headers["HX-Refresh"] = "true"
    return resp


@router.get("/rfx/{rfx_id}/vendor/{vendor_id}/questionnaire", response_class=HTMLResponse)
def questionnaire(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    cmp = engine.build_comparison(state)
    vrow = next(v for v in cmp["vendors"] if v["vendor_id"] == vendor_id)
    return render(request, "partials/vendor_drawer.html", state=state, v=vendor, vrow=vrow)


@router.get("/rfx/{rfx_id}/award", response_class=HTMLResponse)
def award_page(request: Request, rfx_id: str):
    from core import scenario

    state = load_or_404(rfx_id)
    live = snapshots.live_award_calculation(state) if any(v.get("extraction") for v in state["vendors"]) else {"available": False}
    if live.get("available"):
        storage.save_state(rfx_id, state)
    cmp = live.get("cmp") or engine.build_comparison(state)
    split = live.get("split")
    current_rec = next((r for r in state.get("recommendations", []) if r.get("status") == "current"), None)
    historical = [r for r in state.get("recommendations", []) if r.get("status") in ("stale", "superseded") or r.get("legacy")]
    pack = freeze.current_freeze(state)
    flash = award_actions.pop_flash(state)
    if flash is not None:
        # flash consumed — persist cleared flash
        storage.save_state(rfx_id, state)

    life = scenario.recommendation_lifecycle(state)
    freeze_check_complete = freeze.validate_freeze_request(state, mode="complete")
    if split and split.get("rows"):
        for r in split["rows"]:
            if not r.get("runner_up"):
                r["runner_up"] = "—"
                r["gap_pct"] = None
            elif r.get("gap_pct") is None:
                r["gap_display"] = "—"
            else:
                r["gap_display"] = f"{r['gap_pct']:g}%"

    return render(
        request,
        "award.html",
        state=state,
        cmp=cmp,
        split=split,
        live=live,
        current_rec=current_rec,
        historical_recs=historical,
        blockers=(live.get("blockers") or (awardability.blockers_panel(cmp) if cmp.get("lines") else {"total": 0, "by_kind": {}, "items": []})),
        exclusion_summary=live.get("exclusion_summary") or cmp.get("exclusion_summary"),
        gates=live.get("gates") or cmp.get("gates"),
        freeze_pack=pack,
        callouts=edge_callouts.edge_callouts(cmp) if cmp.get("vendors") else [],
        active="award",
        has_blocking_exceptions=exc_mod.has_blocking_exceptions(state),
        flash=flash,
        recommendation_lifecycle=life,
        market_quote_coverage=cmp.get("market_quote_coverage"),
        freeze_check_complete=freeze_check_complete,
        discount_confirmations=state.get("discount_confirmations") or {},
        buyer_review_log=state.get("buyer_review_log") or state.get("reviews") or [],
        blended_rate_banner=cmp.get("blended_rate_banner"),
    )



@router.post("/rfx/{rfx_id}/award/send-notices", response_class=HTMLResponse)
def send_award_notices(request: Request, rfx_id: str, vendor_id: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        award_actions.send_award_notices(state, vendor_id=vendor_id or None)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/award", status_code=303)


@router.post("/rfx/{rfx_id}/award/export-notify")
def export_award_notify(rfx_id: str):
    """Record stakeholder notification then redirect to the xlsx download."""
    state = load_or_404(rfx_id)
    award_actions.record_export_notification(state)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/export.xlsx?notified=1", status_code=303)

@router.get("/rfx/{rfx_id}/export.xlsx")
def export_xlsx(rfx_id: str, provisional: bool = False):
    state = load_or_404(rfx_id)
    counts = snapshots.processing_counts(state)
    if counts["processing"] and not provisional:
        return error_fragment(
            "Vendor responses are still processing. Re-request with ?provisional=1 to download a provisional export, "
            "or wait until all responses finish.",
            409,
        )
    data = export.award_workbook(state, provisional=provisional or bool(counts["processing"]))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="award-{rfx_id}.xlsx"'})


@router.get("/rfx/{rfx_id}/export.csv")
def export_csv(rfx_id: str, provisional: bool = False):
    state = load_or_404(rfx_id)
    counts = snapshots.processing_counts(state)
    if counts["processing"] and not provisional:
        return error_fragment(
            "Vendor responses are still processing. Re-request with ?provisional=1 for a provisional CSV.",
            409,
        )
    return Response(
        export.comparison_csv(state, provisional=provisional or bool(counts["processing"])),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="comparison-{rfx_id}.csv"'},
    )


@router.get("/rfx/{rfx_id}/memo.md")
def export_memo(rfx_id: str, provisional: bool = False):
    state = load_or_404(rfx_id)
    counts = snapshots.processing_counts(state)
    if counts["processing"] and not provisional:
        return error_fragment(
            "Vendor responses are still processing. Re-request with ?provisional=1 for a provisional memo.",
            409,
        )
    return Response(
        export.award_memo_md(state, provisional=provisional or bool(counts["processing"])),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="award-memo-{rfx_id}.md"'},
    )


@router.post("/rfx/{rfx_id}/award/freeze", response_class=HTMLResponse)
async def freeze_award_route(
    request: Request,
    rfx_id: str,
    confirm_assumed: bool = Form(False),
    freeze_mode: str = Form("complete"),
    partial_reason: str = Form(""),
):
    state = load_or_404(rfx_id)
    if not any(v.get("extraction") for v in state["vendors"]):
        return error_fragment("Extract vendor responses before freezing an award.", 400)
    form = await request.form()
    acks = form.getlist("acknowledgement") if hasattr(form, "getlist") else []
    try:
        freeze.freeze_award(
            state,
            confirm_assumed=confirm_assumed,
            require_quality_gate=True,
            mode=freeze_mode or "complete",
            acknowledgements=list(acks),
            partial_reason=partial_reason or "",
        )
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/award")


@router.post("/rfx/{rfx_id}/award/confirm-discount", response_class=HTMLResponse)
def confirm_discount_route(request: Request, rfx_id: str, vendor_id: str = Form(...)):
    """Buyer confirms a conditional discount into the official total (Phase A.4)."""
    state = load_or_404(rfx_id)
    state.setdefault("discount_confirmations", {})
    state["discount_confirmations"][vendor_id] = {
        "confirmed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "confirmed": True,
    }
    from core import snapshots
    snapshots.bump_vendor_data_version(state, "discount_confirmed", affected_vendor_ids=[vendor_id])
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/award")


@router.get("/rfx/{rfx_id}/award/freeze.zip")
def download_freeze_zip(rfx_id: str):
    state = load_or_404(rfx_id)
    pack = freeze.current_freeze(state)
    if not pack:
        # allow downloading the latest historical pack for audit
        packs = state.get("freeze_packs") or []
        pack = packs[-1] if packs else None
    if not pack:
        return error_fragment("No frozen award pack yet. Freeze an award first.", 404)
    data = freeze.export_freeze_zip(state, pack)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="freeze-{pack["id"]}.zip"'},
    )
