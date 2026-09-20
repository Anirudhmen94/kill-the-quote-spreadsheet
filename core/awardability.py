"""Awardability model: what a buyer may put into a frozen award.

States (per quote cell):
  awardable         — usable quote, evidence-backed, may enter freeze
  assumed           — a number exists only because of a soft assumption
                      (currency assumed, ambiguous line map with candidate).
                      NEVER enters a frozen award without explicit confirm.
  needs_review      — ambiguous / alternate / unverified, but NOT a soft
                      assumption. Excluded until the buyer reviews.
  unresolved        — refers to data we do not have ("same as last year")
  not_quoted        — vendor did not quote the line
  conversion_failed — quoted but could not be normalised to INR/pc

Failed conversions and unresolved references never silently enter totals.
Assumed and needs-review cells stay visible with loud chips and are
excluded from freeze unless confirmed / reviewed.
"""
from __future__ import annotations

from . import engine
from .gates import evaluate_gates

ASSUMED_FLAGS = {"currency_assumed", "pack_size_unknown", "weight_unknown", "basis_unknown"}

# Buyer-facing labels for exclusion headline parts (only non-zero parts shown).
EXCLUSION_LABELS = {
    "assumed": "assumed",
    "needs_review": "needs review",
    "unresolved": "unresolved",
    "conversion_failed": "conversion failed",
    "not_quoted": "not quoted",
}


def cell_awardability(cell: dict) -> dict:
    """Classify one comparison cell."""
    status = cell.get("status") or "missing"
    flags = set(cell.get("flags") or [])
    unit = cell.get("unit_inr")
    reason = (cell.get("reason") or "").strip()
    conversion = (cell.get("conversion") or "").strip()

    if status in ("missing", "not_extracted"):
        return {
            "awardability": "not_quoted",
            "include_in_totals": False,
            "may_freeze": False,
            "label": "Not quoted",
            "buyer_note": reason or "Vendor did not quote this line.",
        }

    if status == "unresolved":
        return {
            "awardability": "unresolved",
            "include_in_totals": False,
            "may_freeze": False,
            "label": "Unresolved",
            "buyer_note": reason or "Refers to data we do not have (e.g. 'same as last year').",
        }

    if status == "needs_review":
        # Soft assumption / confirmable candidate → assumed
        candidate = cell.get("unit_inr_candidate")
        if candidate is not None or (flags & ASSUMED_FLAGS):
            return {
                "awardability": "assumed",
                "include_in_totals": False,
                "may_freeze": False,
                "label": "Assumed — confirm to use",
                "buyer_note": reason or conversion or "Value rests on an assumption; confirm before award.",
            }
        if unit is None and candidate is None:
            return {
                "awardability": "conversion_failed",
                "include_in_totals": False,
                "may_freeze": False,
                "label": "Conversion failed",
                "buyer_note": reason or conversion or "Quoted but could not convert to INR per piece.",
            }
        # Ambiguous / alternate / unverified — not an assumption
        return {
            "awardability": "needs_review",
            "include_in_totals": False,
            "may_freeze": False,
            "label": "Needs review",
            "buyer_note": reason or "Ambiguous or unverified — excluded until reviewed.",
        }

    if status in engine.USABLE:
        # Soft assumptions that still produced a usable converted cell
        if flags & {"currency_assumed"} and status == "converted":
            # currency_assumed downgrades to needs_review in normalize; if we still
            # see it on a usable cell, treat as assumed.
            return {
                "awardability": "assumed",
                "include_in_totals": False,
                "may_freeze": False,
                "label": "Currency assumed",
                "buyer_note": conversion or "Currency was assumed, not stated.",
            }
        return {
            "awardability": "awardable",
            "include_in_totals": True,
            "may_freeze": True,
            "label": "Awardable" if status != "reviewed" else "Awardable (buyer-reviewed)",
            "buyer_note": conversion or reason or "",
        }

    return {
        "awardability": "conversion_failed",
        "include_in_totals": False,
        "may_freeze": False,
        "label": status.replace("_", " "),
        "buyer_note": reason or conversion or "",
    }


def _headline(counts: dict, excluded_n: int, coverage_gaps: list) -> str:
    """Build exclusion headline from non-zero awardability buckets only."""
    parts = []
    for key in ("assumed", "needs_review", "unresolved", "conversion_failed", "not_quoted"):
        n = counts.get(key, 0)
        if n:
            parts.append(f"{n} {EXCLUSION_LABELS[key]}")
    detail = ", ".join(parts) if parts else "none"
    return (
        f"{excluded_n} cells excluded from totals ({detail}) · "
        f"{len(coverage_gaps)} lines with no awardable quote"
    )


