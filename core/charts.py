"""Lightweight chart data helpers for Compare / Award — engine numbers only.

Bars are rendered in templates as CSS/HTML (Chart.js remains available for Ask).
All totals reconcile to share_by_vendor / coverage from the live award scenario.
"""
from __future__ import annotations

from typing import Any

from . import engine, freeze, snapshots


def _share_items(share: dict | None) -> list[dict]:
    """Normalise share_by_vendor into sorted list with pcts that sum to ~100."""
    items: list[dict] = []
    if not share:
        return items
    total_ext = 0.0
    total_lines = 0
    for name, s in share.items():
        if not isinstance(s, dict):
            continue
        lines = int(s.get("lines") or 0)
        ext = float(s.get("extended_inr") or 0)
        total_ext += ext
        total_lines += lines
        items.append({"vendor": name, "lines": lines, "extended_inr": ext})
    items.sort(key=lambda x: (-x["extended_inr"], -x["lines"], x["vendor"]))
    for it in items:
        it["extended_pct"] = round(100.0 * it["extended_inr"] / total_ext, 1) if total_ext else 0.0
        it["lines_pct"] = round(100.0 * it["lines"] / total_lines, 1) if total_lines else 0.0
    return items


def allocation_by_vendor(share_by_vendor: dict | None) -> dict:
    """Allocation chart: share of lines and extended ₹ by winning vendor."""
    items = _share_items(share_by_vendor)
    total_lines = sum(i["lines"] for i in items)
    total_ext = round(sum(i["extended_inr"] for i in items), 2)
    return {
        "title": "Allocation by vendor",
        "bars": items,
        "total_lines": total_lines,
        "total_extended_inr": total_ext,
        "empty": not items,
    }


def coverage_bars(
    *,
    covered: int,
    total: int,
    uncovered: int | None = None,
    label: str | None = None,
    market_label: str | None = None,
) -> dict:
    """Awarded vs uncovered line counts (scenario coverage)."""
    total = int(total or 0)
    covered = int(covered or 0)
    if uncovered is None:
        uncovered = max(0, total - covered)
    else:
        uncovered = int(uncovered)
    pct = round(100.0 * covered / total, 1) if total else 0.0
    return {
        "title": "Quality-gated scenario coverage",
        "covered": covered,
        "uncovered": uncovered,
        "total": total,
        "pct_covered": pct,
        "label": label or (f"{covered}/{total} lines awarded (quality-gated)" if total else "No lines"),
        "market_label": market_label,  # only when engine already exposed it
        "empty": total <= 0,
    }


def scenario_cost(
    *,
    total_extended_inr: float | None,
    strategy: str | None = None,
    market_label: str | None = None,
) -> dict:
    """Quality-gated split total; optional market-quote note when exposed."""
    total = float(total_extended_inr or 0)
    return {
        "title": "Scenario cost",
        "total_extended_inr": round(total, 2),
        "strategy": strategy or "quality-gated cheapest per line",
        "market_label": market_label,
        "empty": total_extended_inr is None,
    }


def caption(*, vendor_data_version: int | None, snapshot_id: str | None) -> str:
    bits = []
    if vendor_data_version is not None:
        bits.append(f"Vendor data v{vendor_data_version}")
    if snapshot_id:
        bits.append(f"Snapshot {snapshot_id}")
    return " · ".join(bits) if bits else "Live calculation"


def totals_consistent(allocation: dict, coverage: dict, cost: dict) -> bool:
    """Smoke check: allocation extended sum == scenario cost; lines match coverage."""
    if allocation.get("empty") and cost.get("empty"):
        return True
    alloc_ext = round(float(allocation.get("total_extended_inr") or 0), 2)
    cost_ext = round(float(cost.get("total_extended_inr") or 0), 2)
    if abs(alloc_ext - cost_ext) > 0.02:
        return False
    alloc_lines = int(allocation.get("total_lines") or 0)
    covered = int(coverage.get("covered") or 0)
    if alloc_lines != covered:
        return False
    return True


