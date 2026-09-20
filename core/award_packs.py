"""Award page helpers: per-vendor packs and deterministic 'why this vendor' reasons.

Does not change award maths, gates, or freeze rules — only groups existing
split/scenario/gate data for the slim Award UI.
"""
from __future__ import annotations

from typing import Any


def format_gap_pct(gap_pct: float | int | None) -> str | None:
    """Display gap as +X% (strip trailing zeros)."""
    if gap_pct is None:
        return None
    try:
        n = float(gap_pct)
    except (TypeError, ValueError):
        return None
    # Match engine's round(..., 1) display without forcing .0
    text = f"{n:g}"
    return f"+{text}%"


def line_why_reason(
    row: dict,
    *,
    gate_grade: str | None = None,
) -> str:
    """Deterministic one-line reason for why a vendor won a line.

    Built only from split row fields + the winner's gate grade.
    Example:
      Lowest usable INR/pc among Pass vendors · runner-up Sri Balaji Packaging +5.2% · gate Pass
    """
    grade = (gate_grade or "Pass").strip() or "Pass"
    base = f"Lowest usable INR/pc among {grade} vendors"
    runner = row.get("runner_up")
    if not runner or runner in ("—", "-"):
        mid = "sole usable quote"
    else:
        gap = format_gap_pct(row.get("gap_pct"))
        mid = f"runner-up {runner}" + (f" {gap}" if gap else "")
    return f"{base} · {mid} · gate {grade}"


def _vendor_gate_map(cmp: dict | None, gates: dict | None = None) -> dict[str, str]:
    """Map vendor_id → gate grade (Pass/Partial/Fail)."""
    out: dict[str, str] = {}
    if gates:
        for r in gates.get("rows") or []:
            vid = r.get("vendor_id")
            if vid:
                out[vid] = r.get("grade") or r.get("gate") or ""
    for v in (cmp or {}).get("vendors") or []:
        vid = v.get("vendor_id")
        if vid and vid not in out:
            out[vid] = v.get("gate") or ""
        # Also allow lookup by name when id missing on row
    return out


def _vendor_name_gate_map(cmp: dict | None, gates: dict | None = None) -> dict[str, str]:
    by_id = _vendor_gate_map(cmp, gates)
    out: dict[str, str] = {}
    for v in (cmp or {}).get("vendors") or []:
        name = v.get("name")
        if name:
            out[name] = by_id.get(v.get("vendor_id"), v.get("gate") or "")
    return out


def enrich_split_rows(split: dict | None, cmp: dict | None = None, gates: dict | None = None) -> list[dict]:
    """Return split rows with gap_display + why_reason attached (non-mutating copy)."""
    if not split:
        return []
    by_id = _vendor_gate_map(cmp, gates or (cmp or {}).get("gates"))
    enriched = []
    for r in split.get("rows") or []:
        row = dict(r)
        if not row.get("runner_up"):
            row["runner_up"] = "—"
            row["gap_pct"] = None
            row["gap_display"] = "—"
        elif row.get("gap_pct") is None:
            row["gap_display"] = "—"
        else:
            row["gap_display"] = f"{row['gap_pct']:g}%"
        grade = by_id.get(row.get("winner_id") or "") or None
        if row.get("winner_id"):
            row["gate_grade"] = grade or "Pass"
            row["why_reason"] = line_why_reason(row, gate_grade=row["gate_grade"])
        else:
            row["gate_grade"] = None
            row["why_reason"] = None
        enriched.append(row)
    return enriched


def vendor_award_packs(
    split: dict | None,
    *,
    cmp: dict | None = None,
    gates: dict | None = None,
    total_extended_inr: float | None = None,
) -> list[dict]:
    """Group awarded split rows into per-vendor packs for Award Step 2.

    Each pack: vendor_id, vendor, lines_won, extended_inr, share_pct, lines[{... why_reason}].
    Uncovered rows are omitted (call uncovered_lines_block separately).
    Order follows share_by_vendor (extended desc), then first-seen winner order.
    """
    if not split:
        return []
    rows = enrich_split_rows(split, cmp, gates or (cmp or {}).get("gates"))
    share = split.get("share_by_vendor") or {}
    total = total_extended_inr
    if total is None:
        total = split.get("total_extended_inr") or 0.0
    try:
        total_f = float(total or 0.0)
    except (TypeError, ValueError):
        total_f = 0.0

    packs: dict[str, dict] = {}
    order: list[str] = []

    # Prefer share_by_vendor order (typically insertion = win order); fall back to row order
    preferred = list(share.keys())
    for name in preferred:
        order.append(name)

    for row in rows:
        wid = row.get("winner_id")
        wname = row.get("winner")
        if not wid or not wname:
            continue
        if wname not in packs:
            if wname not in order:
                order.append(wname)
            s = share.get(wname) or {}
            ext = s.get("extended_inr")
            if ext is None:
                ext = 0.0
            lines_count = s.get("lines")
            share_pct = round((float(ext) / total_f) * 100, 1) if total_f > 0 else 0.0
            packs[wname] = {
                "vendor_id": wid,
                "vendor": wname,
                "lines_won": lines_count if lines_count is not None else 0,
                "extended_inr": float(ext),
                "share_pct": share_pct,
                "gate_grade": row.get("gate_grade") or "Pass",
                "lines": [],
            }
        packs[wname]["lines"].append(
            {
                "line_no": row["line_no"],
                "description": row.get("description") or "",
                "annual_qty": row.get("annual_qty"),
                "unit_inr": row.get("unit_inr"),
                "extended_inr": row.get("extended_inr"),
                "runner_up": row.get("runner_up"),
                "gap_pct": row.get("gap_pct"),
                "gap_display": row.get("gap_display"),
                "why_reason": row.get("why_reason"),
                "gate_grade": row.get("gate_grade"),
            }
        )

    # Recompute lines_won from actual lines if share missing
    result = []
    for name in order:
        pack = packs.get(name)
        if not pack:
            continue
        if not pack["lines_won"]:
            pack["lines_won"] = len(pack["lines"])
        if pack["extended_inr"] == 0.0 and pack["lines"]:
            pack["extended_inr"] = round(
                sum(float(ln.get("extended_inr") or 0) for ln in pack["lines"]), 2
            )
            if total_f > 0:
                pack["share_pct"] = round((pack["extended_inr"] / total_f) * 100, 1)
        # Stable line order
        pack["lines"].sort(key=lambda ln: ln["line_no"])
        result.append(pack)
    return result


