"""Exceptions tab: open blockers/gates, override, manager approval."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import exceptions as exc
from core import storage
from core.web import error_fragment, load_or_404, render

router = APIRouter()


@router.get("/rfx/{rfx_id}/exceptions", response_class=HTMLResponse)
def exceptions_page(request: Request, rfx_id: str, status: str = "open"):
    state = load_or_404(rfx_id)
    filter_status = status if status in ("open", "pending", "resolved", "all") else "open"
    items = exc.list_exceptions(state, None if filter_status == "all" else filter_status)
    c = exc.counts(state)
    return render(
        request,
        "exceptions.html",
        state=state,
        active="exceptions",
        items=items,
        counts=c,
        filter_status=filter_status,
        has_blocking=exc.has_blocking_exceptions(state),
    )


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/override", response_class=HTMLResponse)
def override(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.override_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/exceptions?status=resolved", status_code=303)


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
    return RedirectResponse(f"/rfx/{rfx_id}/exceptions?status=pending", status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/approve", response_class=HTMLResponse)
def approve(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.approve_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/exceptions?status=resolved", status_code=303)


@router.post("/rfx/{rfx_id}/exceptions/{key:path}/reject", response_class=HTMLResponse)
def reject(request: Request, rfx_id: str, key: str, note: str = Form("")):
    state = load_or_404(rfx_id)
    try:
        exc.reject_exception(state, key, note)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return RedirectResponse(f"/rfx/{rfx_id}/exceptions?status=open", status_code=303)
