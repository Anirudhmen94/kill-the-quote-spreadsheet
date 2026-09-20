"""Authoritative buyer-facing event / freeze display status.

Single derivation for homepage cards and page status badges.
Raw state["status"] (e.g. award_frozen) must not be shown when a freeze
was repaired to requires_review / invalid_historical.
"""
from __future__ import annotations

from typing import Any

# FreezeValidity: how to treat a freeze pack for notices / active award.
VALIDITY_VALID = "valid"
VALIDITY_STALE = "stale"
VALIDITY_REQUIRES_REVIEW = "requires_review"
VALIDITY_INVALID_HISTORICAL = "invalid_historical"

# Display status keys (stable for tests / data-testid)
STATUS_FREEZE_REQUIRES_REVIEW = "freeze_requires_review"
STATUS_COMPLETE_FROZEN = "complete_frozen"
STATUS_PARTIAL_FROZEN = "partial_frozen"
STATUS_RECOMMENDATION_STALE = "recommendation_stale"
STATUS_RECOMMENDATION_SAVED = "recommendation_saved"
STATUS_PROCESSING_REQUIRES_ACTION = "processing_requires_action"
STATUS_DRAFT = "draft"
STATUS_SENT = "sent"
STATUS_RESPONSES = "responses_received"
STATUS_EXTRACTED = "extracted"
STATUS_COMPARED = "compared"
STATUS_AWARDED = "awarded"
STATUS_AWARD_DRAFTS_SENT = "award_drafts_sent"

LABELS: dict[str, str] = {
    STATUS_FREEZE_REQUIRES_REVIEW: "Freeze requires review",
    STATUS_COMPLETE_FROZEN: "Award frozen · complete",
    STATUS_PARTIAL_FROZEN: "Award frozen · partial",
    STATUS_AWARD_DRAFTS_SENT: "Award drafts sent",
    STATUS_RECOMMENDATION_STALE: "Recommendation stale",
    STATUS_RECOMMENDATION_SAVED: "Recommendation saved",
    STATUS_PROCESSING_REQUIRES_ACTION: "Processing requires action",
    STATUS_DRAFT: "Draft",
    STATUS_SENT: "Sent",
    STATUS_RESPONSES: "Responses received",
    STATUS_EXTRACTED: "Extracted",
    STATUS_COMPARED: "Compared",
    STATUS_AWARDED: "Awarded",
}


def freeze_validity(pack: dict | None) -> str | None:
    """Classify a freeze pack. None if no pack."""
    if not pack:
        return None
    integrity = pack.get("integrity") or ""
    status = pack.get("status") or ""
    if integrity == "invalid_historical_freeze" or status == "requires_review":
        return VALIDITY_REQUIRES_REVIEW
    if status == "invalid_historical_freeze":
        return VALIDITY_INVALID_HISTORICAL
    if status == "historical":
        return VALIDITY_STALE
    if status == "frozen" and integrity != "invalid_historical_freeze":
        # Complete-with-gaps without repair yet — treat as requires review
        uncovered = pack.get("uncovered_lines") or []
        mode = pack.get("freeze_mode") or "complete"
        if mode == "complete" and uncovered:
            return VALIDITY_REQUIRES_REVIEW
        return VALIDITY_VALID
    if status in ("requires_review",):
        return VALIDITY_REQUIRES_REVIEW
    return VALIDITY_STALE


def is_active_valid_freeze(pack: dict | None) -> bool:
    """True only for a currently frozen pack that may send notices / count as locked."""
    return freeze_validity(pack) == VALIDITY_VALID and (pack or {}).get("status") == "frozen"


def latest_relevant_freeze(state: dict) -> dict | None:
    """Prefer state['freeze'], else newest pack — for validity / display."""
    from . import freeze as freeze_mod

    freeze_mod.refresh_freeze_staleness(state)
    freeze_mod.repair_historical_freezes(state)
    pack = state.get("freeze")
    if pack:
        return pack
    packs = state.get("freeze_packs") or []
    return packs[-1] if packs else None


