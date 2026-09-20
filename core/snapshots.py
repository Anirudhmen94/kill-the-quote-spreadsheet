"""Calculation snapshots and vendor-data versioning.

Every analyst answer and award recommendation is tied to a vendor-data version
and a reproducible calculation snapshot. When vendor data changes, dependent
answers and recommendations are marked stale — never silently overwritten.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from . import engine

# Reasons that bump the vendor-data version.
BUMP_REASONS = (
    "vendor_extracted",
    "vendor_reprocessed",
    "vendor_uploaded",
    "vendor_deleted",
    "review_accepted",
    "review_overridden",
    "review_cleared",
    "questionnaire_updated",
    "manual_edit",
    "vendor_simulated",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_id() -> str:
    return uuid.uuid4().hex[:10]


def ensure_snapshot_fields(state: dict) -> dict:
    """Idempotent migration: add snapshot fields to older states; mark legacy rows stale."""
    changed = False
    if "vendor_data_version" not in state:
        state["vendor_data_version"] = 0
        changed = True
    if "vendor_data_versions" not in state:
        state["vendor_data_versions"] = []
        changed = True
    if "calculation_snapshots" not in state:
        state["calculation_snapshots"] = []
        changed = True
    if "version_events" not in state:
        state["version_events"] = []  # recent UI-visible notices
        changed = True
    if "recommendations" not in state:
        # Migrate single recommendation → list, keep legacy pointer for templates
        state["recommendations"] = []
        rec = state.get("recommendation")
        if rec and isinstance(rec, dict):
            legacy = {
                "id": rec.get("id") or _short_id(),
                "rfx_id": state["id"],
                "analyst_answer_id": None,
                "chat_index": rec.get("chat_index"),
                "calculation_snapshot_id": rec.get("calculation_snapshot_id"),
                "vendor_data_version": rec.get("vendor_data_version"),
                "question": rec.get("question", ""),
                "recommendation_markdown": rec.get("answer", ""),
                "tables": rec.get("tables", []),
                "caveats": rec.get("caveats", []),
                "total_value": rec.get("total_value"),
                "covered_line_count": rec.get("covered_line_count"),
                "vendor_allocation": rec.get("vendor_allocation"),
                "created_at": rec.get("saved_at") or _now(),
                "status": "stale" if rec.get("vendor_data_version") is None else rec.get("status", "stale"),
                "stale_reason": rec.get("stale_reason")
                or "This recommendation was created before calculation snapshots were enabled and must be regenerated.",
                "legacy": True,
            }
            state["recommendations"].append(legacy)
            state["recommendation"] = legacy  # keep single-pointer in sync
        changed = True

    # Migrate chat answers missing version/snapshot metadata
    for m in state.get("chat", []):
        if "id" not in m:
            m["id"] = _short_id()
            changed = True
        if m.get("vendor_data_version") is None and m.get("status") is None:
            m["status"] = "stale"
            m["stale_reason"] = (
                "This answer was created before calculation snapshots were enabled and must be regenerated."
            )
            m["legacy"] = True
            changed = True
        elif "status" not in m:
            m["status"] = "current" if m.get("vendor_data_version") == state.get("vendor_data_version") else "stale"
            changed = True

    # Refresh stale flags against current version
    refresh_staleness(state)
    return state


def current_version(state: dict) -> int:
    ensure_snapshot_fields(state)
    return int(state.get("vendor_data_version") or 0)


def input_hash(state: dict, parameters: dict | None = None) -> str:
    """Stable hash of all decision-relevant inputs (not timestamps)."""
    ensure_snapshot_fields(state)
    vendors_payload = []
    for v in state.get("vendors", []):
        ext = v.get("extraction")
        vendors_payload.append(
            {
                "vendor_id": v.get("vendor_id"),
                "status": v.get("status"),
                "file_ids": [f.get("file_id") for f in v.get("files", [])],
                "extraction": _stable_extraction(ext) if ext else None,
            }
        )
    payload = {
        "rfx_id": state.get("id"),
        "vendor_data_version": current_version(state),
        "fx": state.get("fx"),
        "reviews": sorted(
            [
                {
                    "vendor_id": r["vendor_id"],
                    "line_no": r["line_no"],
                    "action": r["action"],
                    "value_inr": r.get("value_inr"),
                }
                for r in state.get("reviews", [])
            ],
            key=lambda x: (x["vendor_id"], x["line_no"]),
        ),
        "vendors": vendors_payload,
        "questionnaire": [
            {"q_id": q["q_id"], "knockout": q.get("knockout"), "text": q.get("text")}
            for q in state.get("rfx", {}).get("questionnaire", [])
        ],
        "parameters": parameters or {},
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _stable_extraction(ext: dict) -> dict:
    """Subset of extraction that affects calculations."""
    return {
        "line_quotes": [
            {
                "line_no": q.get("line_no"),
                "price": q.get("price"),
                "currency": q.get("currency"),
                "price_basis": q.get("price_basis"),
                "basis_qty": q.get("basis_qty"),
                "status": q.get("status"),
                "confidence": q.get("confidence"),
            }
            for q in ext.get("line_quotes", [])
        ],
        "not_quoted_line_nos": sorted(ext.get("not_quoted_line_nos") or []),
        "questionnaire": [
            {"q_id": a.get("q_id"), "answer": a.get("answer"), "answered": a.get("answered"), "status": a.get("status")}
            for a in ext.get("questionnaire", [])
        ],
        "commercials": [
            {"key": t.get("key"), "value": t.get("value"), "numeric_pct": t.get("numeric_pct"), "applies_to": t.get("applies_to")}
            for t in ext.get("commercials", [])
        ],
        "certificates": [
            {"name": c.get("name"), "attached": c.get("attached_as_document"), "claimed": c.get("claimed_in_text")}
            for c in ext.get("certificates", [])
        ],
        "notes": ext.get("notes", ""),
    }


def bump_vendor_data_version(
    state: dict,
    reason: str,
    affected_vendor_ids: list[str] | None = None,
    affected_file_ids: list[str] | None = None,
    created_by: str | None = None,
    notice: str | None = None,
) -> dict:
    """Monotonically increase vendor-data version and invalidate dependents."""
    ensure_snapshot_fields(state)
    if reason not in BUMP_REASONS:
        reason = "manual_edit"
    new_ver = current_version(state) + 1
    event = {
        "id": _short_id(),
        "rfx_id": state["id"],
        "version": new_ver,
        "created_at": _now(),
        "reason": reason,
        "affected_vendor_ids": list(affected_vendor_ids or []),
        "affected_file_ids": list(affected_file_ids or []),
        "created_by": created_by,
    }
    state["vendor_data_version"] = new_ver
    state["vendor_data_versions"].append(event)
    # Keep a bounded history
    if len(state["vendor_data_versions"]) > 100:
        state["vendor_data_versions"] = state["vendor_data_versions"][-100:]

    stale_reason = _stale_reason_for(reason, event, affected_vendor_ids)
    _invalidate_dependents(state, new_ver, stale_reason)

    names = _vendor_names(state, affected_vendor_ids or [])
    auto_notice = notice or _default_notice(reason, names, new_ver)
    state["version_events"].append(
        {
            "at": event["created_at"],
            "version": new_ver,
            "reason": reason,
            "message": auto_notice,
            "affected_vendor_ids": event["affected_vendor_ids"],
        }
    )
    if len(state["version_events"]) > 20:
        state["version_events"] = state["version_events"][-20:]
    return event


def _vendor_names(state: dict, ids: list[str]) -> list[str]:
    by_id = {v["vendor_id"]: v.get("name", v["vendor_id"]) for v in state.get("vendors", [])}
    return [by_id.get(i, i) for i in ids]


def _default_notice(reason: str, names: list[str], version: int) -> str:
    who = ", ".join(names) if names else "vendor data"
    if reason in ("vendor_extracted", "vendor_reprocessed"):
        return f"{who} processed · Vendor data version {version} created · Existing analyst answers marked stale"
    if reason == "vendor_uploaded":
        return f"{who} uploaded · Vendor data version {version} created"
    if reason == "vendor_deleted":
        return f"{who} removed · Vendor data version {version} created · Existing analyst answers marked stale"
    if reason.startswith("review_"):
        return f"Review action on {who} · Vendor data version {version} created · Existing answers marked stale"
    if reason == "vendor_simulated":
        return f"Simulated vendor replies · Vendor data version {version} created"
    return f"Vendor data version {version} created ({reason})"


def _stale_reason_for(reason: str, event: dict, affected: list[str] | None) -> str:
    n = len(affected or [])
    if reason in ("vendor_extracted", "vendor_reprocessed"):
        return (
            f"{n} vendor response(s) were processed after this answer was generated "
            f"(now at vendor data version {event['version']})."
        )
    if reason.startswith("review_"):
        return (
            f"A buyer review action changed the dataset after this was generated "
            f"(now at vendor data version {event['version']})."
        )
    return f"Vendor data changed after this was generated (now at version {event['version']})."


def _invalidate_dependents(state: dict, new_ver: int, stale_reason: str) -> None:
    for m in state.get("chat", []):
        vdv = m.get("vendor_data_version")
        if vdv is None or vdv < new_ver:
            if m.get("status") != "failed":
                m["status"] = "stale"
                m["stale_reason"] = stale_reason

    for snap in state.get("calculation_snapshots", []):
        if snap.get("vendor_data_version", 0) < new_ver and snap.get("status") == "current":
            snap["status"] = "superseded"

    for rec in state.get("recommendations", []):
        vdv = rec.get("vendor_data_version")
        if vdv is None or vdv < new_ver:
            if rec.get("status") != "superseded":
                rec["status"] = "stale"
                rec["stale_reason"] = stale_reason

    # Keep legacy single pointer in sync
    if state.get("recommendation") and isinstance(state["recommendation"], dict):
        ptr = state["recommendation"]
        # Prefer matching by id in recommendations list
        match = next((r for r in state["recommendations"] if r.get("id") == ptr.get("id")), None)
        if match:
            state["recommendation"] = match
        else:
            vdv = ptr.get("vendor_data_version")
            if vdv is None or vdv < new_ver:
                ptr["status"] = "stale"
                ptr["stale_reason"] = stale_reason


def refresh_staleness(state: dict) -> None:
    """Recompute current/stale flags from vendor_data_version (idempotent)."""
    cur = int(state.get("vendor_data_version") or 0)
    for m in state.get("chat", []):
        if m.get("status") == "failed":
            continue
        vdv = m.get("vendor_data_version")
        if vdv is None:
            m["status"] = "stale"
            m.setdefault(
                "stale_reason",
                "This answer was created before calculation snapshots were enabled and must be regenerated.",
            )
        elif vdv < cur:
            m["status"] = "stale"
            m.setdefault(
                "stale_reason",
                f"Vendor data changed after this answer was generated (answer v{vdv}, current v{cur}).",
            )
        else:
            m["status"] = "current"
            m.pop("stale_reason", None)

    for rec in state.get("recommendations", []):
        if rec.get("status") == "superseded":
            continue
        vdv = rec.get("vendor_data_version")
        if vdv is None:
            rec["status"] = "stale"
            rec.setdefault(
                "stale_reason",
                "This recommendation was created before calculation snapshots were enabled and must be regenerated.",
            )
        elif vdv < cur:
            rec["status"] = "stale"
            rec.setdefault(
                "stale_reason",
                f"Vendor data changed after this recommendation was saved (rec v{vdv}, current v{cur}).",
            )
        else:
            rec["status"] = "current"
            rec.pop("stale_reason", None)

    # Sync pointer to latest current, else keep last but mark stale
    recs = state.get("recommendations") or []
    current_recs = [r for r in recs if r.get("status") == "current"]
    if current_recs:
        state["recommendation"] = current_recs[-1]
    elif recs:
        state["recommendation"] = recs[-1]


def create_calculation_snapshot(
    state: dict,
    calculation_type: str,
    parameters: dict | None = None,
    result: dict | None = None,
    status: str = "current",
) -> dict:
    ensure_snapshot_fields(state)
    ih = input_hash(state, parameters)
    # Reuse an existing current snapshot with the same type + input hash
    if status == "current":
        for snap in reversed(state["calculation_snapshots"]):
            if (
                snap.get("calculation_type") == calculation_type
                and snap.get("status") == "current"
                and snap.get("input_hash") == ih
                and snap.get("vendor_data_version") == current_version(state)
            ):
                if result:
                    snap["result"] = result
                return snap
        for snap in state["calculation_snapshots"]:
            if snap.get("calculation_type") == calculation_type and snap.get("status") == "current":
                snap["status"] = "superseded"
    snap = {
        "id": _short_id(),
        "rfx_id": state["id"],
        "vendor_data_version": current_version(state),
        "input_hash": ih,
        "calculation_type": calculation_type,
        "parameters": parameters or {},
        "result": result or {},
        "created_at": _now(),
        "status": status,
    }
    state["calculation_snapshots"].append(snap)
    if len(state["calculation_snapshots"]) > 200:
        state["calculation_snapshots"] = state["calculation_snapshots"][-200:]
    return snap


def processing_counts(state: dict) -> dict:
    """How many vendor responses are extracted / processing / failed / awaiting."""
    vendors = state.get("vendors", [])
    with_files = [v for v in vendors if v.get("files")]
    extracted = [v for v in with_files if v.get("status") == "extracted"]
    extracting = [v for v in with_files if v.get("status") == "extracting"]
    failed = [v for v in with_files if v.get("status") == "error"]
    pending = [v for v in with_files if v.get("status") in ("received", "awaiting") or (v.get("files") and v.get("status") not in ("extracted", "extracting", "error"))]
    awaiting_reply = [v for v in vendors if not v.get("files")]
    return {
        "total_vendors": len(vendors),
        "with_files": len(with_files),
        "extracted": len(extracted),
        "extracting": len(extracting),
        "failed": len(failed),
        "pending": len(pending),
        "awaiting_reply": len(awaiting_reply),
        "processing": len(extracting) + len(pending),
        "all_terminal": len(with_files) > 0 and len(extracting) == 0 and len(pending) == 0,
        "all_extracted": len(with_files) > 0 and len(extracted) == len(with_files),
    }


def unresolved_summary(state: dict) -> dict:
    """Cell-level exclusion counts — same SSOT language as Compare/Award banner.

    Uses awardability annotation so "needs review" is never relabelled "assumed"
    and excluded counts are cells (not lines).
    """
    if not any(v.get("extraction") for v in state.get("vendors", [])):
        return {
            "needs_review": 0,
            "unresolved": 0,
            "missing": 0,
            "assumed": 0,
            "conversion_failed": 0,
            "excluded_from_totals": 0,
            "headline": "0 cells excluded from totals (none) · 0 lines with no awardable quote",
        }
    # Local import avoids cycle at module load
    from . import awardability, scenario

    cmp = engine.build_comparison(state)
    # Keep SSOT with enrich_state_comparison: blended rates excluded before counts
    scenario.apply_blended_rate_exclusions(cmp)
    awardability.annotate_comparison(cmp)
    ex = cmp["exclusion_summary"]
    status_needs = status_unresolved = status_missing = 0
    for v in cmp["vendors"]:
        status_needs += v["counts"].get("needs_review", 0)
        status_unresolved += v["counts"].get("unresolved", 0)
        status_missing += v["counts"].get("missing", 0)
    return {
        # Status-grid counts (for export meta / inbox)
        "needs_review": status_needs,
        "unresolved": status_unresolved,
        "missing": status_missing,
        # Awardability buckets (SSOT with exclusion banner)
        "assumed": ex.get("assumed", 0),
        "conversion_failed": ex.get("conversion_failed", 0),
        "excluded_from_totals": ex["excluded_cells"],
        "headline": ex["headline"],
    }


def data_status(state: dict, context: str = "global", answer: dict | None = None) -> dict:
    """UI-facing data status for the banner.

    context: 'global' | 'ask_answer' | 'compare' | 'award' | 'inbox' | 'email'
    """
    ensure_snapshot_fields(state)
    ver = current_version(state)
    counts = processing_counts(state)
    unresolved = unresolved_summary(state)
    last = state["vendor_data_versions"][-1] if state.get("vendor_data_versions") else None
    updated_at = (last or {}).get("created_at") or state.get("created_at") or ""

    # Processing takes precedence
    if counts["extracting"] or counts["pending"]:
        n = counts["processing"]
        return {
            "kind": "processing",
            "level": "warn",
            "label": "Processing",
            "text": f"Provisional · {n} vendor response{'s' if n != 1 else ''} still being extracted",
            "version": ver,
            "updated_at": updated_at,
            "counts": counts,
            "unresolved": unresolved,
        }

    if answer is not None:
        status = answer.get("status", "current")
        if status == "stale" or answer.get("legacy"):
            return {
                "kind": "stale",
                "level": "danger",
                "label": "Stale",
                "text": "Stale answer · Vendor data changed after this answer was generated",
                "detail": answer.get("stale_reason", ""),
                "version": ver,
                "answer_version": answer.get("vendor_data_version"),
                "updated_at": updated_at,
                "counts": counts,
                "unresolved": unresolved,
            }
        if status == "failed":
            return {
                "kind": "failed",
                "level": "danger",
                "label": "Failed",
                "text": "This answer failed to generate",
                "version": ver,
                "updated_at": updated_at,
                "counts": counts,
                "unresolved": unresolved,
            }

    if context in ("inbox", "email"):
        if counts["with_files"] == 0:
            kind_text = "Awaiting responses"
        elif counts["all_extracted"] and counts["failed"] == 0:
            kind_text = f"All responses processed · Version {ver}"
        elif counts["all_terminal"] and counts["failed"]:
            kind_text = f"Processing complete with failures · Version {ver}"
        else:
            kind_text = f"Processing {counts['processing']} responses · Version {ver}"
        return {
            "kind": "inbox",
            "level": "info" if counts["all_extracted"] else "warn",
            "label": kind_text.split(" · ")[0],
            "text": kind_text,
            "version": ver,
            "updated_at": updated_at,
            "counts": counts,
            "unresolved": unresolved,
        }

    # Current (possibly with unresolved)
    n_proc = counts["extracted"]
    base = f"Current data · Version {ver}"
    if counts["all_extracted"]:
        base += f" · All {n_proc} vendor responses processed"
    elif n_proc:
        base += f" · {n_proc} of {counts['with_files']} responses processed"
    if unresolved["excluded_from_totals"]:
        # Cell-level wording — same SSOT headline as the Compare/Award exclusion banner
        return {
            "kind": "current_unresolved",
            "level": "info",
            "label": "Current with unresolved items",
            "text": f"{base} · {unresolved['headline']}",
            "version": ver,
            "updated_at": updated_at,
            "counts": counts,
            "unresolved": unresolved,
        }
    return {
        "kind": "current",
        "level": "ok",
        "label": "Current",
        "text": base,
        "version": ver,
        "updated_at": updated_at,
        "counts": counts,
        "unresolved": unresolved,
    }


def format_updated(iso: str) -> str:
    if not iso:
        return ""
    try:
        # Prefer a compact UTC label matching the required copy style
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return iso[:16].replace("T", " ") + " UTC"


def attach_answer_metadata(state: dict, result: dict, snapshot: dict) -> dict:
    """Stamp an analyst answer with version/snapshot fields."""
    result = dict(result)
    result["id"] = result.get("id") or _short_id()
    result["calculation_snapshot_id"] = snapshot["id"]
    result["vendor_data_version"] = snapshot["vendor_data_version"]
    result["input_hash"] = snapshot["input_hash"]
    result["status"] = "current"
    result.pop("stale_reason", None)
    result.pop("legacy", None)
    result["at"] = result.get("at") or _now()
    return result


def can_save_recommendation(state: dict, answer: dict) -> tuple[bool, str]:
    """Allow save only if the answer is based on the current vendor-data version."""
    ensure_snapshot_fields(state)
    refresh_staleness(state)
    cur = current_version(state)
    if answer.get("status") == "stale" or answer.get("legacy"):
        return (
            False,
            "This answer was generated before the latest vendor data was processed. "
            "Rerun the question before saving it as an award recommendation.",
        )
    vdv = answer.get("vendor_data_version")
    if vdv is None:
        return (
            False,
            "This answer was created before calculation snapshots were enabled and must be regenerated.",
        )
    if vdv != cur:
        return (
            False,
            "This answer was generated before the latest vendor data was processed. "
            "Rerun the question before saving it as an award recommendation.",
        )
    # Also refuse while processing
    counts = processing_counts(state)
    if counts["extracting"]:
        return (
            False,
            "A vendor response is still being extracted. Wait for processing to finish, then rerun before saving.",
        )
    return True, ""


def save_recommendation_from_answer(state: dict, answer: dict, chat_index: int) -> dict:
    ok, msg = can_save_recommendation(state, answer)
    if not ok:
        raise ValueError(msg)

    # Mark prior current recommendations superseded
    for rec in state.get("recommendations", []):
        if rec.get("status") == "current":
            rec["status"] = "superseded"

    # Pull totals from the answer's calculation snapshot if present
    snap_id = answer.get("calculation_snapshot_id")
    snap = next((s for s in state.get("calculation_snapshots", []) if s["id"] == snap_id), None)
    total_value = None
    covered = None
    allocation = None
    if snap and isinstance(snap.get("result"), dict):
        res = snap["result"]
        total_value = res.get("total_extended_inr") or res.get("total_value")
        covered = res.get("covered_line_count")
        allocation = res.get("share_by_vendor") or res.get("vendor_allocation")
    # Fallback: live split for metadata only (version already matches)
    if total_value is None and any(v.get("extraction") for v in state.get("vendors", [])):
        cmp = engine.build_comparison(state)
        cp = engine.cheapest_per_line(cmp, None, False, False)
        total_value = cp["total_extended_inr"]
        covered = cmp["line_count"] - len(cp["uncovered_lines"])
        allocation = cp["share_by_vendor"]

    rec = {
        "id": _short_id(),
        "rfx_id": state["id"],
        "analyst_answer_id": answer.get("id"),
        "chat_index": chat_index,
        "calculation_snapshot_id": snap_id,
        "vendor_data_version": answer.get("vendor_data_version"),
        "question": answer.get("question", ""),
        "recommendation_markdown": answer.get("answer", ""),
        "answer": answer.get("answer", ""),  # alias for older templates
        "tables": answer.get("tables", []),
        "caveats": answer.get("caveats", []),
        "total_value": total_value,
        "covered_line_count": covered,
        "vendor_allocation": allocation,
        "created_at": _now(),
        "saved_at": _now(),
        "status": "current",
    }
    state.setdefault("recommendations", []).append(rec)
    state["recommendation"] = rec
    state["status"] = "awarded"
    return rec


def export_meta(state: dict, snapshot: dict | None = None, provisional: bool = False) -> dict:
    counts = processing_counts(state)
    unresolved = unresolved_summary(state)
    snap = snapshot
    if snap is None:
        # Prefer latest current comparison / award_scenario snapshot
        for s in reversed(state.get("calculation_snapshots", [])):
            if s.get("status") == "current" and s.get("calculation_type") in ("comparison", "award_scenario", "export", "analyst_answer"):
                snap = s
                break
    ver = current_version(state)
    return {
        "rfx_id": state["id"],
        "export_timestamp": _now(),
        "vendor_data_version": ver,
        "calculation_snapshot_id": (snap or {}).get("id"),
        "export_status": "provisional" if provisional or counts["processing"] else ("historical" if snap and snap.get("status") != "current" else "current"),
        "vendors_processed": counts["extracted"],
        "vendors_still_processing": counts["processing"],
        "vendors_failed": counts["failed"],
        "needs_review": unresolved["needs_review"],
        "unresolved": unresolved["unresolved"],
        "provisional": bool(provisional or counts["processing"]),
    }


def live_award_calculation(state: dict) -> dict:
    """Current live award calculation with snapshot metadata (deterministic).

    Default strategy: quality-gated cheapest-per-line. Assumed / conversion-failed
    / not-quoted cells never enter totals. Compare / Ask / Award all read this.
    Conditional discounts stay out of the official total until buyer confirms.
    """
    ensure_snapshot_fields(state)
    if not any(v.get("extraction") for v in state.get("vendors", [])):
        return {"available": False}

    # Local imports avoid cycles (awardability/gates import engine only)
    from . import awardability, gates, scenario

    cmp = awardability.enrich_state_comparison(state)
    g = cmp["gates"]
    eligible = gates.gate_filter_vendors(cmp, g, True)
    conf = state.get("discount_confirmations") or {}
    res = scenario.compute_award_scenario(
        cmp,
        strategy="quality_gated_cheapest",
        vendor_ids=eligible or [],
        require_quality_gate=True,
        discount_confirmations=conf,
        vendor_data_version=current_version(state),
    )
    split = {
        "eligible_vendors": res.eligible_vendors,
        "eligible_vendor_ids": res.eligible_vendor_ids,
        "rows": res.rows,
        "total_extended_inr": res.total_extended_inr,
        "total_extended_paise": res.total_extended_paise,
        "uncovered_lines": res.uncovered_lines,
        "share_by_vendor": res.share_by_vendor,
        "caveats": res.caveats,
        "allow_needs_review": False,
        "conditional_discounts": res.conditional_discounts,
        "readiness": res.readiness,
        "readiness_headline": res.readiness_headline,
    }

    params = {
        "strategy": "quality_gated_cheapest",
        "require_cleared_questionnaire": True,
        "allow_needs_review": False,
        "eligible_vendor_ids": eligible or [],
        "discount_confirmations": {
            vid: bool(meta.get("confirmed") if isinstance(meta, dict) else meta)
            for vid, meta in conf.items()
        },
    }
    snap = create_calculation_snapshot(
        state,
        "award_scenario",
        parameters=params,
        result={
            "total_extended_inr": split["total_extended_inr"],
            "total_extended_paise": res.total_extended_paise,
            "covered_line_count": res.covered_line_count,
            "uncovered_lines": split["uncovered_lines"],
            "share_by_vendor": split["share_by_vendor"],
            "eligible_vendors": split.get("eligible_vendors") or [],
            "exclusion_summary": cmp.get("exclusion_summary"),
            "gates_summary": g.get("summary"),
            "conditional_discounts": res.conditional_discounts,
        },
    )
    # Attach snapshot id onto the scenario result for downstream consumers
    res_dict = res.as_dict()
    res_dict["calculation_snapshot_id"] = snap["id"]
    return {
        "available": True,
        "snapshot": snap,
        "vendor_data_version": snap["vendor_data_version"],
        "strategy": "quality-gated cheapest per line",
        "total_extended_inr": split["total_extended_inr"],
        "total_extended_paise": res.total_extended_paise,
        "covered_line_count": res.covered_line_count,
        "uncovered_lines": split["uncovered_lines"],
        "share_by_vendor": split["share_by_vendor"],
        "split": split,
        "cmp": cmp,
        "gates": g,
        "blockers": awardability.blockers_panel(cmp),
        "exclusion_summary": cmp.get("exclusion_summary"),
        "created_at": snap["created_at"],
        "conditional_discounts": res.conditional_discounts,
        "scenario": res_dict,
        "readiness": res.readiness,
        "readiness_headline": res.readiness_headline,
    }




def context_for_analyst(state: dict, cmp: dict, snapshot: dict) -> str:
    """Deterministic context block the model must see (never stale extraction claims)."""
    counts = processing_counts(state)
    lines = [
        f"Sourcing event: {cmp['title']}. {cmp['line_count']} RFx lines.",
        f"Vendor data version: {snapshot['vendor_data_version']}.",
        f"Calculation snapshot id: {snapshot['id']}.",
        f"Input hash: {snapshot['input_hash']}.",
        f"Context timestamp (UTC): {snapshot['created_at']}.",
        f"Extraction progress: {counts['extracted']} extracted, {counts['processing']} still processing, {counts['failed']} failed, {counts['awaiting_reply']} awaiting reply.",
        "Vendors (id: name — extraction status; usable lines; questionnaire; freight):",
    ]
    for v in cmp["vendors"]:
        ext_state = v.get("status") or "unknown"
        if ext_state == "extracted":
            avail = "EXTRACTED"
        elif ext_state == "extracting":
            avail = "EXTRACTING (not yet available)"
        elif ext_state == "error":
            avail = "EXTRACTION FAILED"
        elif ext_state == "received":
            avail = "RECEIVED BUT NOT YET EXTRACTED"
        else:
            avail = "NOT EXTRACTED"
        lines.append(
            f"- {v['vendor_id']}: {v['name']} ({v.get('city') or ''}) — {avail}; "
            f"usable {v['usable']}/{cmp['line_count']}, needs_review {v['counts'].get('needs_review', 0)}, "
            f"unresolved {v['counts'].get('unresolved', 0)}, missing {v['counts'].get('missing', 0)}; "
            f"questionnaire {v['questionnaire']['overall']}; freight extra: {v['commercial'].get('freight_extra')}; "
            f"currency hint: {v.get('currency_hint')}"
        )
    lines.append(
        "Line items: "
        + "; ".join(f"L{ln['line_no']} {ln['board']} {ln['description'][:40]}" for ln in cmp["lines"])
    )
    lines.append(
        "IMPORTANT: Only describe a vendor as 'not extracted' if the status above says so. "
        "Never claim a vendor is missing when its status is EXTRACTED."
    )
    return "\n".join(lines)


def assert_context_current(state: dict, snapshot: dict, context_version: int) -> None:
    """Deterministic validation before accepting an analyst answer."""
    cur = current_version(state)
    if context_version != cur:
        raise RuntimeError(
            f"Analyst context vendorDataVersion {context_version} != current {cur}; discard and rerun."
        )
    if snapshot.get("vendor_data_version") != cur:
        raise RuntimeError(
            f"Snapshot version {snapshot.get('vendor_data_version')} != current {cur}; discard and rerun."
        )
    if snapshot.get("id") is None:
        raise RuntimeError("Missing calculation snapshot id")
