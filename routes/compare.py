"""Comparison grid, anomalies panel, evidence drawer, award page and exports."""
from __future__ import annotations

import io
import re

import pymupdf
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, RedirectResponse

from core import award_actions, awardability, demo_ops, engine, exceptions as exc_mod, export, freeze, ingest, snapshots, storage
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
    """Full multi-vendor comparison matrix. Override / Deny / approval live on Anomalies."""
    state = load_or_404(rfx_id)
    cmp = awardability.enrich_state_comparison(state) if any(v.get("extraction") for v in state["vendors"]) else engine.build_comparison(state)
    counts = snapshots.processing_counts(state)
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
    from core import charts, compare_ask

    ready = any(v.get("extraction") for v in state["vendors"])
    gates = cmp.get("gates") or {}
    # All extracted vendors in the matrix (not Pass-only); flagged work stays on Anomalies.
    matrix_vendors = list(cmp["vendors"])
    open_anomaly_count = exc_mod.counts(state).get("open", 0)
    chart_bundle = charts.build_chart_bundle(state) if ready else {"available": False}
    if chart_bundle.get("available"):
        storage.save_state(rfx_id, state)  # persist award_scenario snapshot from live calc
    audit_strip = charts.audit_trust_strip(state)
    return render(
        request,
        "compare.html",
        state=state,
        cmp=cmp,
        counts=counts,
        exclusion_summary=cmp.get("exclusion_summary"),
        gates=gates,
        active="compare",
        compare_premades=compare_ask.COMPARE_PREMADES,
        ready=ready,
        matrix_vendors=matrix_vendors,
        open_anomaly_count=open_anomaly_count,
        charts=chart_bundle,
        audit_strip=audit_strip,
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
    # Cell-level anomaly (not gate/coverage) for resolution actions in the drawer
    exception_item = next(
        (
            i
            for i in exc_mod.list_exceptions(state)
            if i.get("vendor_id") == vendor_id
            and i.get("line_no") == line_no
            and not str(i.get("kind") or "").startswith("gate")
            and i.get("kind") != "coverage_gap"
        ),
        None,
    )
    return render(
        request,
        "partials/evidence_drawer.html",
        state=state,
        v=vendor,
        line=line,
        cell=cell,
        ev=ev,
        file=file,
        ctx=ctx,
        others=others,
        fx=state["fx"],
        exception_item=exception_item,
    )


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


@router.get("/rfx/{rfx_id}/vendor/{vendor_id}/questionnaire", response_class=HTMLResponse)
def questionnaire(request: Request, rfx_id: str, vendor_id: str):
    state = load_or_404(rfx_id)
    vendor = _vendor(state, vendor_id)
    cmp = engine.build_comparison(state)
    vrow = next(v for v in cmp["vendors"] if v["vendor_id"] == vendor_id)
    return render(request, "partials/vendor_drawer.html", state=state, v=vendor, vrow=vrow)


@router.get("/rfx/{rfx_id}/award", response_class=HTMLResponse)
def award_page(request: Request, rfx_id: str):
    from core import award_packs, scenario

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
    gates = live.get("gates") or cmp.get("gates")
    conditional = live.get("conditional_discounts") if live.get("available") else None
    enriched_rows = award_packs.enrich_split_rows(split, cmp, gates) if split else []
    if split is not None:
        # Keep gap_display on the live split object for the optional auditor table
        split = {**split, "rows": enriched_rows}
    vendor_packs = award_packs.vendor_award_packs(
        split,
        cmp=cmp,
        gates=gates,
        total_extended_inr=(live.get("total_extended_inr") if live.get("available") else None),
    )
    uncovered = award_packs.uncovered_line_rows(split)
    notice_preview = award_packs.notice_previews(
        state, freeze_pack=pack, live=live if live.get("available") else None, vendor_packs=vendor_packs
    )
    unconfirmed = award_packs.unconfirmed_discounts(conditional)
    blocking = exc_mod.has_blocking_exceptions(state)
    freeze_checklist = scenario.freeze_ux_checklist(
        state,
        live=live if live.get("available") else None,
        life=life,
        has_blocking_exceptions=blocking,
        freeze_check_complete=freeze_check_complete,
    )
    draft_summary = ""
    if live.get("available") and freeze_checklist.get("needs_save"):
        draft_summary = snapshots.build_live_recommendation_summary(live, vendor_packs=vendor_packs)

    from core import award_ask

    lock_rationale_prefill = ""
    if live.get("available") and not life.get("can_freeze"):
        lock_rationale_prefill = award_ask.default_lock_rationale(state, live)

    from core import charts, event_status

    chart_bundle = charts.build_chart_bundle(state, live=live if live.get("available") else None)
    audit_strip = charts.audit_trust_strip(state)
    lock_cta = event_status.derive_lock_cta(
        state,
        live=live if live.get("available") else None,
        life=life,
        freeze_check_complete=freeze_check_complete,
        has_blocking_exceptions=blocking,
    )
    freeze_validity = event_status.freeze_validity(pack) if pack else None

    return render(
        request,
        "award.html",
        state=state,
        cmp=cmp,
        split=split,
        live=live,
        current_rec=current_rec,
        historical_recs=historical,
        gates=gates,
        freeze_pack=pack,
        freeze_validity=freeze_validity,
        active="award",
        has_blocking_exceptions=blocking,
        flash=flash,
        recommendation_lifecycle=life,
        market_quote_coverage=cmp.get("market_quote_coverage"),
        scenario_award_coverage=(live.get("scenario") or {}).get("scenario_award_coverage")
        if live.get("available")
        else (cmp.get("scenario_award_coverage") if isinstance(cmp, dict) else None),
        freeze_check_complete=freeze_check_complete,
        discount_confirmations=state.get("discount_confirmations") or {},
        conditional_discounts=conditional,
        vendor_packs=vendor_packs,
        uncovered_lines=uncovered,
        notice_preview=notice_preview,
        unconfirmed_discounts=unconfirmed,
        freeze_checklist=freeze_checklist,
        freeze_next_step=freeze_checklist.get("human_blocked_reason"),
        draft_recommendation_summary=draft_summary,
        award_premades=award_ask.PREMADES,
        lock_rationale_prefill=lock_rationale_prefill,
        charts=chart_bundle,
        audit_strip=audit_strip,
        lock_cta=lock_cta,
    )




@router.post("/rfx/{rfx_id}/award/ask-premade", response_class=HTMLResponse)
async def award_ask_premade(request: Request, rfx_id: str, prompt_id: str = Form("")):
    """Premade Award Ask — engine-first, version-bound cache (no Claude on click)."""
    from core import award_ask

    state = load_or_404(rfx_id)
    form = await request.form()
    pid = (prompt_id or form.get("prompt_id") or "").strip()
    if not pid:
        return error_fragment("Unknown premade prompt.", 400)
    if not any(v.get("extraction") for v in state["vendors"]):
        return error_fragment("Extract at least one vendor response before asking.", 400)
    try:
        result = award_ask.get_or_build_premade(state, pid)
    except ValueError as e:
        return error_fragment(str(e), 400)
    state.setdefault("chat", []).append(result)
    storage.save_state(rfx_id, state)
    idx = len(state["chat"]) - 1
    return render(request, "partials/award_ask_answer.html", state=state, m=result, idx=idx)


@router.post("/rfx/{rfx_id}/award/ask", response_class=HTMLResponse)
def award_ask_live(request: Request, rfx_id: str, question: str = Form(...)):
    """Free-ask on Award — live Claude via analyst.ask."""
    from routes import ask as ask_routes
    from core import llm

    state = load_or_404(rfx_id)
    if not any(v.get("extraction") for v in state["vendors"]):
        return error_fragment("Extract at least one vendor response before asking.", 400)
    q = (question or "").strip()
    if not q:
        return error_fragment("Type a question.", 400)
    ok, msg = ask_routes._acquire_ask(rfx_id)
    if not ok:
        return error_fragment(msg, 409)
    try:
        try:
            result = ask_routes._run_ask(state, q)
        except llm.AINotConfigured as e:
            return error_fragment(str(e), 400)
        except RuntimeError as e:
            return error_fragment(f"Data changed while answering; please ask again. ({e})", 409)
        except Exception as e:
            failed = {
                "question": q,
                "answer": f"**The analyst failed.** {e}",
                "error": str(e),
                "status": "failed",
                "trace": [],
                "tables": [],
                "charts": [],
                "caveats": [],
                "at": now_iso(),
                "award_shaped": True,
            }
            state.setdefault("chat", []).append(failed)
            storage.save_state(rfx_id, state)
            return render(
                request,
                "partials/award_ask_answer.html",
                state=state,
                m=failed,
                idx=len(state["chat"]) - 1,
            )
        result["award_shaped"] = True
        state.setdefault("chat", []).append(result)
        storage.save_state(rfx_id, state)
        return render(
            request,
            "partials/award_ask_answer.html",
            state=state,
            m=result,
            idx=len(state["chat"]) - 1,
        )
    finally:
        ask_routes._release_ask(rfx_id)



@router.post("/rfx/{rfx_id}/award/use-and-lock/{idx}", response_class=HTMLResponse)
async def award_use_and_lock(request: Request, rfx_id: str, idx: int):
    """Path A: save Ask suggestion as recommendation, then lock_award."""
    from core import award_ask

    state = load_or_404(rfx_id)
    award_url = f"/rfx/{rfx_id}/award"

    def _after():
        if request.headers.get("HX-Request"):
            return hx_redirect(award_url)
        return RedirectResponse(award_url, status_code=303)

    chat = state.get("chat", [])
    if idx < 0 or idx >= len(chat):
        award_actions.push_flash(state, "Answer not found.", level="error")
        storage.save_state(rfx_id, state)
        return _after()
    m = chat[idx]
    ok, msg = snapshots.can_save_recommendation(state, m)
    if not ok:
        award_actions.push_flash(state, msg, level="error")
        storage.save_state(rfx_id, state)
        return _after()
    try:
        snapshots.save_recommendation_from_answer(state, m, idx)
        out = award_ask.lock_award(state, rationale=None)
    except (ValueError, freeze.FreezeValidationError) as e:
        award_actions.push_flash(state, _freeze_error_flash(str(e)), level="error")
        storage.save_state(rfx_id, state)
        return _after()

    pack = out["pack"]
    mode_label = out.get("mode") or pack.get("freeze_mode") or "complete"
    award_actions.push_flash(
        state,
        f"Recommendation saved and award locked successfully ({mode_label}). "
        f"Snapshot {pack.get('calculation_snapshot_id')}. Next: send award & regret emails below.",
        level="success",
        cta_href=f"{award_url}#lock-send",
        cta_label="Continue: send emails",
    )
    storage.save_state(rfx_id, state)
    return _after()


@router.post("/rfx/{rfx_id}/award/lock", response_class=HTMLResponse)
async def award_lock(request: Request, rfx_id: str, rationale: str = Form("")):
    """One-click lock: optional save-rec → complete freeze only (no auto-partial)."""
    from core import award_ask

    state = load_or_404(rfx_id)
    award_url = f"/rfx/{rfx_id}/award#lock-send"

    def _after():
        if request.headers.get("HX-Request"):
            return hx_redirect(f"/rfx/{rfx_id}/award")
        return RedirectResponse(award_url, status_code=303)

    try:
        out = award_ask.lock_award(state, rationale=rationale or None)
    except (ValueError, freeze.FreezeValidationError) as e:
        award_actions.push_flash(state, _freeze_error_flash(str(e)), level="error")
        storage.save_state(rfx_id, state)
        return _after()

    pack = out["pack"]
    mode_label = out.get("mode") or pack.get("freeze_mode") or "complete"
    extra = " Recommendation saved." if out.get("saved_recommendation") else ""
    award_actions.push_flash(
        state,
        f"Award locked successfully ({mode_label}). Snapshot {pack.get('calculation_snapshot_id')}.{extra} "
        "Next: send award & regret emails below.",
        level="success",
        cta_href=award_url,
        cta_label="Continue: send emails",
    )
    storage.save_state(rfx_id, state)
    return _after()


@router.post("/rfx/{rfx_id}/award/send-notices", response_class=HTMLResponse)
def send_award_notices(request: Request, rfx_id: str, vendor_id: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        award_actions.send_award_notices(state, vendor_id=vendor_id or None)
    except ValueError as e:
        award_actions.push_flash(state, str(e), level="error")
        storage.save_state(rfx_id, state)
        return RedirectResponse(f"/rfx/{rfx_id}/award#award-step-3", status_code=303)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/award", status_code=303)


@router.post("/rfx/{rfx_id}/award/export-notify")
def export_award_notify(rfx_id: str):
    """Record stakeholder notification then redirect to the xlsx download."""
    state = load_or_404(rfx_id)
    award_actions.record_export_notification(state)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/export.xlsx?notified=1", status_code=303)

@router.get("/rfx/{rfx_id}/audit.csv")
def export_audit_csv(rfx_id: str):
    """Small audit trail CSV (reviews, freeze, notices) — not a new workbook format."""
    state = load_or_404(rfx_id)
    data = export.audit_trail_csv(state)
    return Response(
        data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-{rfx_id}.csv"'},
    )


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


@router.post("/rfx/{rfx_id}/award/save-recommendation", response_class=HTMLResponse)
async def save_award_recommendation_route(
    request: Request,
    rfx_id: str,
    rationale: str = Form(""),
    summary: str = Form(""),
):
    """Save live calculation as current recommendation (Award UX path; same persistence as Ask)."""
    state = load_or_404(rfx_id)
    try:
        snapshots.save_recommendation_from_live(
            state,
            rationale,
            summary=summary or None,
        )
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/award#award-step-3", status_code=303)


def _freeze_error_flash(message: str) -> str:
    """Append actionable next steps when complete/partial freeze is rejected."""
    msg = (message or "").strip()
    lower = msg.lower()
    if "assumed" in lower:
        hint = (
            " Next: under Advanced… tick “Confirm assumed cells into freeze”, "
            "or use Freeze partial… with acknowledgements and a written reason."
        )
    elif "uncovered" in lower or "full allocation" in lower or "coverage" in lower:
        hint = (
            " Next: resolve coverage on Compare, or use Advanced… → Freeze partial… "
            "(acknowledge coverage gaps + selected blockers, and write a reason)."
        )
    elif "blocker" in lower:
        hint = (
            " Next: resolve selected-award blockers on Compare, or use Advanced… → "
            "Freeze partial… with acknowledgements and a written reason."
        )
    elif "acknowledgement" in lower or "partial freeze requires" in lower:
        hint = (
            " Next: open Advanced…, tick the required acknowledgements, "
            "and enter a partial reason before Freeze partial…"
        )
    elif "recommendation" in lower:
        hint = " Next: write a short rationale and save this calculation as your recommendation."
    elif "manual freeze" in lower or "use & lock" in lower or "lock / use" in lower:
        hint = (
            " Next: open Freeze partial… under Manual lock, tick coverage gaps "
            "(and selected blockers if listed), and enter a non-empty partial reason."
        )
    elif "extraction" in lower:
        hint = (
            " Next: finish reading vendor replies on Email, exclude failed vendors with a reason, "
            "or use Freeze partial… with an extraction_incomplete acknowledgement."
        )
    else:
        hint = (
            " Next: use Freeze partial… (acknowledgements + reason), "
            "confirm assumed cells if needed, or resolve issues on Compare."
        )
    return msg + hint


@router.post("/rfx/{rfx_id}/award/replacement-recommendation", response_class=HTMLResponse)
async def replacement_recommendation_route(
    request: Request,
    rfx_id: str,
    rationale: str = Form(""),
):
    """Preserve invalid historical freeze; save a new recommendation without auto-freezing."""
    from core import event_status, vendor_extraction as vex

    state = load_or_404(rfx_id)
    pack = event_status.latest_relevant_freeze(state)
    validity = event_status.freeze_validity(pack)
    if validity not in (
        event_status.VALIDITY_REQUIRES_REVIEW,
        event_status.VALIDITY_INVALID_HISTORICAL,
    ):
        award_actions.push_flash(
            state,
            "Replacement recommendation is only for invalid historical freezes that require review.",
            level="error",
        )
        storage.save_state(rfx_id, state)
        return RedirectResponse(f"/rfx/{rfx_id}/award#lock-send", status_code=303)

    failed = [
        v
        for v in state.get("vendors") or []
        if vex.get_status(v) == vex.STATUS_FAILED_NO_PREVIOUS
    ]
    if failed:
        names = ", ".join(v.get("name") or "?" for v in failed)
        award_actions.push_flash(
            state,
            f"Resolve failed vendor response(s) first (retry or exclude): {names}.",
            level="error",
        )
        storage.save_state(rfx_id, state)
        return RedirectResponse(f"/rfx/{rfx_id}/email", status_code=303)

    text_r = (rationale or "").strip()
    if len(text_r) < 8:
        award_actions.push_flash(
            state,
            "Replacement recommendation requires a written rationale (preserve historical freeze; do not auto-freeze).",
            level="error",
        )
        storage.save_state(rfx_id, state)
        return RedirectResponse(f"/rfx/{rfx_id}/award#replacement-rec", status_code=303)

    try:
        snapshots.save_recommendation_from_live(
            state,
            text_r
            + (
                f"\n\n_Replacement after invalid historical freeze "
                f"`{(pack or {}).get('id')}` — historical pack preserved; not auto-frozen._"
            ),
        )
    except ValueError as e:
        award_actions.push_flash(state, str(e), level="error")
        storage.save_state(rfx_id, state)
        return RedirectResponse(f"/rfx/{rfx_id}/award#replacement-rec", status_code=303)

    state.setdefault("buyer_review_log", []).append(
        {
            "action": "replacement_recommendation",
            "freeze_id": (pack or {}).get("id"),
            "detail": "Saved replacement recommendation; historical invalid freeze preserved; no auto-freeze.",
            "at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(timespec="seconds"),
            "actor": "buyer",
        }
    )
    award_actions.push_flash(
        state,
        "Replacement recommendation saved. Historical freeze preserved for audit. "
        "Notices remain disabled until you create a new valid freeze.",
        cta_href=f"/rfx/{rfx_id}/award#lock-send",
        cta_label="Continue on Award",
    )
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/award#lock-send", status_code=303)



@router.post("/rfx/{rfx_id}/award/freeze", response_class=HTMLResponse)
async def freeze_award_route(
    request: Request,
    rfx_id: str,
    confirm_assumed: bool = Form(False),
    freeze_mode: str = Form("complete"),
    partial_reason: str = Form(""),
):
    state = load_or_404(rfx_id)
    award_url = f"/rfx/{rfx_id}/award#award-step-3"

    def _after_post(*, hx_ok: bool = True):
        """Normal form POSTs need HTTP 303; HTMX clients use HX-Redirect."""
        if request.headers.get("HX-Request") and hx_ok:
            return hx_redirect(f"/rfx/{rfx_id}/award")
        return RedirectResponse(award_url, status_code=303)

    if not any(v.get("extraction") for v in state["vendors"]):
        award_actions.push_flash(
            state,
            "Extract vendor responses before freezing an award.",
            level="error",
        )
        storage.save_state(rfx_id, state)
        return _after_post()
    form = await request.form()
    acks = form.getlist("acknowledgement") if hasattr(form, "getlist") else []
    mode = freeze_mode or "complete"
    try:
        pack = freeze.freeze_award(
            state,
            confirm_assumed=confirm_assumed,
            require_quality_gate=True,
            mode=mode,
            acknowledgements=list(acks),
            partial_reason=partial_reason or "",
        )
    except (ValueError, freeze.FreezeValidationError) as e:
        award_actions.push_flash(state, _freeze_error_flash(str(e)), level="error")
        storage.save_state(rfx_id, state)
        return _after_post()
    mode_label = pack.get("freeze_mode") or mode
    award_actions.push_flash(
        state,
        f"Award frozen successfully ({mode_label}). Snapshot {pack.get('calculation_snapshot_id')}. "
        "Next: send award & regret notices below.",
        level="success",
        cta_href=award_url,
        cta_label="Continue: send notices",
    )
    storage.save_state(rfx_id, state)
    return _after_post()


@router.post("/rfx/{rfx_id}/award/confirm-discount", response_class=HTMLResponse)
def confirm_discount_route(
    request: Request,
    rfx_id: str,
    vendor_id: str = Form(...),
    confirmed_by: str = Form("buyer"),
):
    """Buyer confirms a conditional discount into the official total (Phase A.4)."""
    from core import scenario as _scenario

    state = load_or_404(rfx_id)
    cmp = awardability.enrich_state_comparison(state) if any(v.get("extraction") for v in state["vendors"]) else engine.build_comparison(state)
    vendor = next((v for v in cmp.get("vendors", []) if v["vendor_id"] == vendor_id), None)
    if not vendor:
        return error_fragment("Vendor not found.", 404)
    commercial = vendor.get("commercial") or {}
    pct = commercial.get("discount_pct")
    if not pct:
        return error_fragment("This vendor has no conditional discount to confirm.", 400)
    condition = commercial.get("discount_condition") or f"{pct:g}% conditional discount"
    state.setdefault("discount_confirmations", {})
    state["discount_confirmations"][vendor_id] = {
        "confirmed": True,
        "confirmed_at": now_iso(),
        "confirmed_by": (confirmed_by or "buyer").strip() or "buyer",
        "condition": condition,
        "pct": float(pct),
        "vendor_name": vendor.get("name"),
    }
    _scenario.append_buyer_review_log(
        state,
        {
            "source": "award",
            "vendor_id": vendor_id,
            "vendor_name": vendor.get("name"),
            "line_no": None,
            "action": "confirm_discount",
            "value_inr": None,
            "note": f"Confirmed {pct:g}% conditional discount — {condition}",
        },
    )
    snapshots.bump_vendor_data_version(state, "discount_confirmed", affected_vendor_ids=[vendor_id])
    # Recalc + new snapshot so Ask/Award share the confirmed official total
    live = snapshots.live_award_calculation(state)
    storage.save_state(rfx_id, state)
    if request.headers.get("HX-Request"):
        return hx_redirect(f"/rfx/{rfx_id}/award")
    return RedirectResponse(f"/rfx/{rfx_id}/award", status_code=303)


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
