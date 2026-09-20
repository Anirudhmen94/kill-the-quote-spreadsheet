"""Buyer-facing one-liners for ugly edges on Compare / Inbox / Award.

These are first-class callouts, not footnotes — the brief evaluates what we
show when we are not sure.
"""
from __future__ import annotations


def edge_callouts(cmp: dict) -> list[dict]:
    """Loud, specific callouts derived from the comparison."""
    out: list[dict] = []
    for v in cmp.get("vendors") or []:
        counts = v.get("counts") or {}
        usable = v.get("usable") or 0
        total = cmp.get("line_count") or 0
        name = v.get("name") or v.get("vendor_id")
        gate = v.get("gate") or ""

        if counts.get("missing") and usable < total:
            out.append(
                {
                    "kind": "partial_quote",
                    "level": "warn",
                    "vendor": name,
                    "text": (
                        f"{name} quoted {usable}/{total} lines "
                        f"({counts.get('missing', 0)} not quoted). "
                        "Missing lines never enter totals."
                    ),
                }
            )

        if counts.get("unresolved"):
            out.append(
                {
                    "kind": "unresolved",
                    "level": "danger",
                    "vendor": name,
                    "text": (
                        f"{name}: {counts['unresolved']} unresolved cell(s) "
                        "(e.g. 'same as last year'). Shown, excluded, not invented."
                    ),
                }
            )

        if v.get("currency_hint") == "USD" or any(
            "fx" in (ln["cells"][v["vendor_id"]].get("flags") or [])
            for ln in cmp.get("lines") or []
            if v["vendor_id"] in ln.get("cells", {})
        ):
            # only once per vendor
            if not any(c["kind"] == "fx" and c["vendor"] == name for c in out):
                fx = cmp.get("fx") or {}
                rate = (fx.get("rates_to_inr") or {}).get("USD")
                out.append(
                    {
                        "kind": "fx",
                        "level": "info",
                        "vendor": name,
                        "text": (
                            f"{name} has USD quotes → converted at "
                            f"{rate:g} INR/USD as of {fx.get('as_of')} ({fx.get('source')}). "
                            "Conversion shown on every blue cell."
                        ),
                    }
                )

        # per-kg / per-pack chips
        flag_hits = {"per_kg": 0, "per_pack": 0, "per_100": 0, "per_1000": 0}
        low_conf = 0
        for ln in cmp.get("lines") or []:
            cell = ln["cells"].get(v["vendor_id"]) or {}
            for f in cell.get("flags") or []:
                if f in flag_hits:
                    flag_hits[f] += 1
            if cell.get("confidence") is not None and cell["confidence"] < 0.65 and cell.get("status") in ("ok", "converted", "needs_review"):
                low_conf += 1
        if flag_hits["per_kg"]:
            out.append(
                {
                    "kind": "per_kg",
                    "level": "info",
                    "vendor": name,
                    "text": (
                        f"{name}: {flag_hits['per_kg']} line(s) quoted per kg → "
                        "converted via RFx nominal blank weight (formula on the cell). Marked converted."
                    ),
                }
            )
        if flag_hits["per_pack"]:
            out.append(
                {
                    "kind": "per_pack",
                    "level": "info",
                    "vendor": name,
                    "text": (
                        f"{name}: {flag_hits['per_pack']} line(s) quoted per pack/box → "
                        "divided by disclosed pack size. Marked converted."
                    ),
                }
            )
        if low_conf:
            out.append(
                {
                    "kind": "low_confidence",
                    "level": "warn",
                    "vendor": name,
                    "text": (
                        f"{name}: {low_conf} cell(s) under 65% extraction confidence "
                        "(typical for photo / sparse email). Click a cell for evidence."
                    ),
                }
            )
        if gate == "Partial":
            out.append(
                {
                    "kind": "gate_partial",
                    "level": "warn",
                    "vendor": name,
                    "text": (
                        f"{name} did not clear the quality questionnaire ({v.get('gate_reason') or 'incomplete'}). "
                        "Excluded from the default quality-gated award."
                    ),
                }
            )
        if gate == "Fail":
            out.append(
                {
                    "kind": "gate_fail",
                    "level": "danger",
                    "vendor": name,
                    "text": f"{name} failed knockout(s). Excluded from quality-gated award.",
                }
            )
        if v.get("commercial", {}).get("freight_extra"):
            out.append(
                {
                    "kind": "freight",
                    "level": "warn",
                    "vendor": name,
                    "text": f"{name}: freight is EXTRA — not in unit prices, never assumed zero.",
                }
            )

    # Coverage gaps
    gaps = cmp.get("coverage_gaps") or []
    if gaps:
        out.append(
            {
                "kind": "coverage_gap",
                "level": "danger",
                "vendor": None,
                "text": (
                    f"{len(gaps)} line(s) have no awardable quote from anyone: "
                    f"{', '.join(str(x) for x in gaps[:12])}{'…' if len(gaps)>12 else ''}. "
                    "Draft a clarification before awarding."
                ),
            }
        )
    return out
