"""Exception resolution actions (Override / Deny / Approve). GET redirects to Compare#anomalies."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import exceptions as exc
from core import storage
from core.web import error_fragment, load_or_404

router = APIRouter()


def _compare_anomalies(rfx_id: str, status: str = "open") -> str:
    st = status if status in ("open", "pending", "resolved", "all") else "open"
    return f"/rfx/{rfx_id}/compare?status={st}#anomalies"


@router.get("/rfx/{rfx_id}/exceptions", response_class=HTMLResponse)
def exceptions_page(request: Request, rfx_id: str, status: str = "open"):
    """Exceptions tab absorbed into Compare — redirect to #anomalies."""
    load_or_404(rfx_id)  # 404 if missing
    return RedirectResponse(_compare_anomalies(rfx_id, status), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/override", response_class=HTMLResponse)
def override(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.override_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_compare_anomalies(rfx_id, "resolved"), status_code=303)


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
    return RedirectResponse(_compare_anomalies(rfx_id, "pending"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/approve", response_class=HTMLResponse)
def approve(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.approve_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_compare_anomalies(rfx_id, "resolved"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/reject", response_class=HTMLResponse)
def reject(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.reject_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_compare_anomalies(rfx_id, "open"), status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/deny", response_class=HTMLResponse)
def deny(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.deny_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(_compare_anomalies(rfx_id, "resolved"), status_code=303)
