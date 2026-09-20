"""Award-page actions: stub email notices to vendors + stakeholder alerts."""
from __future__ import annotations

from datetime import datetime, timezone

from . import engine, freeze

DEFAULT_STAKEHOLDERS = [
    {"role": "Category lead", "email": "category.lead@buyer.example"},
    {"role": "Finance", "email": "finance@buyer.example"},
    {"role": "Plant buyer", "email": "plant.buyer@buyer.example"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vendor_by_name(state: dict) -> dict[str, dict]:
    return {v["name"]: v for v in state.get("vendors", [])}


def push_flash(state: dict, message: str, level: str = "success") -> None:
    state["flash"] = {"message": message, "level": level, "at": _now()}


def pop_flash(state: dict) -> dict | None:
    return state.pop("flash", None)


def stakeholders(state: dict) -> list[dict]:
    return list(state.get("stakeholders") or DEFAULT_STAKEHOLDERS)


def _append_outbox(state: dict, entry: dict) -> None:
    state.setdefault("outbox", []).append(entry)


def _pack_or_raise(state: dict) -> dict:
    pack = freeze.current_freeze(state)
    if not pack:
        packs = state.get("freeze_packs") or []
        pack = packs[-1] if packs else None
    if not pack:
        raise ValueError("Freeze an award first before sending notices.")
    return pack


def send_award_notices(state: dict, vendor_id: str | None = None) -> dict:
    """Stub-send award notices / regrets to vendors; always notify stakeholders."""
    pack = _pack_or_raise(state)
    by_name = _vendor_by_name(state)
    sent: list[dict] = []
    messages = list(pack.get("notices") or []) + list(pack.get("regrets") or [])
    for msg in messages:
        v = by_name.get(msg.get("vendor") or "")
        if not v:
            continue
        if vendor_id and v["vendor_id"] != vendor_id:
            continue
        entry = {
            "kind": msg.get("kind") or "award_notice",
            "to": v.get("email") or f"{v['vendor_id']}@vendor.example",
            "vendor_id": v["vendor_id"],
            "vendor_name": v["name"],
            "subject": msg.get("subject") or "Award update",
            "body": msg.get("body") or "",
            "sent_at": _now(),
            "delivery": "stubbed (no SMTP)",
            "freeze_id": pack.get("id"),
            "snapshot_id": pack.get("calculation_snapshot_id"),
        }
        _append_outbox(state, entry)
        sent.append(entry)

    stake_entries = notify_stakeholders(
        state,
        action="award_notices_sent",
        detail=(
            f"Sent {len(sent)} vendor notice(s)/regret(s) for freeze {pack.get('id')} "
            f"(snapshot {pack.get('calculation_snapshot_id') or '—'}). "
            f"Strategy: {pack.get('strategy') or pack.get('strategy_key') or '—'}. "
            f"Total: {engine.fmt_inr(pack.get('total_extended_inr'))}."
        ),
        pack=pack,
    )
    flash = (
        f"Stub-sent {len(sent)} vendor email(s) and notified {len(stake_entries)} stakeholder(s). "
        f"See Email → Outbox."
    )
    push_flash(state, flash)
    return {"sent": sent, "stakeholders": stake_entries, "flash": flash}


def notify_stakeholders(
    state: dict, action: str, detail: str, pack: dict | None = None
) -> list[dict]:
    """Append internal stakeholder stub emails summarizing the award action."""
    pack = pack or freeze.current_freeze(state)
    snap = total = strategy = None
    vendors_summary = ""
    if pack:
        snap = pack.get("calculation_snapshot_id")
        total = pack.get("total_extended_inr")
        strategy = pack.get("strategy") or pack.get("strategy_key")
        share = pack.get("share_by_vendor") or {}
        if share:
            bits = []
            for n, s in share.items():
                if isinstance(s, dict):
                    lines = s.get("lines", s.get("line_count", "?"))
                    bits.append(f"{n} ({lines} lines)")
                else:
                    bits.append(str(n))
            vendors_summary = ", ".join(bits)
    title = (state.get("rfx") or {}).get("title") or state["id"]
    entries: list[dict] = []
    for sh in stakeholders(state):
        subject = f"[KQ] {action.replace('_', ' ').title()} — {title}"
        body = (
            f"Internal notification for {sh['role']} ({sh['email']}).\n\n"
            f"Event: {title} ({state['id']})\n"
            f"Action: {action}\n"
            f"When: {_now()}\n"
            f"Snapshot: {snap or '—'}\n"
            f"Strategy: {strategy or '—'}\n"
            f"Total extended: {engine.fmt_inr(total) if total is not None else '—'}\n"
            f"Vendors: {vendors_summary or '—'}\n\n"
            f"{detail}\n"
        )
        entry = {
            "kind": "stakeholder_alert",
            "to": sh["email"],
            "vendor_id": None,
            "vendor_name": sh["role"],
            "subject": subject,
            "body": body,
            "sent_at": _now(),
            "delivery": "stubbed (no SMTP)",
            "action": action,
            "freeze_id": (pack or {}).get("id") if pack else None,
            "snapshot_id": snap,
        }
        _append_outbox(state, entry)
        entries.append(entry)
    return entries


def record_export_notification(state: dict) -> dict:
    """Called when award workbook is exported — notify stakeholders + flash."""
    pack = freeze.current_freeze(state)
    stake = notify_stakeholders(
        state,
        action="award_workbook_exported",
        detail=(
            f"Award workbook (.xlsx) downloaded for RFx {state['id']}. "
            f"Freeze: {(pack or {}).get('id') or 'none'}; "
            f"snapshot: {(pack or {}).get('calculation_snapshot_id') or 'live'}."
        ),
        pack=pack,
    )
    flash = f"Award workbook exported. Notified {len(stake)} stakeholder(s) via Outbox."
    push_flash(state, flash)
    return {"stakeholders": stake, "flash": flash}
