"""Quality-gate engine shared by Compare and Award.

A vendor's gate result is Pass / Partial / Fail. Cleared (Pass) means every
knockout questionnaire item has a clear pass. Partial means knockouts are
unanswered/unclear (incomplete). Fail means at least one knockout failed.

This is the single source of truth — Compare badges and Award eligibility
must both call `evaluate_gates`, never re-derive.
"""
from __future__ import annotations

from . import engine


def evaluate_vendor_gate(rfx: dict, extraction: dict | None) -> dict:
    """Return Pass/Partial/Fail for one vendor from the questionnaire."""
    q = engine.questionnaire_status(rfx, extraction)
    overall = q["overall"]
    if overall == "cleared":
        grade = "Pass"
    elif overall == "failed":
        grade = "Fail"
    elif overall in ("incomplete", "not_extracted"):
        grade = "Partial"
    else:
        grade = "Partial"
    return {
        "grade": grade,
        "overall": overall,
        "knockout_failed": list(q.get("knockout_failed") or []),
        "knockout_open": list(q.get("knockout_open") or []),
        "answered": q.get("answered", 0),
        "total": q.get("total", 0),
        "eligible_for_quality_gated_award": grade == "Pass",
        "reason": _reason(grade, q),
    }


def _reason(grade: str, q: dict) -> str:
    if grade == "Pass":
        return "All knockout questions cleared."
    if grade == "Fail":
        failed = ", ".join(q.get("knockout_failed") or []) or "knockout"
        return f"Failed knockout(s): {failed}."
    if q.get("overall") == "not_extracted":
        return "Questionnaire not yet extracted — treated as incomplete, not failed."
    open_q = ", ".join(q.get("knockout_open") or []) or "knockout items"
    return f"Incomplete knockouts ({open_q}) — excluded from quality-gated awards until answered."


def evaluate_gates(state: dict) -> dict:
    """Matrix of gate results for every vendor. Shared by Compare + Award."""
    rfx = state["rfx"]
    rows = []
    for v in state.get("vendors", []):
        gate = evaluate_vendor_gate(rfx, v.get("extraction"))
        rows.append(
            {
                "vendor_id": v["vendor_id"],
                "name": v.get("name", v["vendor_id"]),
                "status": v.get("status"),
                **gate,
            }
        )
    passed = [r for r in rows if r["grade"] == "Pass"]
    partial = [r for r in rows if r["grade"] == "Partial"]
    failed = [r for r in rows if r["grade"] == "Fail"]
    return {
        "vendors": rows,
        "pass_ids": [r["vendor_id"] for r in passed],
        "partial_ids": [r["vendor_id"] for r in partial],
        "fail_ids": [r["vendor_id"] for r in failed],
        "summary": {
            "pass": len(passed),
            "partial": len(partial),
            "fail": len(failed),
            "total": len(rows),
        },
    }


def gate_filter_vendors(cmp: dict, gates: dict, require_pass: bool) -> list[str] | None:
    """Vendor ids eligible under the current gate policy, or None for all."""
    if not require_pass:
        return None
    return list(gates.get("pass_ids") or [])