def active_valid_freeze(state: dict) -> dict | None:
    """Return the current valid frozen pack, or None."""
    from . import freeze as freeze_mod

    pack = freeze_mod.current_freeze(state)
    if is_active_valid_freeze(pack):
        return pack
    # current_freeze may return requires_review for UI — exclude those
    frozen = [
        p
        for p in (state.get("freeze_packs") or [])
        if is_active_valid_freeze(p)
    ]
    return frozen[-1] if frozen else None


def _failed_unexcluded(state: dict) -> list[dict]:
    from . import vendor_extraction as vex

    out = []
    for v in state.get("vendors") or []:
        st = vex.get_status(v)
        if st == vex.STATUS_FAILED_NO_PREVIOUS:
            out.append(v)
        elif st in (vex.STATUS_AWAITING, vex.STATUS_EXTRACTING) and v.get("files"):
            out.append(v)
    return out


def derive_event_display_status(state: dict) -> dict[str, Any]:
    """Buyer-facing display status with priority order from the product brief.

    Priority:
      1. invalid / requires_review freeze
      2. valid complete freeze
      3. valid partial freeze
      4. stale recommendation
      5. saved recommendation
      6. extraction needs action
      7. normal workflow (draft → sent → responses → extracted → compared)
    """
    from . import scenario, snapshots

    snapshots.ensure_snapshot_fields(state)
    pack = latest_relevant_freeze(state)
    validity = freeze_validity(pack)

    if validity in (VALIDITY_REQUIRES_REVIEW, VALIDITY_INVALID_HISTORICAL):
        return _result(
            STATUS_FREEZE_REQUIRES_REVIEW,
            pack=pack,
            validity=validity,
            detail=(pack or {}).get("invalid_reason")
            or "Historical complete freeze has coverage gaps and needs review.",
        )

    if is_active_valid_freeze(pack):
        mode = (pack or {}).get("freeze_mode") or "complete"
        if mode == "partial":
            return _result(STATUS_PARTIAL_FROZEN, pack=pack, validity=VALIDITY_VALID)
        return _result(STATUS_COMPLETE_FROZEN, pack=pack, validity=VALIDITY_VALID)

    draft = state.get("award_draft") if isinstance(state.get("award_draft"), dict) else None
    if draft and draft.get("sent"):
        conf = draft.get("send_confirmation") or {}
        detail = None
        if conf.get("award_vendors") or conf.get("regret_vendors"):
            detail = (
                f"Award drafts: {', '.join(conf.get('award_vendors') or []) or '—'}; "
                f"regrets: {', '.join(conf.get('regret_vendors') or []) or '—'}"
            )
        return _result(STATUS_AWARD_DRAFTS_SENT, detail=detail)

    life = scenario.recommendation_lifecycle(state)
    lifecycle = life.get("lifecycle") or ""
    if lifecycle == scenario.REC_STALE or lifecycle == "stale":
        return _result(STATUS_RECOMMENDATION_STALE, detail=life.get("banner"))
    if lifecycle in (scenario.REC_SAVED, "saved", "current") and life.get("can_freeze"):
        return _result(STATUS_RECOMMENDATION_SAVED)

    failed = _failed_unexcluded(state)
    if failed:
        names = ", ".join(v.get("name") or v.get("vendor_id") or "?" for v in failed[:3])
        return _result(
            STATUS_PROCESSING_REQUIRES_ACTION,
            detail=f"Vendor response needs retry or exclude: {names}",
            failed_vendors=failed,
        )

    # Normal workflow from raw / derived state
    raw = (state.get("status") or "draft").strip()
    if raw in ("award_frozen", "awarded") and not is_active_valid_freeze(pack):
        # Stale raw flag after repair or historical — fall through to compared/rec
        if lifecycle in (scenario.REC_SAVED, "saved") and life.get("can_freeze"):
            return _result(STATUS_RECOMMENDATION_SAVED)
        raw = "compared"

    key_map = {
        "draft": STATUS_DRAFT,
        "sent": STATUS_SENT,
        "responses_received": STATUS_RESPONSES,
        "extracted": STATUS_EXTRACTED,
        "compared": STATUS_COMPARED,
        "recommended": STATUS_RECOMMENDATION_SAVED,
        "awarded": STATUS_AWARDED,
        "award_frozen": STATUS_COMPLETE_FROZEN,
        "award_drafts_sent": STATUS_AWARD_DRAFTS_SENT,
    }
    key = key_map.get(raw, raw if raw in LABELS else STATUS_DRAFT)
    # Prefer "compared" when vendors extracted
    vendors = state.get("vendors") or []
    with_files = [v for v in vendors if v.get("files")]
    extracted = [v for v in with_files if v.get("status") == "extracted" and v.get("extraction")]
    excluded = [v for v in with_files if v.get("status") == "excluded"]
    if key in (STATUS_DRAFT, STATUS_SENT, STATUS_RESPONSES) and extracted:
        if len(extracted) + len(excluded) >= len(with_files) and with_files:
            key = STATUS_COMPARED if raw in ("compared", "extracted", "responses_received") or extracted else key
            if raw == "compared" or (extracted and len(extracted) + len(excluded) == len(with_files)):
                key = STATUS_COMPARED
    return _result(key)


