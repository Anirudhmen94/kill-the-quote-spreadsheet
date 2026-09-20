"""Buyer-facing one-liners for ugly edges on Compare / Inbox / Award.

These are first-class callouts, not footnotes — the brief evaluates what we
show when we are not sure.
"""
from __future__ import annotations


def vendor_coverage_parts(v: dict, total: int) -> dict:
    """Coherent coverage math that always adds up to `total` lines.

    usable + needs_review + unresolved + not_quoted (+ not_extracted) == total
    quoted = usable + needs_review + unresolved  (== total - not_quoted - not_extracted)
    """
    counts = v.get("counts") or {}
    usable = int(v.get("usable") or 0)
    needs_review = int(counts.get("needs_review") or 0)
    unresolved = int(counts.get("unresolved") or 0)
    missing = int(counts.get("missing") or 0)
    not_extracted = int(counts.get("not_extracted") or 0)
    not_quoted = missing + not_extracted
    quoted = usable + needs_review + unresolved
    # Prefer engine's quoted when present; fall back to derived
    if v.get("quoted") is not None:
        quoted = int(v["quoted"])
    return {
        "usable": usable,
        "quoted": quoted,
        "needs_review": needs_review,
        "unresolved": unresolved,
        "not_quoted": not_quoted,
        "total": total,
        "sums_to_total": usable + needs_review + unresolved + not_quoted == total,
    }


def coverage_chip_text(name: str, parts: dict) -> str:
    """Buyer-facing partial-quote chip; parts always disclose the full 30."""
    bits = [
        f"{parts['usable']} usable",
        f"{parts['quoted']} quoted",
    ]
    if parts["needs_review"]:
        bits.append(f"{parts['needs_review']} needs review")
    if parts["unresolved"]:
        bits.append(f"{parts['unresolved']} unresolved")
    bits.append(f"{parts['not_quoted']} not quoted")
    return (
        f"{name}: {' / '.join(bits)} "
        f"(of {parts['total']}). Missing and review cells never enter totals."
    )


def _is_angled_photo_vendor(v: dict) -> bool:
    fmt = (v.get("format") or "").lower()
    notes = (v.get("notes") or "").lower()
    if fmt in ("image", "photo", "jpg", "jpeg", "png"):
        return True
    return any(tok in notes for tok in ("angled", "phone photo", "rate card", "ratecard"))


def edge_callouts(cmp: dict) -> list[dict]:
    """Loud, specific callouts derived from the comparison.

    Shared by Compare and Award so both screens show the same edges.
    """
    out: list[dict] = []
    for v in cmp.get("vendors") or []:
        counts = v.get("counts") or {}
        total = cmp.get("line_count") or 0
        name = v.get("name") or v.get("vendor_id")
        gate = v.get("gate") or ""
        parts = vendor_coverage_parts(v, total)

        if parts["not_quoted"] or parts["needs_review"] or parts["unresolved"]:
            if parts["usable"] < total:
                out.append(
                    {
                        "kind": "partial_quote",
                        "level": "warn",
                        "vendor": name,
                        "text": coverage_chip_text(name, parts),
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
        pack_sizes: set[int] = set()
        for ln in cmp.get("lines") or []:
            cell = ln["cells"].get(v["vendor_id"]) or {}
            for f in cell.get("flags") or []:
                if f in flag_hits:
                    flag_hits[f] += 1
            if cell.get("basis_qty") and "per_pack" in (cell.get("flags") or []):
                try:
                    pack_sizes.add(int(cell["basis_qty"]))
                except (TypeError, ValueError):
                    pass
            if (
                cell.get("confidence") is not None
                and cell["confidence"] < 0.65
                and cell.get("status") in ("ok", "converted", "needs_review")
            ):
                low_conf += 1

        angled = _is_angled_photo_vendor(v)

        if angled:
            pack_note = ""
            if pack_sizes:
                sizes = "/".join(str(s) for s in sorted(pack_sizes))
                pack_note = f" Per pack/box ÷{sizes} on converted cells."
            elif flag_hits["per_pack"]:
                pack_note = " Per pack/box divided by disclosed pack size."
            conf_note = (
                f" {low_conf} cell(s) under 65% confidence."
                if low_conf
                else ""
            )
            out.append(
                {
                    "kind": "angled_photo",
                    "level": "warn",
                    "vendor": name,
                    "text": (
                        f"{name}: source is an angled phone photo / rate card photo."
                        f"{pack_note}{conf_note} "
                        "Open a cell for evidence — do not treat these like a clean PDF."
                    ),
                }
            )

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
        if flag_hits["per_pack"] and not angled:
            # Angled-photo vendors already carry pack ÷N in the photo callout
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
        elif flag_hits["per_pack"] and angled:
            # Still surface the conversion count, with photo context
            sizes = "/".join(str(s) for s in sorted(pack_sizes)) if pack_sizes else "disclosed size"
            out.append(
                {
                    "kind": "per_pack",
                    "level": "info",
                    "vendor": name,
                    "text": (
                        f"{name}: {flag_hits['per_pack']} line(s) from angled phone / rate card photo "
                        f"quoted per pack/box → ÷{sizes}. Marked converted."
                    ),
                }
            )
        if low_conf and not angled:
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
        elif low_conf and angled:
            out.append(
                {
                    "kind": "low_confidence",
                    "level": "warn",
                    "vendor": name,
                    "text": (
                        f"{name}: {low_conf} cell(s) under 65% extraction confidence "
                        "from an angled phone photo / rate card photo. Click a cell for evidence."
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