def annotate_comparison(cmp: dict) -> dict:
    """Attach awardability to every cell; return exclusion SSOT + blockers."""
    blockers: list[dict] = []
    counts = {
        "awardable": 0,
        "assumed": 0,
        "needs_review": 0,
        "unresolved": 0,
        "not_quoted": 0,
        "conversion_failed": 0,
    }
    exclusions: list[dict] = []

    for ln in cmp["lines"]:
        for v in cmp["vendors"]:
            cell = ln["cells"][v["vendor_id"]]
            a = cell_awardability(cell)
            cell["awardability"] = a["awardability"]
            cell["awardability_label"] = a["label"]
            cell["may_freeze"] = a["may_freeze"]
            cell["buyer_note"] = a["buyer_note"]
            counts[a["awardability"]] = counts.get(a["awardability"], 0) + 1
            if not a["include_in_totals"]:
                exclusions.append(
                    {
                        "line_no": ln["line_no"],
                        "vendor_id": v["vendor_id"],
                        "vendor": v["name"],
                        "awardability": a["awardability"],
                        "status": cell.get("status"),
                        "reason": a["buyer_note"],
                        "flags": list(cell.get("flags") or []),
                    }
                )
            if a["awardability"] in ("assumed", "needs_review", "conversion_failed", "unresolved"):
                blockers.append(
                    {
                        "kind": a["awardability"],
                        "line_no": ln["line_no"],
                        "vendor_id": v["vendor_id"],
                        "vendor": v["name"],
                        "label": a["label"],
                        "detail": a["buyer_note"],
                        "sku": ln.get("sku"),
                        "description": ln.get("description"),
                    }
                )
            elif a["awardability"] == "not_quoted" and cell.get("status") == "missing":
                # Only surface not-quoted as blockers when it creates coverage gaps
                # (no usable quote on the line at all) — filled below.
                pass

    # Coverage gaps: lines with zero awardable quotes
    coverage_gaps = []
    for ln in cmp["lines"]:
        awardable_n = sum(
            1
            for v in cmp["vendors"]
            if ln["cells"][v["vendor_id"]].get("awardability") == "awardable"
        )
        if awardable_n == 0:
            coverage_gaps.append(ln["line_no"])
            blockers.append(
                {
                    "kind": "coverage_gap",
                    "line_no": ln["line_no"],
                    "vendor_id": None,
                    "vendor": None,
                    "label": "No awardable quote",
                    "detail": "Every vendor is missing, unresolved, assumed, needs review, or failed conversion on this line.",
                    "sku": ln.get("sku"),
                    "description": ln.get("description"),
                }
            )

    cmp["awardability_counts"] = counts
    cmp["exclusions"] = exclusions
    cmp["blockers"] = blockers
    cmp["coverage_gaps"] = coverage_gaps
    cmp["exclusion_summary"] = {
        "excluded_cells": len(exclusions),
        "assumed": counts.get("assumed", 0),
        "needs_review": counts.get("needs_review", 0),
        "unresolved": counts.get("unresolved", 0),
        "not_quoted": counts.get("not_quoted", 0),
        "conversion_failed": counts.get("conversion_failed", 0),
        "coverage_gaps": len(coverage_gaps),
        "headline": _headline(counts, len(exclusions), coverage_gaps),
    }
    return cmp


def enrich_state_comparison(state: dict) -> dict:
    """Build comparison + gates + awardability in one place (SSOT for UI)."""
    cmp = engine.build_comparison(state)
    gates = evaluate_gates(state)
    # Attach gate grade onto vendor rows
    by_id = {g["vendor_id"]: g for g in gates["vendors"]}
    for v in cmp["vendors"]:
        g = by_id.get(v["vendor_id"], {})
        v["gate"] = g.get("grade", "Partial")
        v["gate_reason"] = g.get("reason", "")
        v["gate_eligible"] = g.get("eligible_for_quality_gated_award", False)
    annotate_comparison(cmp)
    cmp["gates"] = gates
    return cmp


def blockers_panel(cmp: dict, limit: int = 40) -> dict:
    """Accurate blocker counts for the Award/Compare panel."""
    blockers = cmp.get("blockers") or []
    by_kind: dict[str, int] = {}
    for b in blockers:
        by_kind[b["kind"]] = by_kind.get(b["kind"], 0) + 1
    return {
        "total": len(blockers),
        "by_kind": by_kind,
        "entries": blockers[:limit],
        "truncated": len(blockers) > limit,
    }
