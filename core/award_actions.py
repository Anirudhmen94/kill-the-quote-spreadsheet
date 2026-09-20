"""Award-page actions: stub email notices to vendors + stakeholder alerts."""
from __future__ import annotations

from datetime import datetime, timezone

from . import award_draft, engine, freeze

DEFAULT_STAKEHOLDERS = [
    {"role": "Manager", "email": "manager@buyer.example"},
    {"role": "Category lead", "email": "category.lead@buyer.example"},
    {"role": "Finance", "email": "finance@buyer.example"},
    {"role": "Plant buyer", "email": "plant.buyer@buyer.example"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vendor_by_name(state: dict) -> dict[str, dict]:
    return {v["name"]: v for v in state.get("vendors", [])}


def _vendor_by_id(state: dict) -> dict[str, dict]:
    return {v["vendor_id"]: v for v in state.get("vendors", [])}


def push_flash(
    state: dict,
    message: str,
    level: str = "success",
    *,
    cta_href: str | None = None,
    cta_label: str | None = None,
    confirmation: dict | None = None,
) -> None:
    state["flash"] = {"message": message, "level": level, "at": _now()}
    if cta_href:
        state["flash"]["cta_href"] = cta_href
    if cta_label:
        state["flash"]["cta_label"] = cta_label
    if confirmation:
        state["flash"]["confirmation"] = confirmation


def pop_flash(state: dict) -> dict | None:
    return state.pop("flash", None)


def stakeholders(state: dict) -> list[dict]:
    return list(state.get("stakeholders") or DEFAULT_STAKEHOLDERS)


def _append_outbox(state: dict, entry: dict) -> None:
    state.setdefault("outbox", []).append(entry)


def send_award_notices(state: dict, vendor_id: str | None = None) -> dict:
    """Stub-send award + regret notices from the Award draft (primary buyer path).

    Falls back to a valid freeze pack only when no draft allocation exists
    (legacy / demo seeds). Does not require freeze for the normal Award UX.
    """
    draft = state.get("award_draft") if isinstance(state.get("award_draft"), dict) else None
    has_draft_alloc = bool(draft and draft.get("allocation"))

    # Primary buyer path: Award draft with line assignments
    if has_draft_alloc:
        return _send_from_draft(state, vendor_id=vendor_id)
    # Legacy / demo: valid freeze pack notices
    return _send_from_freeze(state, vendor_id=vendor_id)


def _send_from_draft(state: dict, vendor_id: str | None = None) -> dict:
    check = award_draft.validate_for_send(state)
    if not check["ok"]:
        raise ValueError("; ".join(check["errors"]))

    notices, regrets = award_draft.build_notices_from_draft(state)
    by_id = _vendor_by_id(state)
    sent: list[dict] = []
    messages = list(notices) + list(regrets)
    for msg in messages:
        v = by_id.get(msg.get("vendor_id") or "") or _vendor_by_name(state).get(msg.get("vendor") or "")
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
            "line_nos": msg.get("line_nos") or [],
            "source": "award_draft",
            "snapshot_id": ((state.get("award_scenario") or {}).get("snapshot_id")),
        }
        _append_outbox(state, entry)
        sent.append(entry)

    totals = award_draft.draft_totals(state)
    award_vendors = sorted(
        {e["vendor_name"] for e in sent if e.get("kind") == "award_notice"}
    )
    regret_vendors = sorted(
        {
            e["vendor_name"]
            for e in sent
            if e.get("kind") in ("regret", "regret_notice")
        }
    )
    stake_list = stakeholders(state)
    confirmation = {
        "award_count": len(award_vendors),
        "regret_count": len(regret_vendors),
        "award_vendors": award_vendors,
        "regret_vendors": regret_vendors,
        "total_extended_inr": totals.get("total_extended_inr"),
        "covered_line_count": totals.get("covered_line_count"),
        "manager_notified": True,
        "manager_roles": [s.get("role") for s in stake_list],
        "manager_emails": [s.get("email") for s in stake_list],
    }
    award_draft.mark_sent(state, confirmation)

    stake_entries = notify_stakeholders(
        state,
        action="award_drafts_sent",
        detail=(
            f"Sent {len(award_vendors)} award draft(s) and {len(regret_vendors)} regret notice(s). "
            f"Award: {', '.join(award_vendors) or '—'}. "
            f"Regret: {', '.join(regret_vendors) or '—'}. "
            f"Draft total: {engine.fmt_inr(totals.get('total_extended_inr'))}."
        ),
        pack=None,
    )
    flash = (
        f"Award drafts sent to {len(award_vendors)} vendor(s); "
        f"regret notices to {len(regret_vendors)}. "
        f"Manager notified ({len(stake_entries)} stakeholder alert(s)). "
        f"View Outbox."
    )
    push_flash(
        state,
        flash,
        cta_href=f"/rfx/{state['id']}/email#outbox",
        cta_label="View Email Outbox",
        confirmation=confirmation,
    )
    return {
        "sent": sent,
        "stakeholders": stake_entries,
        "flash": flash,
        "confirmation": confirmation,
    }


def _send_from_freeze(state: dict, vendor_id: str | None = None) -> dict:
    """Legacy path: notices from a valid freeze pack (demo / back-compat)."""
    from . import event_status

    pack = event_status.active_valid_freeze(state)
    if not pack:
        latest = event_status.latest_relevant_freeze(state)
        if latest and event_status.freeze_validity(latest) in (
            event_status.VALIDITY_REQUIRES_REVIEW,
            event_status.VALIDITY_INVALID_HISTORICAL,
        ):
            raise ValueError(
                "Notices disabled — freeze requires review (invalid historical complete freeze). "
                "Use the Award draft Send flow instead, or create a replacement recommendation."
            )
        raise ValueError(
            "Complete the Award checklist and assign lines before sending notices."
        )

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
            "source": "freeze",
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
    award_count = sum(1 for entry in sent if entry.get("kind") == "award_notice")
    regret_count = sum(1 for entry in sent if entry.get("kind") in ("regret", "regret_notice"))
    award_vendors = sorted(
        {e["vendor_name"] for e in sent if e.get("kind") == "award_notice"}
    )
    regret_vendors = sorted(
        {
            e["vendor_name"]
            for e in sent
            if e.get("kind") in ("regret", "regret_notice")
        }
    )
    confirmation = {
        "award_count": award_count,
        "regret_count": regret_count,
        "award_vendors": award_vendors,
        "regret_vendors": regret_vendors,
        "manager_notified": True,
        "manager_roles": [s.get("role") for s in stakeholders(state)],
    }
    flash = (
        f"Successfully stub-sent {award_count} award notice(s) and {regret_count} regret notice(s) "
        f"to Outbox (no real SMTP). Manager notified. View Outbox."
    )
    push_flash(
        state,
        flash,
        cta_href=f"/rfx/{state['id']}/email#outbox",
        cta_label="View Email Outbox",
        confirmation=confirmation,
    )
    return {"sent": sent, "stakeholders": stake_entries, "flash": flash, "confirmation": confirmation}


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
    elif isinstance(state.get("award_draft"), dict):
        totals = award_draft.draft_totals(state)
        total = totals.get("total_extended_inr")
        strategy = "award draft · quality-gated line assign"
        share = totals.get("share_by_vendor") or {}
        bits = [f"{n} ({s.get('lines')} lines)" for n, s in share.items()]
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
    draft = state.get("award_draft") if isinstance(state.get("award_draft"), dict) else None
    detail = (
        f"Award workbook (.xlsx) downloaded for RFx {state['id']}. "
        f"Freeze: {(pack or {}).get('id') or 'none'}; "
        f"draft sent: {(draft or {}).get('sent')}; "
        f"snapshot: {(pack or {}).get('calculation_snapshot_id') or 'live'}."
    )
    stake = notify_stakeholders(
        state,
        action="award_workbook_exported",
        detail=detail,
        pack=pack,
    )
    flash = f"Award workbook exported. Notified {len(stake)} stakeholder(s) via Outbox."
    push_flash(state, flash)
    return {"stakeholders": stake, "flash": flash}
