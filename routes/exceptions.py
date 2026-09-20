"""Anomalies tab + exception resolution actions (Override / Deny / Approve).

GET /anomalies is the primary page. GET /exceptions redirects there.
POST actions redirect back to /anomalies with the matching status filter.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import awardability, charts, edge_callouts, engine, exceptions as exc
from core import snapshots, storage
from core.web import error_fragment, load_or_404, render

router = APIRouter()


def _anomalies_url(rfx_id: str, status: str = "open") -> str:
    st = status if status in ("open", "pending", "resolved", "all") else "open"
    return f"/rfx/{rfx_id}/anomalies?status={st}"


@router.get("/rfx/{rfx_id}/anomalies", response_class=HTMLResponse)
def anomalies_page(request: Request, rfx_id: str, status: str = "open"):
    """Primary Anomalies tab — flagged cells, gates, coverage, edge callouts, actions."""
    state = load_or_404(rfx_id)
    ready = any(v.get("extraction") for v in state["vendors"])
    cmp = (
        awardability.enrich_state_comparison(state)
        if ready
        else engine.build_comparison(state)
    )
    filter_status = status if status in ("open", "pending", "resolved", "all") else "open"
    anomaly_items = exc.list_exceptions(state, None if filter_status == "all" else filter_status)
    exc_counts = exc.counts(state)
    callouts = edge_callouts.edge_callouts(cmp) if ready else []
    # Format/upload + pricing-method callouts belong here (not on clean Compare).
    edge_kinds = {
        "angled_photo",
        "low_confidence",
        "freight",
        "per_kg",
        "per_pack",
        "partial_quote",
        "gate_fail",
        "gate_partial",
        "coverage_gap",
        "unresolved",
        "fx",
    }
    anomaly_callouts = [c for c in callouts if c.get("kind") in edge_kinds]
    audit_strip = charts.audit_trust_strip(state)
    return render(
        request,
        "anomalies.html",
        state=state,
        cmp=cmp,
        active="anomalies",
        ready=ready,
        anomaly_items=anomaly_items,
        exc_counts=exc_counts,
        filter_status=filter_status,
        has_blocking_exceptions=exc.has_blocking_exceptions(state),
        anomaly_callouts=anomaly_callouts,
        audit_strip=audit_strip,
        counts=snapshots.processing_counts(state),
    )


@router.get("/rfx/{rfx_id}/exceptions", response_class=HTMLResponse)
def exceptions_page(request: Request, rfx_id: str, status: str = "open"):
    """Legacy Exceptions URL → Anomalies tab."""
    load_or_404(rfx_id)
    return RedirectResponse(_anomalies_url(rfx_id, status), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/override", response_class=HTMLResponse)
def override(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.override_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_anomalies_url(rfx_id, "resolved"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/request-approval", response_class=HTMLResponse)
def request_approval(
    request: Request,
    rfx_id: str,
    key: str,
    note: str = Form(""),
    manager_name: str = Form(""),
    manager_email: str = Form(""),
):
    state = load_or_404(rfx_id)
    try:
        exc.request_approval(
            state, key, note, manager_name=manager_name, manager_email=manager_email
        )
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_anomalies_url(rfx_id, "pending"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/approve", response_class=HTMLResponse)
def approve(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.approve_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_anomalies_url(rfx_id, "resolved"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/reject", response_class=HTMLResponse)
def reject(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.reject_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_anomalies_url(rfx_id, "open"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/deny", response_class=HTMLResponse)
def deny(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.deny_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_anomalies_url(rfx_id, "resolved"), status_code=303)
