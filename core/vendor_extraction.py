"""Structured vendor extraction status — buyer-safe messages, never raw exceptions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Canonical statuses (buyer-visible via buyer_message / UI labels)
STATUS_AWAITING = "awaiting"
STATUS_EXTRACTING = "extracting"
STATUS_EXTRACTED = "extracted"
STATUS_FAILED_NO_PREVIOUS = "failed_no_previous_data"
STATUS_FAILED_USING_PREVIOUS = "failed_using_previous_version"
STATUS_EXCLUDED = "excluded_by_buyer"

BUYER_MSG_FAILED_NO_PREVIOUS = (
    "We couldn't read this vendor's reply. Retry reading, exclude this vendor from "
    "the award with a reason, or view technical details in the AI log."
)
BUYER_MSG_FAILED_USING_PREVIOUS = (
    "Re-read failed. Keeping the previous successful extraction. Retry, or view "
    "technical details in the AI log."
)
BUYER_MSG_EXTRACTING = "Reading this vendor's reply…"
BUYER_MSG_AWAITING = "Awaiting vendor reply."
BUYER_MSG_EXCLUDED = "Excluded from this award by the buyer."

# Statuses that block a complete freeze unless the vendor is excluded.
COMPLETE_FREEZE_BLOCKING = frozenset(
    {
        STATUS_AWAITING,
        STATUS_EXTRACTING,
        STATUS_FAILED_NO_PREVIOUS,
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_status_blob(vendor: dict) -> dict:
    blob = vendor.get("extraction_status")
    if not isinstance(blob, dict):
        blob = {}
        vendor["extraction_status"] = blob
    return blob


def normalize_legacy_vendor(vendor: dict) -> dict:
    """Backfill extraction_status from legacy status/error fields."""
    blob = _ensure_status_blob(vendor)
    if blob.get("status"):
        return blob
    legacy = (vendor.get("status") or "").strip()
    has_ext = bool(vendor.get("extraction"))
    if legacy == "extracting":
        blob.update(
            {
                "status": STATUS_EXTRACTING,
                "buyer_message": BUYER_MSG_EXTRACTING,
                "technical_error": None,
            }
        )
    elif legacy == "error" or (vendor.get("error") and not has_ext):
        blob.update(
            {
                "status": STATUS_FAILED_NO_PREVIOUS,
                "buyer_message": BUYER_MSG_FAILED_NO_PREVIOUS,
                "technical_error": vendor.get("error"),
                "failure_at": blob.get("failure_at") or _now(),
            }
        )
    elif has_ext or legacy == "extracted":
        blob.update(
            {
                "status": STATUS_EXTRACTED,
                "buyer_message": None,
                "technical_error": None,
                "last_success_version": blob.get("last_success_version"),
            }
        )
    elif legacy == "received":
        blob.update(
            {
                "status": STATUS_AWAITING,
                "buyer_message": "Reply received — not read yet.",
                "technical_error": None,
            }
        )
    else:
        blob.update(
            {
                "status": STATUS_AWAITING,
                "buyer_message": BUYER_MSG_AWAITING,
                "technical_error": None,
            }
        )
    blob.setdefault("retry_count", 0)
    return blob


def get_status(vendor: dict) -> str:
    return normalize_legacy_vendor(vendor).get("status") or STATUS_AWAITING


def buyer_message(vendor: dict) -> str | None:
    blob = normalize_legacy_vendor(vendor)
    if blob.get("status") == STATUS_EXCLUDED:
        reason = blob.get("exclusion_reason") or ""
        base = BUYER_MSG_EXCLUDED
        return f"{base} Reason: {reason}" if reason else base
    msg = blob.get("buyer_message")
    if msg:
        return msg
    # Never fall back to raw vendor["error"] in buyer UI
    return None


def technical_error(vendor: dict) -> str | None:
    blob = normalize_legacy_vendor(vendor)
    return blob.get("technical_error") or None


def set_extracting(vendor: dict) -> None:
    blob = _ensure_status_blob(vendor)
    blob["status"] = STATUS_EXTRACTING
    blob["buyer_message"] = BUYER_MSG_EXTRACTING
    blob["technical_error"] = None
    vendor["status"] = "extracting"
    vendor["error"] = None  # never stash raw errors on legacy field for UI


def set_extracted(
    vendor: dict,
    *,
    version: int | None = None,
    model_call_id: str | None = None,
) -> None:
    blob = _ensure_status_blob(vendor)
    blob["status"] = STATUS_EXTRACTED
    blob["buyer_message"] = None
    blob["technical_error"] = None
    if version is not None:
        blob["last_success_version"] = version
    if model_call_id:
        blob["model_call_id"] = model_call_id
    vendor["status"] = "extracted"
    vendor["error"] = None


def set_failed(
    vendor: dict,
    *,
    technical: str,
    had_previous: bool,
    model_call_id: str | None = None,
) -> None:
    blob = _ensure_status_blob(vendor)
    blob["retry_count"] = int(blob.get("retry_count") or 0) + 1
    blob["failure_at"] = _now()
    blob["technical_error"] = (technical or "")[:4000]
    if model_call_id:
        blob["model_call_id"] = model_call_id
    if had_previous and vendor.get("extraction"):
        blob["status"] = STATUS_FAILED_USING_PREVIOUS
        ver = blob.get("last_success_version")
        msg = BUYER_MSG_FAILED_USING_PREVIOUS
        if ver is not None:
            msg = (
                f"Re-read failed. Keeping the previous successful extraction "
                f"(vendor data v{ver}). Retry, or view technical details in the AI log."
            )
        blob["buyer_message"] = msg
        vendor["status"] = "extracted"  # prior data still usable
    else:
        blob["status"] = STATUS_FAILED_NO_PREVIOUS
        blob["buyer_message"] = BUYER_MSG_FAILED_NO_PREVIOUS
        vendor["status"] = "error"
        vendor["extraction"] = None
    # Legacy error field: keep buyer-safe only (templates may still read v.error)
    vendor["error"] = blob["buyer_message"]


def set_excluded(
    vendor: dict,
    *,
    reason: str,
    actor: str = "buyer",
) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Exclusion requires a written reason.")
    blob = _ensure_status_blob(vendor)
    blob["status"] = STATUS_EXCLUDED
    blob["buyer_message"] = f"{BUYER_MSG_EXCLUDED} Reason: {reason}"
    blob["exclusion_reason"] = reason
    blob["exclusion_actor"] = actor
    blob["exclusion_at"] = _now()
    vendor["status"] = "excluded"
    vendor["error"] = blob["buyer_message"]


def is_excluded(vendor: dict) -> bool:
    return get_status(vendor) == STATUS_EXCLUDED


def contributes_prices(vendor: dict) -> bool:
    """Whether this vendor's extraction may contribute prices to compare/award."""
    st = get_status(vendor)
    if st == STATUS_EXCLUDED:
        return False
    if st == STATUS_FAILED_NO_PREVIOUS:
        return False
    if st in (STATUS_AWAITING, STATUS_EXTRACTING):
        return False
    return bool(vendor.get("extraction"))