def uncovered_line_rows(split: dict | None) -> list[dict]:
    """Rows with no usable winner — for the Uncovered lines block."""
    if not split:
        return []
    out = []
    for r in split.get("rows") or []:
        if r.get("winner_id"):
            continue
        out.append(
            {
                "line_no": r.get("line_no"),
                "description": r.get("description") or "",
                "annual_qty": r.get("annual_qty"),
                "reason": "No usable quote among Pass vendors",
            }
        )
    # Also surface uncovered_lines ids that might lack a row (defensive)
    seen = {x["line_no"] for x in out}
    for ln in split.get("uncovered_lines") or []:
        if ln not in seen:
            out.append(
                {
                    "line_no": ln,
                    "description": "",
                    "annual_qty": None,
                    "reason": "No usable quote among Pass vendors",
                }
            )
    out.sort(key=lambda x: x["line_no"] or 0)
    return out


def notice_previews(
    state: dict,
    *,
    freeze_pack: dict | None = None,
    live: dict | None = None,
    vendor_packs: list[dict] | None = None,
) -> dict[str, Any]:
    """Draft award/regret notice previews for Step 3.

    Prefer frozen pack notices when present; otherwise build from the current
    award proposal (same text as freeze would generate) and attach line lists
    from vendor_packs for display.
    """
    from . import freeze

    packs = vendor_packs or []
    lines_by_vendor = {p["vendor"]: [ln["line_no"] for ln in p["lines"]] for p in packs}

    if freeze_pack and (freeze_pack.get("notices") or freeze_pack.get("regrets")):
        notices = []
        for n in freeze_pack.get("notices") or []:
            notices.append(
                {
                    **n,
                    "line_nos": lines_by_vendor.get(n.get("vendor") or "", []),
                    "source": "freeze",
                }
            )
        regrets = []
        for n in freeze_pack.get("regrets") or []:
            regrets.append({**n, "line_nos": [], "source": "freeze"})
        return {
            "frozen": True,
            "notices": notices,
            "regrets": regrets,
            "freeze_id": freeze_pack.get("id"),
            "snapshot_id": freeze_pack.get("calculation_snapshot_id"),
        }

    # Live / not-yet-frozen preview
    try:
        proposal = freeze.build_award_proposal(state, require_quality_gate=True)
        notices_raw, regrets_raw = freeze._notices_and_regrets(proposal)
    except Exception:
        # Fallback: invent minimal stubs from share only (still deterministic fields)
        share = (live or {}).get("share_by_vendor") or (packs and {
            p["vendor"]: {"lines": p["lines_won"], "extended_inr": p["extended_inr"]} for p in packs
        }) or {}
        snap_id = ((live or {}).get("snapshot") or {}).get("id") or "—"
        strategy = (live or {}).get("strategy") or "quality-gated cheapest per line"
        notices_raw = []
        for name, s in share.items():
            if isinstance(s, dict):
                n_lines = s.get("lines", "?")
                ext = s.get("extended_inr")
            else:
                n_lines, ext = "?", None
            from . import engine
            notices_raw.append(
                {
                    "vendor": name,
                    "kind": "award_notice",
                    "subject": f"Award notice — {n_lines} line(s)"
                    + (f" · {engine.fmt_inr(ext)}" if ext is not None else ""),
                    "body": (
                        f"Subject to final commercial confirmation, we intend to award you "
                        f"{n_lines} line(s) under strategy: {strategy}. Snapshot {snap_id}."
                    ),
                }
            )
        winner_names = set(share.keys())
        regrets_raw = []
        for v in ((live or {}).get("cmp") or {}).get("vendors") or []:
            if v["name"] in winner_names:
                continue
            regrets_raw.append(
                {
                    "vendor": v["name"],
                    "kind": "regret",
                    "subject": f"Regret — {((live or {}).get('cmp') or {}).get('title') or 'RFx'}",
                    "body": f"Thank you for quoting. On this event we are not awarding lines to {v['name']}.",
                }
            )
        proposal = {"snapshot": {"id": snap_id}}

    notices = []
    for n in notices_raw:
        notices.append(
            {
                **n,
                "line_nos": lines_by_vendor.get(n.get("vendor") or "", []),
                "source": "live",
            }
        )
    regrets = [{**n, "line_nos": [], "source": "live"} for n in regrets_raw]
    return {
        "frozen": False,
        "notices": notices,
        "regrets": regrets,
        "freeze_id": None,
        "snapshot_id": (proposal.get("snapshot") or {}).get("id"),
    }


def unconfirmed_discounts(conditional_discounts: dict | None) -> list[dict]:
    """Details entries that still need buyer confirm — for the Step 1 strip."""
    if not conditional_discounts:
        return []
    return [d for d in (conditional_discounts.get("details") or []) if not d.get("confirmed")]
