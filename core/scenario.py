"""Single source of truth for award-scenario maths (Phases A/B).

Money in the decision core is integer **paise** (1 INR = 100 paise).
Display helpers derive INR floats. Compare, Ask tools, Exceptions, Award,
freeze validation, and recommendation persistence all go through
`compute_award_scenario` — do not invent parallel calc paths.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import engine

# ---------------------------------------------------------------------------
# Recommendation lifecycle (Phase A.2)
# ---------------------------------------------------------------------------
REC_CALCULATED = "calculated"
REC_SAVED = "saved"
REC_STALE = "stale"
REC_FROZEN_COMPLETE = "frozen_complete"
REC_FROZEN_PARTIAL = "frozen_partial"

# ---------------------------------------------------------------------------
# Analyst readiness (Phase B.3)
# ---------------------------------------------------------------------------
READY_AWARD = "award_ready"
READY_CONDITIONAL = "conditional"
READY_COMMERCIAL_ONLY = "commercial_comparison_only"
READY_NOT_EXECUTABLE = "not_executable"

READY_HEADLINES = {
    READY_AWARD: "Award-ready — selected allocation has no blockers and full line coverage.",
    READY_CONDITIONAL: "Conditional — awardable with buyer acknowledgements (partial freeze).",
    READY_COMMERCIAL_ONLY: "Commercial comparison only — not an executable award recommendation.",
    READY_NOT_EXECUTABLE: "Not executable — selected-award blockers or incomplete allocation.",
}

# ---------------------------------------------------------------------------
# Exception classes (Phase B.1)
# ---------------------------------------------------------------------------
EXC_SELECTED_BLOCKER = "selected_award_blocker"
EXC_COVERAGE_GAP = "coverage_gap"
EXC_BUYER_ACK = "buyer_acknowledgement"
EXC_ALT_SOURCING = "alternative_sourcing_opportunity"
EXC_EXCLUDED_VENDOR = "excluded_vendor_issue"
EXC_INFO = "informational"


def inr_to_paise(v: float | int | None) -> int | None:
    if v is None:
        return None
    return int(round(float(v) * 100))


def paise_to_inr(p: int | None) -> float | None:
    if p is None:
        return None
    return round(p / 100.0, 2)


@dataclass
class CoverageLabel:
    """Market quote coverage vs scenario award coverage (Phase A.3)."""

    kind: str  # market_quote | scenario_award
    covered: int
    total: int
    gap_lines: list[int] = field(default_factory=list)
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            noun = "quoted" if self.kind == "market_quote" else "awarded"
            self.label = f"{self.covered}/{self.total} lines {noun}"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AwardScenarioResult:
    """Deterministic award-scenario contract (integer paise for money)."""

    strategy: str
    strategy_key: str
    vendor_data_version: int
    calculation_snapshot_id: str | None
    total_extended_paise: int
    covered_line_count: int
    line_count: int
    uncovered_lines: list[int]
    eligible_vendor_ids: list[str]
    eligible_vendors: list[str]
    rows: list[dict]
    share_by_vendor: dict
    market_quote_coverage: dict
    scenario_award_coverage: dict
    selected_award_blockers: list[dict]
    coverage_gaps: list[dict]
    buyer_acknowledgements_required: list[dict]
    alternative_sourcing: list[dict]
    excluded_vendor_issues: list[dict]
    informational: list[dict]
    caveats: list[str]
    conditional_discounts: dict
    readiness: str
    readiness_headline: str
    require_quality_gate: bool = True
    allow_needs_review: bool = False
    apply_conditional_discounts: bool = False
    discounts_confirmed: bool = False

    @property
    def total_extended_inr(self) -> float:
        return paise_to_inr(self.total_extended_paise) or 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total_extended_inr"] = self.total_extended_inr
        # Back-compat keys used by existing freeze / live_award_calculation
        d["total_extended_inr"] = self.total_extended_inr
        d["uncovered_lines"] = self.uncovered_lines
        d["share_by_vendor"] = {
            k: {
                "lines": v.get("lines", 0),
                "extended_inr": v.get("extended_inr", paise_to_inr(v.get("extended_paise"))),
                "extended_paise": v.get("extended_paise"),
            }
            for k, v in self.share_by_vendor.items()
        }
        return d


# ---------------------------------------------------------------------------
# Coverage helpers
# ---------------------------------------------------------------------------

def market_quote_coverage(cmp: dict) -> CoverageLabel:
    gaps: list[int] = []
    covered = 0
    usable = getattr(engine, "USABLE", {"ok", "converted", "reviewed"})
    for ln in cmp["lines"]:
        any_usable = False
        for v in cmp["vendors"]:
            cell = ln["cells"][v["vendor_id"]]
            if cell.get("excluded_blended_rate"):
                continue
            if cell.get("awardability") == "awardable":
                any_usable = True
                break
            if cell.get("unit_inr") is not None and cell.get("status") in usable:
                any_usable = True
                break
        if any_usable:
            covered += 1
        else:
            gaps.append(ln["line_no"])
    return CoverageLabel("market_quote", covered, cmp["line_count"], gaps)


def scenario_award_coverage(rows: list[dict], line_count: int) -> CoverageLabel:
    gaps = [r["line_no"] for r in rows if not r.get("winner_id")]
    covered = line_count - len(gaps)
    return CoverageLabel("scenario_award", covered, line_count, gaps)


# ---------------------------------------------------------------------------
# Exception classification
# ---------------------------------------------------------------------------

def classify_exception(
    *,
    kind: str,
    vendor_id: str | None,
    vendor_gate: str | None,
    eligible_vendor_ids: set[str],
    is_selected: bool,
    line_no: int | None = None,
) -> str:
    if kind in ("coverage_gap", "no_awardable_quote"):
        return EXC_COVERAGE_GAP
    if kind in ("freight_extra", "angled_photo", "fx", "per_kg", "low_confidence", "informational"):
        return EXC_INFO
    if kind in ("buyer_ack", "assumed_confirm", "partial_freeze_ack", "conditional_discount"):
        return EXC_BUYER_ACK
    if kind in ("alternative", "unrestricted_cheaper"):
        return EXC_ALT_SOURCING
    if vendor_id and vendor_id not in eligible_vendor_ids and not is_selected:
        return EXC_EXCLUDED_VENDOR
    if vendor_gate in ("Fail", "Partial", "Fail") and vendor_id and vendor_id not in eligible_vendor_ids:
        return EXC_EXCLUDED_VENDOR
    if is_selected or kind in (
        "assumed",
        "needs_review",
        "conversion_failed",
        "unresolved",
        "blended_rate",
    ):
        return EXC_SELECTED_BLOCKER
    if str(kind).startswith("gate"):
        if vendor_id and vendor_id not in eligible_vendor_ids:
            return EXC_EXCLUDED_VENDOR
        return EXC_SELECTED_BLOCKER
    return EXC_INFO


def _cell_selected_blocker(cell: dict, line_no: int, vendor: dict) -> dict | None:
    a = cell.get("awardability") or ""
    if a in ("assumed", "needs_review", "conversion_failed", "unresolved"):
        return {
            "class": EXC_SELECTED_BLOCKER,
            "kind": a,
            "line_no": line_no,
            "vendor_id": vendor["vendor_id"],
            "vendor": vendor["name"],
            "label": cell.get("awardability_label") or a,
            "detail": cell.get("buyer_note") or cell.get("reason") or a,
            "blocks_freeze": True,
        }
    if cell.get("excluded_blended_rate"):
        return {
            "class": EXC_SELECTED_BLOCKER,
            "kind": "blended_rate",
            "line_no": line_no,
            "vendor_id": vendor["vendor_id"],
            "vendor": vendor["name"],
            "label": "Blended rate across dissimilar specs",
            "detail": cell.get("buyer_note")
            or "Identical rate across materially different specs — excluded from totals.",
            "blocks_freeze": True,
        }
    return None


# ---------------------------------------------------------------------------
# Conditional discounts (Phase A.4) — Kraftline 5% stays out until confirmed
# ---------------------------------------------------------------------------

def _conditional_discount_info(cmp: dict, share: dict, apply: bool) -> dict:
    potential = 0
    applied = 0
    details = []
    share_after = {k: dict(v) for k, v in share.items()}
    for name, s in share.items():
        v = next((x for x in cmp["vendors"] if x["name"] == name), None)
        if not v:
            continue
        commercial = v.get("commercial") or {}
        pct = commercial.get("discount_pct")
        cond = commercial.get("discount_condition") or ""
        if not pct:
            continue
        base = s.get("extended_paise")
        if base is None:
            base = inr_to_paise(s.get("extended_inr") or 0) or 0
        saving = int(round(base * float(pct) / 100.0))
        potential += saving
        details.append(
            {
                "vendor": name,
                "vendor_id": v["vendor_id"],
                "pct": float(pct),
                "condition": cond,
                "potential_saving_paise": saving,
                "potential_saving_inr": paise_to_inr(saving),
                "confirmed": bool(apply),
            }
        )
        if apply:
            applied += saving
            share_after[name]["extended_paise"] = base - saving
            share_after[name]["extended_inr"] = paise_to_inr(base - saving)
    total_before = sum((s.get("extended_paise") or 0) for s in share.values())
    return {
        "potential_paise": potential,
        "potential_inr": paise_to_inr(potential) or 0.0,
        "applied_paise": applied if apply else 0,
        "applied_inr": (paise_to_inr(applied) or 0.0) if apply else 0.0,
        "total_before_paise": total_before,
        "total_after_paise": total_before - (applied if apply else 0),
        "share_after": share_after if apply else share,
        "details": details,
        "summary": (
            "; ".join(f"{d['vendor']} {d['pct']:g}% ({d['condition']})" for d in details)
            if details
            else "No conditional discounts"
        ),
    }


def _readiness(
    *,
    selected_blockers: list,
    coverage_gaps: list,
    acks: list,
    covered: int,
    total: int,
    eligible_ids: list,
) -> tuple[str, str]:
    if not eligible_ids:
        return READY_COMMERCIAL_ONLY, READY_HEADLINES[READY_COMMERCIAL_ONLY]
    if selected_blockers:
        return READY_NOT_EXECUTABLE, READY_HEADLINES[READY_NOT_EXECUTABLE]
    if coverage_gaps or covered < total:
        if acks:
            return READY_CONDITIONAL, READY_HEADLINES[READY_CONDITIONAL]
        return READY_NOT_EXECUTABLE, READY_HEADLINES[READY_NOT_EXECUTABLE]
    if acks:
        return READY_CONDITIONAL, READY_HEADLINES[READY_CONDITIONAL]
    return READY_AWARD, READY_HEADLINES[READY_AWARD]


def _rows_from_cheapest(raw: dict, cmp: dict) -> tuple:
    rows_out = []
    total_paise = 0
    for r in raw.get("rows") or []:
        unit = r.get("unit_inr")
        up = inr_to_paise(unit)
        qty = r.get("annual_qty")
        if up is not None and qty is not None:
            ep = int(round(up * qty))
        else:
            ep = inr_to_paise(r.get("extended_inr"))
        if ep is not None:
            total_paise += ep
        rows_out.append(
            {
                **r,
                "unit_paise": up,
                "extended_paise": ep,
                "extended_inr": paise_to_inr(ep) if ep is not None else r.get("extended_inr"),
                "runner_up_unit_paise": inr_to_paise(r.get("runner_up_unit_inr")),
                "runner_up": r.get("runner_up"),
                "gap_pct": r.get("gap_pct"),
            }
        )
    share: dict[str, dict] = {}
    for r in rows_out:
        if not r.get("winner"):
            continue
        s = share.setdefault(r["winner"], {"lines": 0, "extended_paise": 0, "extended_inr": 0.0})
        s["lines"] += 1
        s["extended_paise"] += r.get("extended_paise") or 0
        s["extended_inr"] = paise_to_inr(s["extended_paise"])
    eligible_names = list(raw.get("eligible_vendors") or [])
    eligible_ids = [v["vendor_id"] for v in cmp["vendors"] if v["name"] in eligible_names]
    if not eligible_ids:
        eligible_ids = list(raw.get("eligible_vendor_ids") or [])
    return (
        rows_out,
        total_paise,
        share,
        list(raw.get("uncovered_lines") or []),
        eligible_names,
        eligible_ids,
        list(raw.get("caveats") or []),
    )


# ---------------------------------------------------------------------------
# Canonical compute
# ---------------------------------------------------------------------------

def compute_award_scenario(
    cmp: dict,
    *,
    strategy: str = "quality_gated_cheapest",
    vendor_ids: list[str] | None = None,
    require_cleared_questionnaire: bool = False,
    allow_needs_review: bool = False,
    max_vendors: int | None = None,
    apply_conditional_discounts: bool = False,
    discounts_confirmed: bool = False,
    vendor_data_version: int = 0,
    calculation_snapshot_id: str | None = None,
    require_quality_gate: bool = False,
) -> AwardScenarioResult:
    """Canonical award-scenario calculation for Compare / Ask / Award / freeze."""
    apply_disc = bool(discounts_confirmed or apply_conditional_discounts)

    cheapest = getattr(engine, "cheapest_per_line", None) or getattr(engine, "cheapest_per_line")
    award_fn = getattr(engine, "award_scenario", None) or getattr(engine, "award_scenario", None)

    if strategy == "single_vendor" and award_fn:
        raw = award_fn(
            cmp,
            strategy="single_vendor",
            vendor_ids=vendor_ids,
            require_cleared_questionnaire=require_cleared_questionnaire,
            allow_needs_review=allow_needs_review,
            apply_conditional_discounts=False,
        )
        best = (raw.get("options") or [None])[0]
        rows: list[dict] = []
        uncovered = list(best["uncovered_lines"]) if best else [ln["line_no"] for ln in cmp["lines"]]
        total_paise = 0
        share: dict[str, dict] = {}
        if best:
            for ln in cmp["lines"]:
                if ln["line_no"] in uncovered:
                    rows.append(_empty_row(ln))
                    continue
                cell = ln["cells"][best["vendor_id"]]
                unit = cell.get("unit_inr")
                up = inr_to_paise(unit)
                ep = int(round(up * ln["annual_qty"])) if up is not None else None
                if ep is not None:
                    total_paise += ep
                rows.append(
                    {
                        "line_no": ln["line_no"],
                        "description": ln["description"],
                        "annual_qty": ln["annual_qty"],
                        "winner": best.get("vendor") or best.get("name"),
                        "winner_id": best["vendor_id"],
                        "unit_inr": unit,
                        "unit_paise": up,
                        "extended_inr": paise_to_inr(ep),
                        "extended_paise": ep,
                        "runner_up": None,
                        "runner_up_unit_inr": None,
                        "runner_up_unit_paise": None,
                        "gap_pct": None,
                    }
                )
            vname = best.get("vendor") or best.get("name")
            share[vname] = {
                "lines": best.get("lines_covered") or sum(1 for r in rows if r.get("winner_id")),
                "extended_inr": best.get("total_inr") or paise_to_inr(total_paise),
                "extended_paise": inr_to_paise(best.get("total_inr")) or total_paise,
            }
        eligible_names = list(raw.get("eligible_vendors") or [])
        eligible_ids = [v["vendor_id"] for v in cmp["vendors"] if v["name"] in eligible_names]
        caveats = list(raw.get("caveats") or [])
    elif strategy == "split_max_n" and award_fn and max_vendors:
        raw = award_fn(
            cmp,
            strategy="split_max_n",
            vendor_ids=vendor_ids,
            require_cleared_questionnaire=require_cleared_questionnaire,
            allow_needs_review=allow_needs_review,
            max_vendors=max_vendors,
            apply_conditional_discounts=False,
        )
        rows, total_paise, share, uncovered, eligible_names, eligible_ids, caveats = _rows_from_cheapest(raw, cmp)
        if raw.get("chosen_vendors"):
            eligible_names = list(raw["chosen_vendors"])
            eligible_ids = [v["vendor_id"] for v in cmp["vendors"] if v["name"] in eligible_names]
    else:
        raw = cheapest(
            cmp,
            vendor_ids,
            require_cleared_questionnaire,
            allow_needs_review,
        )
        rows, total_paise, share, uncovered, eligible_names, eligible_ids, caveats = _rows_from_cheapest(raw, cmp)

    disc_info = _conditional_discount_info(cmp, share, apply_disc)
    if disc_info["applied_paise"] and apply_disc:
        total_paise = disc_info["total_after_paise"]
        share = disc_info["share_after"]
        caveats = list(caveats) + [
            "Conditional discounts confirmed by buyer and applied to the official total."
        ]
    elif disc_info["potential_paise"]:
        caveats = list(caveats) + [
            f"Conditional discounts available (potential −₹{disc_info['potential_inr']:,.2f}) "
            "— held out of the official total until buyer confirms."
        ]

    mkt = market_quote_coverage(cmp)
    scen = scenario_award_coverage(rows, cmp["line_count"])

    selected_blockers: list[dict] = []
    for r in rows:
        if not r.get("winner_id"):
            continue
        cell = next(
            (ln["cells"][r["winner_id"]] for ln in cmp["lines"] if ln["line_no"] == r["line_no"]),
            {},
        )
        vendor = next(v for v in cmp["vendors"] if v["vendor_id"] == r["winner_id"])
        b = _cell_selected_blocker(cell, r["line_no"], vendor)
        if b:
            selected_blockers.append(b)

    coverage_gap_items = [
        {
            "class": EXC_COVERAGE_GAP,
            "kind": "coverage_gap",
            "line_no": ln,
            "vendor_id": None,
            "vendor": None,
            "label": "No awardable quote",
            "detail": "No usable quote on this line under the current scenario.",
            "blocks_freeze": True,
        }
        for ln in scen.gap_lines
    ]

    eligible_set = set(eligible_ids)
    excluded_issues: list[dict] = []
    informational: list[dict] = []
    alt: list[dict] = []
    for b in cmp.get("blockers") or []:
        vid = b.get("vendor_id")
        is_sel = any(
            r.get("winner_id") == vid and r.get("line_no") == b.get("line_no") for r in rows
        )
        gate = None
        if vid:
            gate = next((v.get("gate") for v in cmp["vendors"] if v["vendor_id"] == vid), None)
        cls = classify_exception(
            kind=b.get("kind") or "needs_review",
            vendor_id=vid,
            vendor_gate=gate,
            eligible_vendor_ids=eligible_set,
            is_selected=is_sel,
            line_no=b.get("line_no"),
        )
        item = {**b, "class": cls, "blocks_freeze": cls in (EXC_SELECTED_BLOCKER, EXC_COVERAGE_GAP)}
        if cls == EXC_EXCLUDED_VENDOR:
            excluded_issues.append(item)
        elif cls == EXC_INFO:
            informational.append(item)
        elif cls == EXC_ALT_SOURCING:
            alt.append(item)

    acks: list[dict] = []
    for r in rows:
        if not r.get("winner_id"):
            continue
        cell = next(
            (ln["cells"][r["winner_id"]] for ln in cmp["lines"] if ln["line_no"] == r["line_no"]),
            {},
        )
        if cell.get("awardability") == "assumed":
            acks.append(
                {
                    "class": EXC_BUYER_ACK,
                    "kind": "assumed_confirm",
                    "line_no": r["line_no"],
                    "vendor_id": r["winner_id"],
                    "label": "Confirm assumed cell",
                    "detail": cell.get("buyer_note") or "Assumed value requires buyer confirm for freeze.",
                    "blocks_complete_freeze": True,
                }
            )
    if disc_info["potential_paise"] and not apply_disc:
        acks.append(
            {
                "class": EXC_BUYER_ACK,
                "kind": "conditional_discount",
                "line_no": None,
                "vendor_id": None,
                "label": "Conditional discount not confirmed",
                "detail": disc_info["summary"],
                "blocks_complete_freeze": False,
            }
        )

    readiness, headline = _readiness(
        selected_blockers=selected_blockers,
        coverage_gaps=coverage_gap_items,
        acks=acks,
        covered=scen.covered,
        total=scen.total,
        eligible_ids=eligible_ids,
    )

    strategy_label = {
        "quality_gated_cheapest": "quality-gated cheapest per line",
        "split_cheapest": "cheapest per line (all vendors)",
        "single_vendor": "single vendor",
        "split_max_n": "split limited to N vendors",
    }.get(strategy, strategy)

    return AwardScenarioResult(
        strategy=strategy_label,
        strategy_key=strategy,
        vendor_data_version=vendor_data_version,
        calculation_snapshot_id=calculation_snapshot_id,
        total_extended_paise=int(total_paise),
        covered_line_count=scen.covered,
        line_count=cmp["line_count"],
        uncovered_lines=list(uncovered),
        eligible_vendor_ids=eligible_ids,
        eligible_vendors=eligible_names,
        rows=rows,
        share_by_vendor=share,
        market_quote_coverage=mkt.as_dict(),
        scenario_award_coverage=scen.as_dict(),
        selected_award_blockers=selected_blockers,
        coverage_gaps=coverage_gap_items,
        buyer_acknowledgements_required=acks,
        alternative_sourcing=alt,
        excluded_vendor_issues=excluded_issues,
        informational=informational,
        caveats=caveats,
        conditional_discounts=disc_info,
        readiness=readiness,
        readiness_headline=headline,
        require_quality_gate=require_quality_gate or require_cleared_questionnaire,
        allow_needs_review=allow_needs_review,
        apply_conditional_discounts=apply_disc,
        discounts_confirmed=apply_disc,
    )


def _empty_row(ln: dict) -> dict:
    return {
        "line_no": ln["line_no"],
        "description": ln["description"],
        "annual_qty": ln["annual_qty"],
        "winner": None,
        "winner_id": None,
        "unit_inr": None,
        "unit_paise": None,
        "extended_inr": None,
        "extended_paise": None,
        "runner_up": None,
        "runner_up_unit_inr": None,
        "runner_up_unit_paise": None,
        "gap_pct": None,
    }


# ---------------------------------------------------------------------------
# Blended-rate detector (Phase D.1)
# ---------------------------------------------------------------------------

def detect_blended_rates(cmp: dict, min_lines: int = 4) -> list[dict]:
    """Identical source/normalised rate on N≥4 lines with material spec diffs."""
    flags: list[dict] = []
    for v in cmp["vendors"]:
        vid = v["vendor_id"]
        buckets: dict[tuple, list] = {}
        for ln in cmp["lines"]:
            cell = ln["cells"].get(vid) or {}
            unit = cell.get("unit_inr")
            if unit is None:
                continue
            raw = cell.get("raw_price")
            key = (round(float(unit), 4), None if raw is None else round(float(raw), 4))
            buckets.setdefault(key, []).append(ln)
        priced_line_count = sum(1 for lines in buckets.values() for _ in lines)
        for key, lines in buckets.items():
            if len(lines) < min_lines:
                continue
            # Flat catalog price (every priced line same rate) is not "blended subset" —
            # tests and honest list pricing use one rate across the board. Flag only when
            # the identical-rate cluster is a proper subset of the vendor's priced lines.
            if len(lines) >= priced_line_count:
                continue
            flutes = {(ln.get("flute") or "") for ln in lines}
            boards = {(ln.get("board") or "") for ln in lines}
            gsms = {(ln.get("gsm") or 0) for ln in lines}
            vols = []
            for ln in lines:
                L = ln.get("length_mm") or 0
                W = ln.get("width_mm") or 0
                H = ln.get("height_mm") or 0
                vols.append(max(L * W * H, 1))
            vol_spread = (max(vols) - min(vols)) / max(min(vols), 1) if vols else 0
            # Material difference: flute OR gsm OR (board ply change) OR >25% blank volume.
            # Volume-only cycling on the same flute/gsm/board is ignored to avoid false
            # positives on patterned demo price lists; PakAsia blended seed hits flute/gsm.
            flute_diff = len(flutes - {""}) > 1
            gsm_diff = len(gsms - {0}) > 1
            board_diff = len(boards - {""}) > 1
            diverse = flute_diff or gsm_diff or board_diff or vol_spread > 0.25
            # Prefer flute/gsm/board; allow volume-only only when N is large (>=8)
            if not (flute_diff or gsm_diff or board_diff) and not (vol_spread > 0.25 and len(lines) >= 8):
                continue
            if not diverse:
                continue
            line_nos = [ln["line_no"] for ln in lines]
            flags.append(
                {
                    "vendor_id": vid,
                    "vendor": v["name"],
                    "unit_inr": key[0],
                    "raw_price": key[1],
                    "line_nos": line_nos,
                    "line_count": len(line_nos),
                    "reason": (
                        f"Identical rate ₹{key[0]:g}/pc across {len(line_nos)} lines with material "
                        f"spec differences (flute/gsm/volume). Excluded from totals pending review."
                    ),
                }
            )
    return flags


def apply_blended_rate_exclusions(cmp: dict) -> list[dict]:
    """Mark blended-rate cells excluded from totals; mutate cmp cells."""
    flags = detect_blended_rates(cmp)
    usable = getattr(engine, "USABLE", {"ok", "converted", "reviewed"})
    for fl in flags:
        for ln in cmp["lines"]:
            if ln["line_no"] not in fl["line_nos"]:
                continue
            cell = ln["cells"][fl["vendor_id"]]
            cell["excluded_blended_rate"] = True
            cell["flags"] = list(set(cell.get("flags") or []) | {"blended_rate"})
            if cell.get("awardability") == "awardable" or cell.get("status") in usable:
                cell["awardability"] = "needs_review"
                cell["awardability_label"] = "Blended rate — excluded"
                cell["may_freeze"] = False
                cell["buyer_note"] = fl["reason"]
                cell["include_in_totals"] = False
                if cell.get("status") in usable:
                    cell["status"] = "needs_review"
                    cell["reason"] = fl["reason"]
    if flags:
        cmp["blended_rate_flags"] = flags
    return flags


# ---------------------------------------------------------------------------
# Recommendation lifecycle + narrative guard
# ---------------------------------------------------------------------------

def recommendation_lifecycle(state: dict) -> dict:
    from . import snapshots

    ensure = getattr(snapshots, "ensure_snapshot_fields", None) or getattr(
        snapshots, "ensure_snapshot_fields", None
    )
    if ensure:
        ensure(state)
    cur_fn = getattr(snapshots, "current_version", None) or getattr(snapshots, "current_version", None)
    cur = cur_fn(state) if cur_fn else state.get("vendor_data_version") or 0

    recs = state.get("recommendations") or []
    current = next(
        (r for r in recs if r.get("status") in ("current", REC_SAVED, "saved")),
        None,
    )
    stale_rec = next(
        (r for r in recs if r.get("status") in (REC_STALE, "stale")),
        None,
    )
    freeze = state.get("freeze") or {}
    if freeze.get("status") == "frozen":
        mode = freeze.get("freeze_mode") or "complete"
        life = REC_FROZEN_COMPLETE if mode == "complete" else REC_FROZEN_PARTIAL
        return {
            "lifecycle": life,
            "banner": None,
            "can_freeze": False,
            "freeze_blocked_reason": "Award already frozen.",
            "current": current or freeze,
            "unsaved_banner_shown": bool(state.get("_unsaved_rec_banner_shown")),
        }

    ver_key = "vendor_data_version"
    if current and current.get(ver_key) == cur and current.get("status") in ("current", REC_SAVED, "saved"):
        return {
            "lifecycle": REC_SAVED,
            "banner": None,
            "can_freeze": True,
            "freeze_blocked_reason": None,
            "current": current,
            "unsaved_banner_shown": bool(state.get("_unsaved_rec_banner_shown")),
        }
    if stale_rec or (
        current
        and current.get(ver_key) is not None
        and current.get(ver_key) != cur
    ):
        return {
            "lifecycle": REC_STALE,
            "banner": "Saved recommendation is stale — vendor data changed. Rerun and save again before freeze.",
            "can_freeze": False,
            "freeze_blocked_reason": "Save a current recommendation before freezing.",
            "current": current or stale_rec,
            "unsaved_banner_shown": True,
        }

    banner = None
    if not state.get("_unsaved_rec_banner_shown"):
        banner = "Live calculation is not saved as a recommendation yet. Save with a rationale before freeze."
        state["_unsaved_rec_banner_shown"] = True
    return {
        "lifecycle": REC_CALCULATED,
        "banner": banner,
        "can_freeze": False,
        "freeze_blocked_reason": "Save the current calculation as a recommendation (rationale required) before freeze.",
        "current": None,
        "unsaved_banner_shown": True,
    }


def validate_narrative_vs_engine(answer_text: str, result: AwardScenarioResult) -> dict:
    """On contradiction, return structured deterministic fallback (Phase B.3)."""
    import re

    text = answer_text or ""
    problems: list[str] = []
    official = result.total_extended_inr
    amounts = []
    for m in re.finditer(r"₹\s*([\d,]+(?:\.\d+)?)", text):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    if amounts and official:
        large = [a for a in amounts if a > official * 0.5]
        if large and not any(abs(a - official) / max(official, 1) < 0.05 for a in large):
            problems.append("narrative_total_mismatch")
    if result.readiness == READY_NOT_EXECUTABLE and re.search(
        r"\b(award ready|ready to award|recommend awarding)\b", text, re.I
    ):
        problems.append("readiness_contradiction")
    if result.readiness == READY_COMMERCIAL_ONLY and re.search(
        r"\b(freeze|execute the award|place the PO)\b", text, re.I
    ):
        problems.append("commercial_only_overclaim")

    if not problems:
        return {"ok": True, "problems": [], "fallback": None}

    return {
        "ok": False,
        "problems": problems,
        "fallback": {
            "headline": result.readiness_headline,
            "readiness": result.readiness,
            "total_extended_inr": result.total_extended_inr,
            "total_extended_paise": result.total_extended_paise,
            "scenario_award_coverage": result.scenario_award_coverage,
            "market_quote_coverage": result.market_quote_coverage,
            "selected_award_blockers": result.selected_award_blockers[:10],
            "coverage_gaps": result.coverage_gaps[:10],
            "share_by_vendor": result.share_by_vendor,
            "caveats": result.caveats,
            "note": "Analyst narrative contradicted the engine — showing deterministic fallback.",
            "problems": problems,
        },
    }


def append_buyer_review_log(state: dict, entry: dict) -> dict:
    """Unified buyer review log (evidence override, exceptions override, approvals)."""
    from datetime import datetime, timezone

    state.setdefault("buyer_review_log", [])
    state.setdefault("reviews", [])
    row = {
        "at": entry.get("at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": entry.get("source") or "unknown",  # evidence | exceptions | approval
        "vendor_id": entry.get("vendor_id"),
        "vendor_name": entry.get("vendor_name"),
        "line_no": entry.get("line_no"),
        "action": entry.get("action") or "override",
        "value_inr": entry.get("value_inr"),
        "note": entry.get("note") or "",
        "exception_key": entry.get("exception_key"),
    }
    state["buyer_review_log"].append(row)
    # Keep reviews in sync so Compare totals re-enter
    if row["vendor_id"] and row["line_no"] is not None:
        state["reviews"] = [
            r
            for r in state["reviews"]
            if not (r.get("vendor_id") == row["vendor_id"] and r.get("line_no") == row["line_no"])
        ]
        state["reviews"].append(
            {
                "vendor_id": row["vendor_id"],
                "vendor_name": row["vendor_name"],
                "line_no": row["line_no"],
                "action": row["action"],
                "value_inr": row["value_inr"],
                "note": row["note"],
                "at": row["at"],
                "via": row["source"],
            }
        )
    return row


def get_current_award_readiness(state: dict, *, require_quality_gate: bool = True) -> dict:
    """Analyst-facing readiness for current saved recommendation / live calc (Phase B)."""
    from . import freeze

    life = recommendation_lifecycle(state)
    try:
        check = freeze.validate_freeze_request(
            state,
            mode="complete",
            confirm_assumed=False,
            acknowledgements=[],
            partial_reason="",
            require_quality_gate=require_quality_gate,
        )
        scen = check.get("scenario") or {}
    except Exception as e:
        return {
            "lifecycle": life,
            "readiness": READY_NOT_EXECUTABLE,
            "headline": str(e),
            "can_freeze_complete": False,
            "can_freeze_partial": life.get("can_freeze", False),
            "errors": [str(e)],
        }
    readiness = scen.get("readiness") or READY_NOT_EXECUTABLE
    return {
        "lifecycle": life,
        "readiness": readiness,
        "headline": scen.get("readiness_headline") or life.get("banner"),
        "can_freeze_complete": bool(check.get("ok")),
        "can_freeze_partial": bool(life.get("can_freeze")),
        "errors": check.get("errors") or [],
        "scenario": scen,
    }