def vendors_blocking_complete_freeze(state: dict) -> list[dict]:
    """Expected vendors whose extraction status blocks a complete freeze."""
    out = []
    for v in state.get("vendors") or []:
        # Expected = has files (reply arrived) or already in play; skip never-invited empties? 
        # Brief: awaiting | extracting | failed_no_previous_data unless excluded.
        # Treat all listed vendors as expected for the event.
        st = get_status(v)
        if st == STATUS_EXCLUDED:
            continue
        if st in COMPLETE_FREEZE_BLOCKING:
            # Awaiting with no files: still blocks complete (buyer must exclude or wait)
            out.append(
                {
                    "vendor_id": v.get("vendor_id"),
                    "vendor": v.get("name"),
                    "status": st,
                    "buyer_message": buyer_message(v),
                }
            )
    return out


def validate_extraction_payload(data: dict) -> list[str]:
    """Fail if required arrays were omitted (None) rather than present as empty."""
    errors: list[str] = []
    for key in ("line_quotes", "not_quoted_line_nos", "commercials", "questionnaire", "certificates"):
        if key not in data or data[key] is None:
            errors.append(
                f"Required field {key!r} was omitted — cannot treat as empty 'none found'."
            )
    return errors


def summarize_for_ui(vendor: dict) -> dict[str, Any]:
    blob = normalize_legacy_vendor(vendor)
    return {
        "status": blob.get("status"),
        "buyer_message": buyer_message(vendor),
        "has_technical_details": bool(blob.get("technical_error")),
        "retry_count": blob.get("retry_count") or 0,
        "last_success_version": blob.get("last_success_version"),
        "excluded": blob.get("status") == STATUS_EXCLUDED,
        "exclusion_reason": blob.get("exclusion_reason"),
    }