def build_chart_bundle(state: dict, live: dict | None = None) -> dict[str, Any]:
    """Build Compare/Award chart payload from live award calculation (engine only)."""
    snapshots.ensure_snapshot_fields(state)
    if live is None:
        if not any(v.get("extraction") for v in state.get("vendors", [])):
            return {"available": False}
        live = snapshots.live_award_calculation(state)
    if not live or not live.get("available"):
        return {"available": False}

    share = live.get("share_by_vendor") or (live.get("split") or {}).get("share_by_vendor") or {}
    scen = live.get("scenario") or {}
    award_cov = scen.get("scenario_award_coverage") or {}
    market_cov = scen.get("market_quote_coverage") or {}

    covered = live.get("covered_line_count")
    if covered is None:
        covered = award_cov.get("covered")
    total_lines = award_cov.get("total")
    if total_lines is None:
        cmp = live.get("cmp") or {}
        total_lines = cmp.get("line_count") or (covered or 0) + len(live.get("uncovered_lines") or [])
    uncovered_n = len(live.get("uncovered_lines") or [])
    if award_cov.get("gap_lines") is not None:
        uncovered_n = len(award_cov["gap_lines"])

    market_label = market_cov.get("label") if market_cov else None
    allocation = allocation_by_vendor(share)
    coverage = coverage_bars(
        covered=int(covered or 0),
        total=int(total_lines or 0),
        uncovered=uncovered_n,
        label=award_cov.get("label"),
        market_label=market_label,
    )
    cost = scenario_cost(
        total_extended_inr=live.get("total_extended_inr"),
        strategy=live.get("strategy"),
        market_label=market_label,
    )
    snap = live.get("snapshot") or {}
    return {
        "available": True,
        "allocation": allocation,
        "coverage": coverage,
        "cost": cost,
        "caption": caption(
            vendor_data_version=live.get("vendor_data_version"),
            snapshot_id=snap.get("id"),
        ),
        "vendor_data_version": live.get("vendor_data_version"),
        "snapshot_id": snap.get("id"),
        "consistent": totals_consistent(allocation, coverage, cost),
        "fmt_total": engine.fmt_inr(cost["total_extended_inr"]),
    }


def audit_trust_strip(state: dict) -> dict[str, Any]:
    """Short trust strip: last freeze, notices sent, buyer overrides + deep links."""
    snapshots.ensure_snapshot_fields(state)
    rid = state.get("id") or ""
    pack = freeze.current_freeze(state)
    # Prefer current frozen; else latest pack (may be historical)
    if pack is None:
        packs = state.get("freeze_packs") or []
        pack = packs[-1] if packs else None

    notices_sent = sum(
        1
        for m in state.get("outbox") or []
        if (m.get("kind") or "") in ("award_notice", "regret", "regret_notice")
    )
    override_actions = {"override", "accept", "approved"}
    reviews = state.get("reviews") or []
    log = state.get("buyer_review_log") or []
    overrides = sum(1 for r in reviews if (r.get("action") or "") in override_actions)
    if not overrides and log:
        overrides = sum(1 for r in log if (r.get("action") or "") in override_actions)

    from . import event_status

    freeze_label = "Not frozen"
    freeze_detail = None
    freeze_validity = None
    invalid_historical = None
    if pack:
        freeze_validity = event_status.freeze_validity(pack)
        fid = pack.get("id") or "—"
        mode = pack.get("freeze_mode") or ""
        covered = pack.get("covered_line_count")
        line_count = (pack.get("processing_completeness") or {}).get("line_count")
        uncovered = list(pack.get("uncovered_lines") or [])
        if freeze_validity in (
            event_status.VALIDITY_REQUIRES_REVIEW,
            event_status.VALIDITY_INVALID_HISTORICAL,
        ):
            freeze_label = "Freeze requires review"
            coverage_bits = ""
            if covered is not None and line_count:
                coverage_bits = f" · {covered}/{line_count} preserved for audit"
            freeze_detail = f"{fid}{coverage_bits}"
            invalid_historical = {
                "id": fid,
                "original_mode": mode or "complete",
                "covered_line_count": covered,
                "line_count": line_count,
                "uncovered_lines": uncovered,
                "frozen_at": pack.get("frozen_at"),
                "repaired_at": pack.get("reclassified_at") or pack.get("invalidated_at"),
                "integrity": pack.get("integrity"),
                "invalid_reason": pack.get("invalid_reason"),
                "status": pack.get("status"),
                "ai_log_href": f"/rfx/{rid}/ai-log",
            }
        elif event_status.is_active_valid_freeze(pack):
            if mode == "partial":
                freeze_label = f"Frozen partial · {fid}"
            else:
                freeze_label = f"Frozen complete · {fid}"
            freeze_detail = (
                f"v{pack.get('vendor_data_version')} · "
                f"snap {pack.get('calculation_snapshot_id') or '—'}"
            )
        else:
            status = pack.get("status") or "historical"
            freeze_label = f"Historical freeze · {fid}"
            if mode:
                freeze_label += f" · {mode}"
            freeze_detail = (
                f"v{pack.get('vendor_data_version')} · "
                f"snap {pack.get('calculation_snapshot_id') or '—'}"
            )

    return {
        "freeze_label": freeze_label,
        "freeze_detail": freeze_detail,
        "freeze_id": (pack or {}).get("id") if pack else None,
        "freeze_validity": freeze_validity,
        "invalid_historical": invalid_historical,
        "notices_sent": notices_sent,
        "buyer_overrides": overrides,
        "links": {
            "review": f"/rfx/{rid}/anomalies",
            "outbox": f"/rfx/{rid}/email#outbox",
            "ai_log": f"/rfx/{rid}/ai-log",
            "workbook": f"/rfx/{rid}/export.xlsx",
        },
    }