def _result(
    key: str,
    *,
    pack: dict | None = None,
    validity: str | None = None,
    detail: str | None = None,
    failed_vendors: list | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": LABELS.get(key, key.replace("_", " ").capitalize()),
        "validity": validity,
        "detail": detail,
        "freeze_id": (pack or {}).get("id") if pack else None,
        "freeze_mode": (pack or {}).get("freeze_mode") if pack else None,
        "failed_vendors": failed_vendors or [],
    }


def derive_lock_cta(
    state: dict,
    *,
    live: dict | None = None,
    life: dict | None = None,
    freeze_check_complete: dict | None = None,
    has_blocking_exceptions: bool = False,
) -> dict[str, Any]:
    """Contextual Lock award CTA — never an enabled generic Lock when invalid.

    Returns:
      show_enabled_lock: bool
      cta_key / cta_label / cta_href / cta_detail / cta_vendor_id
    """
    from . import scenario, vendor_extraction as vex

    life = life or scenario.recommendation_lifecycle(state)
    pack = latest_relevant_freeze(state)
    validity = freeze_validity(pack)
    rid = state.get("id") or ""

    # Already validly frozen — no lock CTA
    if is_active_valid_freeze(pack):
        return {
            "show_enabled_lock": False,
            "already_frozen": True,
            "cta_key": None,
            "cta_label": None,
            "cta_href": None,
            "cta_detail": None,
            "cta_vendor_id": None,
        }

    if validity in (VALIDITY_REQUIRES_REVIEW, VALIDITY_INVALID_HISTORICAL):
        return {
            "show_enabled_lock": False,
            "already_frozen": False,
            "cta_key": "invalid_historical_freeze",
            "cta_label": "Create replacement recommendation",
            "cta_href": f"/rfx/{rid}/award#replacement-rec",
            "cta_detail": (
                "This historical complete freeze has coverage gaps and requires review. "
                "Preserve it for audit, recalculate, save a new recommendation with a "
                "rationale (and resolve failed responses) — do not auto-freeze."
            ),
            "cta_vendor_id": None,
            "freeze_pack": pack,
        }

    failed = [
        v
        for v in (state.get("vendors") or [])
        if vex.get_status(v) == vex.STATUS_FAILED_NO_PREVIOUS
    ]
    if failed:
        v = failed[0]
        return {
            "show_enabled_lock": False,
            "already_frozen": False,
            "cta_key": "failed_response",
            "cta_label": "Resolve vendor response first",
            "cta_href": f"/rfx/{rid}/email#vendor-{v.get('vendor_id')}",
            "cta_detail": (
                f"{v.get('name')}: retry reading or exclude with a reason before locking."
            ),
            "cta_vendor_id": v.get("vendor_id"),
        }

    awaiting = [
        v
        for v in (state.get("vendors") or [])
        if vex.get_status(v) in (vex.STATUS_AWAITING, vex.STATUS_EXTRACTING) and v.get("files")
    ]
    if awaiting:
        v = awaiting[0]
        return {
            "show_enabled_lock": False,
            "already_frozen": False,
            "cta_key": "awaiting_extracting",
            "cta_label": "Finish reading responses first",
            "cta_href": f"/rfx/{rid}/email",
            "cta_detail": f"{v.get('name')} is still awaiting or being read.",
            "cta_vendor_id": v.get("vendor_id"),
        }

    if not life.get("can_freeze"):
        return {
            "show_enabled_lock": False,
            "already_frozen": False,
            "cta_key": "unsaved_recommendation",
            "cta_label": "Save recommendation",
            "cta_href": f"/rfx/{rid}/award#lock-send",
            "cta_detail": life.get("freeze_blocked_reason")
            or "Save a current recommendation before locking.",
            "cta_vendor_id": None,
            "needs_rationale": True,
        }

    check = freeze_check_complete
    if check is None and live and live.get("available"):
        from . import freeze as freeze_mod

        check = freeze_mod.validate_freeze_request(state, mode="complete")

    if check and not check.get("ok"):
        types = {
            (e.get("type") if isinstance(e, dict) else None) for e in (check.get("errors") or [])
        }
        uncovered = check.get("uncovered_lines") or []
        if "incomplete_coverage" in types or uncovered:
            return {
                "show_enabled_lock": False,
                "already_frozen": False,
                "cta_key": "incomplete_coverage",
                "cta_label": "Freeze partial…",
                "cta_href": f"/rfx/{rid}/award#lock-send",
                "cta_detail": (
                    "Quality-gated scenario coverage is incomplete. Use Freeze partial… "
                    "with acknowledgements and a written reason, or resolve gaps on Anomalies."
                ),
                "cta_vendor_id": None,
                "open_partial": True,
            }
        if "selected_award_blockers" in types or has_blocking_exceptions:
            return {
                "show_enabled_lock": False,
                "already_frozen": False,
                "cta_key": "selected_blockers",
                "cta_label": "Resolve on Anomalies",
                "cta_href": f"/rfx/{rid}/anomalies",
                "cta_detail": "Selected-award blockers remain. Resolve or acknowledge via Freeze partial…",
                "cta_vendor_id": None,
            }
        # Other complete-freeze blockers — still no enabled generic lock
        return {
            "show_enabled_lock": False,
            "already_frozen": False,
            "cta_key": "freeze_blocked",
            "cta_label": "Review freeze blockers",
            "cta_href": f"/rfx/{rid}/award#lock-send",
            "cta_detail": "; ".join(
                (e.get("message") if isinstance(e, dict) else str(e))
                for e in (check.get("errors") or [])[:3]
            ),
            "cta_vendor_id": None,
        }

    # Complete freeze is clear — enabled Lock award is OK
    if live and live.get("available") and life.get("can_freeze"):
        return {
            "show_enabled_lock": True,
            "already_frozen": False,
            "cta_key": None,
            "cta_label": None,
            "cta_href": None,
            "cta_detail": None,
            "cta_vendor_id": None,
        }

    return {
        "show_enabled_lock": False,
        "already_frozen": False,
        "cta_key": "not_ready",
        "cta_label": "Extract responses first",
        "cta_href": f"/rfx/{rid}/email",
        "cta_detail": "Award calculation is not available yet.",
        "cta_vendor_id": None,
    }
