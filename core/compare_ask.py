"""Compare Ask premades: engine-first, version-bound cache (same style as award_ask).

Premade clicks never wait on Claude. Free-form Compare Ask stays on analyst.ask.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import award_ask, awardability, engine, snapshots

# Short labels for chips; full question text stored on the answer.
COMPARE_PREMADES: list[dict[str, str]] = [
    {
        "id": "quality_gated_split",
        "label": "Quality-gated cheapest per line?",
        "question": (
            "What if we split it, cheapest per line, but only among vendors who "
            "cleared the quality questionnaire?"
        ),
    },
    {
        "id": "like_for_like",
        "label": "Cheapest overall like-for-like?",
        "question": (
            "Who is cheapest overall on a like-for-like basis, and what is "
            "excluded from that comparison?"
        ),
    },
    {
        "id": "usd_fx",
        "label": "USD quotes & 3% FX swing",
        "question": (
            "Which vendor quoted in USD and what exchange rate did you use? "
            "How much does a 3% weaker rupee change the ranking?"
        ),
    },
    {
        "id": "coverage_gaps",
        "label": "Lines with no usable quote",
        "question": (
            "Which lines have no usable quote from anyone, and what would I "
            "need to clarify to fix that?"
        ),
    },
    {
        "id": "two_suppliers",
        "label": "Best two-supplier split",
        "question": (
            "If we want at most two suppliers, which two and what does it cost "
            "versus the full split?"
        ),
    },
    {
        "id": "needs_review_delta",
        "label": "Include needs-review cells?",
        "question": (
            "If we include needs-review / assumed cells, what changes versus "
            "the quality-gated award?"
        ),
    },
]

_CACHE_KEY = "compare_ask_cache"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def premade_by_id(prompt_id: str) -> dict[str, str] | None:
    for p in COMPARE_PREMADES:
        if p["id"] == prompt_id:
            return p
    return None


def _fmt_inr(v: float | int | None) -> str:
    if v is None:
        return "—"
    try:
        return engine.fmt_inr(float(v))
    except Exception:
        return str(v)


def _cmp(state: dict) -> dict:
    return awardability.enrich_state_comparison(state)


def _table(title: str, columns: list[str], rows: list[list[Any]]) -> dict:
    return {"title": title, "columns": columns, "rows": rows}


def _narrative_quality_gated(state: dict, cmp: dict) -> tuple[str, list[dict], list[str]]:
    # Reuse Award engine narrative when available; fall back to direct engine call.
    try:
        built = award_ask.compute_premade(state, "best_split")
        md = built.get("answer") or ""
        tables = list(built.get("tables") or [])
        caveats = list(built.get("caveats") or [])
        return md, tables, caveats
    except Exception:
        pass
    cp = engine.cheapest_per_line(cmp, None, True, False)
    share = cp.get("share_by_vendor") or {}
    bits = [
        f"- **{name}**: {s.get('lines')} line(s), {_fmt_inr(s.get('extended_inr'))}"
        for name, s in share.items()
    ] or ["- No Pass-vendor winners yet."]
    uncovered = cp.get("uncovered_lines") or []
    note = (
        f" Uncovered: line(s) {', '.join(str(x) for x in uncovered)}."
        if uncovered
        else " Full coverage among Pass vendors."
    )
    md = (
        "## Recommendation\n\n"
        "Quality-gated cheapest-per-line among vendors who cleared knockout quality checks.\n\n"
        f"**Official total:** {_fmt_inr(cp.get('total_extended_inr'))} / yr.{note}\n\n"
        "### Split\n\n"
        + "\n".join(bits)
        + "\n\n_Numbers from the engine (version-bound). Short template narrative — not a live model call._"
    )
    rows = [
        [name, s.get("lines"), s.get("extended_inr")]
        for name, s in share.items()
    ]
    tables = [_table("quality_gated_split", ["vendor", "lines", "extended_inr"], rows)]
    return md, tables, list(cp.get("caveats") or [])


def _narrative_like_for_like(cmp: dict) -> tuple[str, list[dict], list[str]]:
    vt = engine.vendor_totals(cmp, None, False, False)
    vendors = vt.get("vendors") or []
    ranked = sorted(
        vendors,
        key=lambda v: (
            v.get("total_on_common_lines_inr") is None,
            v.get("total_on_common_lines_inr") or 0,
        ),
    )
    common = vt.get("common_line_count")
    lines = []
    for i, v in enumerate(ranked, 1):
        tot = v.get("total_on_common_lines_inr")
        lines.append(
            f"{i}. **{v.get('vendor')}**: {_fmt_inr(tot)} on {common} common lines "
            f"(own coverage {v.get('lines_covered') or '—'} lines, "
            f"{_fmt_inr(v.get('total_on_covered_lines_inr'))})"
        )
    winner = ranked[0].get("vendor") if ranked else "—"
    excl = []
    for v in cmp.get("vendors") or []:
        c = v.get("counts") or {}
        bits = []
        if c.get("needs_review"):
            bits.append(f"{c['needs_review']} needs-review")
        if c.get("unresolved"):
            bits.append(f"{c['unresolved']} unresolved")
        if c.get("missing"):
            bits.append(f"{c['missing']} missing")
        if bits:
            excl.append(f"- **{v.get('name')}**: {', '.join(bits)} held out of like-for-like")
    md = (
        "## Like-for-like ranking\n\n"
        f"Compared on the **{common}** lines every listed vendor covered with a usable quote. "
        f"**Cheapest:** {winner}.\n\n"
        + "\n".join(lines)
        + "\n\n### Excluded from this comparison\n\n"
        + ("\n".join(excl) if excl else "- No major cell exclusions beyond the common-line intersection.")
        + "\n\n_Engine vendor_totals — not a live model call._"
    )
    rows = [
        [
            v.get("vendor"),
            v.get("total_on_common_lines_inr"),
            v.get("total_on_covered_lines_inr"),
            common,
        ]
        for v in ranked
    ]
    tables = [
        _table(
            "vendor_totals_like_for_like",
            ["vendor", "common_lines_inr", "own_coverage_inr", "common_line_count"],
            rows,
        )
    ]
    return md, tables, list(vt.get("caveats") or []) or engine.caveats_for(cmp, None, None, False)


def _narrative_usd_fx(cmp: dict) -> tuple[str, list[dict], list[str]]:
    fx = cmp.get("fx") or {}
    rates = fx.get("rates_to_inr") or {}
    usd_rate = rates.get("USD")
    usd_vendors = [
        v
        for v in (cmp.get("vendors") or [])
        if "USD" in str(v.get("currency_hint") or "").upper()
    ]
    # Also scan converted cells for USD currency
    if not usd_vendors:
        for v in cmp.get("vendors") or []:
            for ln in cmp.get("lines") or []:
                cell = (ln.get("cells") or {}).get(v["vendor_id"]) or {}
                if str(cell.get("currency") or "").upper() == "USD":
                    usd_vendors.append(v)
                    break
    names = [v.get("name") for v in usd_vendors] or ["—"]
    lines = [
        f"- **FX table:** ₹{usd_rate} per USD (as of {fx.get('as_of') or '—'}; "
        f"{fx.get('source') or 'buyer rate'}).",
        f"- **USD quoters:** {', '.join(names)}.",
    ]
    sens_rows: list[list[Any]] = []
    for v in usd_vendors:
        # Weaker rupee ≈ higher INR per USD ≈ vendor looks more expensive: +3% on their prices
        sens = engine.sensitivity(cmp, v["vendor_id"], 3.0, True, False)
        before = sens.get("total_before_inr")
        after = sens.get("total_after_inr")
        changed = sens.get("lines_that_change_winner") or []
        lines.append(
            f"- **+3% on {v.get('name')}** (weaker-rupee proxy): award total "
            f"{_fmt_inr(before)} → {_fmt_inr(after)}; "
            f"{len(changed)} line winner(s) change."
        )
        sens_rows.append(
            [v.get("name"), 3.0, before, after, len(changed)]
        )
    if not usd_vendors:
        lines.append("- No vendor currently carries a USD currency hint on the comparison.")
    md = (
        "## USD quotes and FX\n\n"
        + "\n".join(lines)
        + "\n\n_Engine fx_rates + sensitivity — not a live model call._"
    )
    tables = [
        _table(
            "fx_rates",
            ["currency", "to_inr"],
            [[k, v] for k, v in rates.items()],
        )
    ]
    if sens_rows:
        tables.append(
            _table(
                "fx_sensitivity_plus_3pct",
                ["vendor", "pct_change", "total_before_inr", "total_after_inr", "lines_changed"],
                sens_rows,
            )
        )
    return md, tables, []


def _narrative_coverage(cmp: dict) -> tuple[str, list[dict], list[str]]:
    # Lines with no usable quote from anyone (all vendors missing/unresolved/needs_review)
    gap_rows: list[list[Any]] = []
    sole_rows: list[list[Any]] = []
    for ln in cmp.get("lines") or []:
        usable = []
        for v in cmp.get("vendors") or []:
            cell = (ln.get("cells") or {}).get(v["vendor_id"]) or {}
            st = cell.get("status")
            if st in ("ok", "converted", "reviewed") and cell.get("unit_inr") is not None:
                usable.append(v.get("name"))
        if not usable:
            gap_rows.append([ln.get("line_no"), ln.get("description"), "no usable quote"])
        elif len(usable) == 1:
            sole_rows.append([ln.get("line_no"), ln.get("description"), usable[0]])
    flags = engine.list_flags(cmp, None, ["unresolved", "missing", "needs_review"])
    clarify = []
    for item in (flags.get("items") or [])[:12]:
        clarify.append(
            f"- L{item.get('line_no')} / {item.get('vendor')} ({item.get('status')}): "
            f"{item.get('reason') or 'clarify source'}"
        )
    md = (
        "## Coverage gaps\n\n"
        f"**{len(gap_rows)}** line(s) have no usable quote from anyone; "
        f"**{len(sole_rows)}** line(s) have only one usable quote.\n\n"
    )
    if gap_rows:
        md += "### No usable quote\n\n" + "\n".join(
            f"- **L{r[0]}** — {r[1] or '—'}" for r in gap_rows
        ) + "\n\n"
    if sole_rows:
        md += "### Single usable quote\n\n" + "\n".join(
            f"- **L{r[0]}** — only {r[2]}" for r in sole_rows[:15]
        ) + ("\n\n" if len(sole_rows) <= 15 else f"\n- …and {len(sole_rows) - 15} more\n\n")
    if clarify:
        md += "### Clarifications that would help\n\n" + "\n".join(clarify) + "\n\n"
    md += "_Engine coverage scan — not a live model call._"
    tables = [
        _table("no_usable_quote", ["line_no", "description", "reason"], gap_rows),
        _table("sole_usable_quote", ["line_no", "description", "vendor"], sole_rows),
    ]
    return md, tables, []


def _narrative_two_suppliers(cmp: dict) -> tuple[str, list[dict], list[str]]:
    full = engine.award_scenario(cmp, "split_cheapest", None, True, False, None)
    limited = engine.award_scenario(cmp, "split_max_n", None, True, False, 2)
    full_share = full.get("share_by_vendor") or {}
    lim_share = limited.get("share_by_vendor") or {}
    full_total = full.get("total_extended_inr")
    lim_total = limited.get("total_extended_inr")
    delta = None
    if full_total is not None and lim_total is not None:
        delta = lim_total - full_total
    two = ", ".join(lim_share.keys()) or "—"
    md = (
        "## At most two suppliers\n\n"
        f"**Recommended pair (quality-gated):** {two}.\n\n"
        f"- Full Pass split total: {_fmt_inr(full_total)}\n"
        f"- Two-supplier total: {_fmt_inr(lim_total)}\n"
        f"- Premium vs full split: {_fmt_inr(delta)}\n\n"
        "### Two-supplier share\n\n"
        + "\n".join(
            f"- **{n}**: {s.get('lines')} line(s), {_fmt_inr(s.get('extended_inr'))}"
            for n, s in lim_share.items()
        )
        + "\n\n_Engine award_scenario(split_max_n=2) — not a live model call._"
    )
    tables = [
        _table(
            "full_vs_two_supplier",
            ["scenario", "total_extended_inr", "vendors"],
            [
                ["full_pass_split", full_total, len(full_share)],
                ["max_two", lim_total, len(lim_share)],
            ],
        ),
        _table(
            "two_supplier_share",
            ["vendor", "lines", "extended_inr"],
            [[n, s.get("lines"), s.get("extended_inr")] for n, s in lim_share.items()],
        ),
    ]
    return md, tables, list(limited.get("caveats") or [])


def _narrative_needs_review(cmp: dict) -> tuple[str, list[dict], list[str]]:
    base = engine.cheapest_per_line(cmp, None, True, False)
    with_nr = engine.cheapest_per_line(cmp, None, True, True)
    flags = engine.list_flags(cmp, None, ["needs_review"])
    n_nr = flags.get("count") or 0
    base_total = base.get("total_extended_inr")
    nr_total = with_nr.get("total_extended_inr")
    delta = None
    if base_total is not None and nr_total is not None:
        delta = nr_total - base_total
    base_share = set((base.get("share_by_vendor") or {}).keys())
    nr_share = set((with_nr.get("share_by_vendor") or {}).keys())
    md = (
        "## Including needs-review cells\n\n"
        f"**{n_nr}** needs-review cell(s) are held out of the default quality-gated award.\n\n"
        f"- Default (exclude NR): {_fmt_inr(base_total)}\n"
        f"- Include NR: {_fmt_inr(nr_total)}\n"
        f"- Delta: {_fmt_inr(delta)}\n"
        f"- Vendors in default share: {', '.join(sorted(base_share)) or '—'}\n"
        f"- Vendors if NR included: {', '.join(sorted(nr_share)) or '—'}\n\n"
        "_Engine cheapest_per_line ± allow_needs_review — not a live model call._"
    )
    tables = [
        _table(
            "needs_review_delta",
            ["mode", "total_extended_inr", "uncovered_count"],
            [
                ["exclude_nr", base_total, len(base.get("uncovered_lines") or [])],
                ["include_nr", nr_total, len(with_nr.get("uncovered_lines") or [])],
            ],
        ),
        _table(
            "needs_review_cells",
            ["line_no", "vendor", "reason"],
            [
                [i.get("line_no"), i.get("vendor"), i.get("reason")]
                for i in (flags.get("items") or [])[:40]
            ],
        ),
    ]
    return md, tables, engine.caveats_for(cmp, None, None, True)


_NARRATORS = {
    "quality_gated_split": lambda state, cmp: _narrative_quality_gated(state, cmp),
    "like_for_like": lambda state, cmp: _narrative_like_for_like(cmp),
    "usd_fx": lambda state, cmp: _narrative_usd_fx(cmp),
    "coverage_gaps": lambda state, cmp: _narrative_coverage(cmp),
    "two_suppliers": lambda state, cmp: _narrative_two_suppliers(cmp),
    "needs_review_delta": lambda state, cmp: _narrative_needs_review(cmp),
}


def compute_premade(state: dict, prompt_id: str) -> dict:
    """Engine-first Compare premade answer (no Claude). Suitable to cache."""
    meta = premade_by_id(prompt_id)
    if not meta:
        raise ValueError(f"Unknown compare premade prompt: {prompt_id}")
    if not any(v.get("extraction") for v in state.get("vendors", [])):
        raise ValueError("Extract at least one vendor response before asking.")

    live = snapshots.live_award_calculation(state)
    if not live.get("available"):
        raise ValueError("No calculation available yet.")
    cmp = live.get("cmp") or _cmp(state)
    snap = live.get("snapshot") or {}
    vdv = live.get("vendor_data_version")

    answer_md, tables, caveats = _NARRATORS[prompt_id](state, cmp)
    raw = {
        "id": f"compare-premade-{prompt_id}-v{vdv}",
        "question": meta["question"],
        "answer": answer_md,
        "trace": [
            {
                "tool": "compare_ask_premade",
                "input": {"prompt_id": prompt_id, "vendor_data_version": vdv},
                "ok": True,
                "output_preview": "engine-first template narrative",
            }
        ],
        "tables": tables,
        "charts": [],
        "caveats": caveats or [],
        "source": "compare_ask_premade",
        "premade_id": prompt_id,
        "award_shaped": prompt_id in ("quality_gated_split", "two_suppliers"),
        "at": _now(),
        "status": "current",
    }
    return snapshots.attach_answer_metadata(state, raw, snap)


def get_or_build_premade(state: dict, prompt_id: str) -> dict:
    """Return cached Compare premade for current vendor_data_version, or compute + store."""
    snapshots.ensure_snapshot_fields(state)
    vdv = snapshots.current_version(state)
    cache = state.setdefault(_CACHE_KEY, {})
    for k in [k for k in list(cache.keys()) if not str(k).endswith(f":v{vdv}")]:
        cache.pop(k, None)

    key = f"{prompt_id}:v{vdv}"
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("answer") and hit.get("vendor_data_version") == vdv:
        out = dict(hit)
        out["cache_hit"] = True
        return out

    built = compute_premade(state, prompt_id)
    built["cache_hit"] = False
    cache[key] = {k: v for k, v in built.items() if k != "cache_hit"}
    return built
