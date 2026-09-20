"""Freeze an award into an immutable pack bound to a calculation snapshot.

Later vendor edits bump vendor_data_version; freeze is marked historical via
the existing snapshot invalidation patterns (status → stale/superseded).
Assumed cells never enter a frozen pack without explicit confirm.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timezone

from . import awardability, engine, export, gates, snapshots


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short() -> str:
    return uuid.uuid4().hex[:10]


def build_award_proposal(
    state: dict,
    *,
    require_quality_gate: bool = True,
    allow_needs_review: bool = False,
    confirm_assumed: bool = False,
    strategy: str = "quality_gated_cheapest",
) -> dict:
    """Deterministic award proposal used by Award page and freeze."""
    snapshots.ensure_snapshot_fields(state)
    cmp = awardability.enrich_state_comparison(state)
    g = cmp["gates"]

    vendor_ids = gates.gate_filter_vendors(cmp, g, require_quality_gate)
    if require_quality_gate and not vendor_ids:
        split = {
            "eligible_vendors": [],
            "rows": [],
            "total_extended_inr": 0.0,
            "uncovered_lines": [ln["line_no"] for ln in cmp["lines"]],
            "share_by_vendor": {},
            "caveats": [
                "No vendor cleared the quality questionnaire. Quality-gated award cannot run. "
                "Relax the gate or clear knockouts first."
            ],
        }
    else:
        split = engine.cheapest_per_line(
            cmp,
            vendor_ids,
            require_cleared_questionnaire=False,  # already filtered by gate ids
            allow_needs_review=allow_needs_review,
        )
        # Extra safety: strip any assumed cells from freeze candidates
        if not confirm_assumed:
            cleaned_rows = []
            total = 0.0
            share: dict = {}
            uncovered = list(split.get("uncovered_lines") or [])
            for r in split["rows"]:
                if not r.get("winner_id"):
                    cleaned_rows.append(r)
                    continue
                cell = next(
                    (ln["cells"][r["winner_id"]] for ln in cmp["lines"] if ln["line_no"] == r["line_no"]),
                    None,
                )
                if cell and cell.get("awardability") == "assumed":
                    uncovered.append(r["line_no"])
                    cleaned_rows.append(
                        {
                            **r,
                            "winner": None,
                            "winner_id": None,
                            "unit_inr": None,
                            "extended_inr": None,
                            "blocked_reason": "Assumed cell — confirm before freeze",
                        }
                    )
                    continue
                cleaned_rows.append(r)
                if r.get("winner") and r.get("extended_inr") is not None:
                    total += r["extended_inr"]
                    s = share.setdefault(r["winner"], {"lines": 0, "extended_inr": 0.0})
                    s["lines"] += 1
                    s["extended_inr"] = round(s["extended_inr"] + r["extended_inr"], 2)
            split = {
                **split,
                "rows": cleaned_rows,
                "total_extended_inr": round(total, 2),
                "uncovered_lines": sorted(set(uncovered)),
                "share_by_vendor": share,
            }

    params = {
        "strategy": strategy,
        "require_quality_gate": require_quality_gate,
        "allow_needs_review": allow_needs_review,
        "confirm_assumed": confirm_assumed,
        "eligible_vendor_ids": vendor_ids,
    }
    snap = snapshots.create_calculation_snapshot(
        state,
        "award_scenario",
        parameters=params,
        result={
            "total_extended_inr": split["total_extended_inr"],
            "covered_line_count": cmp["line_count"] - len(split["uncovered_lines"]),
            "uncovered_lines": split["uncovered_lines"],
            "share_by_vendor": split["share_by_vendor"],
            "eligible_vendors": split.get("eligible_vendors") or [],
        },
    )
    blockers = awardability.blockers_panel(cmp)
    return {
        "available": True,
        "snapshot": snap,
        "vendor_data_version": snap["vendor_data_version"],
        "strategy": (
            "quality-gated cheapest per line"
            if require_quality_gate
            else "cheapest per line (all vendors)"
        ),
        "strategy_key": strategy,
        "require_quality_gate": require_quality_gate,
        "confirm_assumed": confirm_assumed,
        "total_extended_inr": split["total_extended_inr"],
        "covered_line_count": cmp["line_count"] - len(split["uncovered_lines"]),
        "uncovered_lines": split["uncovered_lines"],
        "share_by_vendor": split["share_by_vendor"],
        "split": split,
        "cmp": cmp,
        "gates": g,
        "blockers": blockers,
        "exclusion_summary": cmp.get("exclusion_summary"),
        "created_at": snap["created_at"],
    }


def _notices_and_regrets(proposal: dict) -> tuple[list[dict], list[dict]]:
    """Draft award notices for winners and regrets for others (stub text)."""
    share = proposal.get("share_by_vendor") or {}
    winners = set(share.keys())
    notices = []
    for name, s in share.items():
        notices.append(
            {
                "vendor": name,
                "kind": "award_notice",
                "subject": f"Award notice — {s['lines']} line(s) · {engine.fmt_inr(s['extended_inr'])}",
                "body": (
                    f"Subject to final commercial confirmation, we intend to award you "
                    f"{s['lines']} line(s) totalling {engine.fmt_inr(s['extended_inr'])} per year "
                    f"under strategy: {proposal['strategy']}. "
                    f"Snapshot {proposal['snapshot']['id']} · vendor data v{proposal['vendor_data_version']}."
                ),
            }
        )
    regrets = []
    for v in proposal["cmp"]["vendors"]:
        if v["name"] in winners:
            continue
        gate = v.get("gate", "")
        reason_bits = []
        if gate == "Fail":
            reason_bits.append("failed quality knockout(s)")
        elif gate == "Partial":
            reason_bits.append("incomplete quality questionnaire")
        if v["name"] not in winners:
            reason_bits.append("not lowest awardable on any awarded line under the chosen strategy")
        regrets.append(
            {
                "vendor": v["name"],
                "kind": "regret",
                "subject": f"Regret — {proposal['cmp']['title']}",
                "body": (
                    f"Thank you for quoting. On this event we are not awarding lines to {v['name']} "
                    f"({'; '.join(reason_bits)}). Snapshot {proposal['snapshot']['id']}."
                ),
            }
        )
    return notices, regrets




def _current_saved_recommendation(state: dict) -> dict | None:
    from . import scenario, snapshots

    snapshots.ensure_snapshot_fields(state)
    life = scenario.recommendation_lifecycle(state)
    if life["lifecycle"] != scenario.REC_SAVED:
        return None
    return life.get("current")


def validate_freeze_request(
    state: dict,
    *,
    mode: str = "complete",
    confirm_assumed: bool = False,
    acknowledgements: list[str] | None = None,
    partial_reason: str = "",
    require_quality_gate: bool = True,
) -> dict:
    """Server-side re-validation for complete vs partial freeze (Phase A.5)."""
    from . import scenario

    acknowledgements = list(acknowledgements or [])
    life = scenario.recommendation_lifecycle(state)
    errors: list[str] = []
    if not life["can_freeze"]:
        errors.append(life.get("freeze_blocked_reason") or "Save a current recommendation before freeze.")

    proposal = build_award_proposal(
        state,
        require_quality_gate=require_quality_gate,
        confirm_assumed=confirm_assumed,
    )
    result = scenario.compute_award_scenario(
        proposal["cmp"],
        strategy="quality_gated_cheapest" if require_quality_gate else "split_cheapest",
        vendor_ids=proposal.get("eligible_vendor_ids")
        or (proposal["split"].get("eligible_vendor_ids")),
        require_cleared_questionnaire=False,
        allow_needs_review=False,
        vendor_data_version=proposal["vendor_data_version"],
        calculation_snapshot_id=proposal["snapshot"]["id"],
        require_quality_gate=require_quality_gate,
        discounts_confirmed=bool((state.get("discount_confirmations") or {})),
    )

    selected_blockers = result.selected_award_blockers
    coverage_gaps = result.coverage_gaps
    acks_needed = result.buyer_acknowledgements_required

    if mode == "complete":
        if coverage_gaps or result.uncovered_lines:
            errors.append(
                "Complete freeze requires full allocation — uncovered lines: "
                + ", ".join(str(x) for x in (result.uncovered_lines or []))
            )
        if selected_blockers:
            errors.append(
                f"Complete freeze blocked by {len(selected_blockers)} selected-award blocker(s)."
            )
        # Assumed cells in split without confirm
        assumed = [
            r
            for r in proposal["split"]["rows"]
            if r.get("blocked_reason") and "Assumed" in (r.get("blocked_reason") or "")
        ]
        if assumed and not confirm_assumed:
            errors.append("Assumed cells present — confirm them or use partial freeze.")
    elif mode == "partial":
        if not (partial_reason or "").strip():
            errors.append("Partial freeze requires a written reason.")
        missing_acks = []
        for a in acks_needed:
            key = a.get("kind") or a.get("label")
            if key and key not in acknowledgements and a.get("blocks_complete_freeze"):
                missing_acks.append(a.get("label") or key)
        # Also require explicit ack of coverage gaps / selected blockers when present
        if coverage_gaps and "coverage_gaps" not in acknowledgements:
            missing_acks.append("coverage_gaps")
        if selected_blockers and "selected_blockers" not in acknowledgements:
            missing_acks.append("selected_blockers")
        if missing_acks:
            errors.append(
                "Partial freeze requires acknowledgements: " + ", ".join(missing_acks)
            )
    else:
        errors.append(f"Unknown freeze mode {mode!r}")

    return {
        "ok": not errors,
        "errors": errors,
        "mode": mode,
        "proposal": proposal,
        "scenario": result.as_dict(),
        "lifecycle": life,
        "acknowledgements": acknowledgements,
        "partial_reason": partial_reason,
    }


def freeze_award(
    state: dict,
    *,
    confirm_assumed: bool = False,
    require_quality_gate: bool = True,
    mode: str = "complete",
    acknowledgements: list[str] | None = None,
    partial_reason: str = "",
) -> dict:
    """Create an immutable freeze pack (complete or partial) bound to a snapshot.

    Complete: full allocation, no selected-award blockers, saved recommendation required.
    Partial: acknowledgements + reason required; still immutable once written.
    """
    from . import scenario

    if state.get("demo_mode") and state.get("freeze_locked_by_demo"):
        raise ValueError("Demo mode prevents overwriting a locked freeze without Interview reset.")

    check = validate_freeze_request(
        state,
        mode=mode,
        confirm_assumed=confirm_assumed,
        acknowledgements=acknowledgements,
        partial_reason=partial_reason,
        require_quality_gate=require_quality_gate,
    )
    if not check["ok"]:
        raise ValueError("; ".join(check["errors"]))

    proposal = check["proposal"]
    scen = check["scenario"]
    assumed_in_split = [
        r
        for r in proposal["split"]["rows"]
        if r.get("blocked_reason") and "Assumed" in (r.get("blocked_reason") or "")
    ]
    notices, regrets = _notices_and_regrets(proposal)
    rec = _current_saved_recommendation(state)

    total_paise = scen.get("total_extended_paise")
    if total_paise is None:
        from .scenario import inr_to_paise
        total_paise = inr_to_paise(proposal["total_extended_inr"])

    pack = {
        "id": _short(),
        "frozen_at": _now(),
        "status": "frozen",
        "freeze_mode": mode,
        "immutable": True,
        "strategy": proposal["strategy"],
        "strategy_key": proposal["strategy_key"],
        "require_quality_gate": require_quality_gate,
        "confirm_assumed": confirm_assumed,
        "calculation_snapshot_id": proposal["snapshot"]["id"],
        "vendor_data_version": proposal["vendor_data_version"],
        "input_hash": proposal["snapshot"].get("input_hash"),
        "total_extended_inr": proposal["total_extended_inr"],
        "total_extended_paise": total_paise,
        "covered_line_count": proposal["covered_line_count"],
        "uncovered_lines": proposal["uncovered_lines"],
        "share_by_vendor": proposal["share_by_vendor"],
        "market_quote_coverage": scen.get("market_quote_coverage"),
        "scenario_award_coverage": scen.get("scenario_award_coverage"),
        "recommendation_id": (rec or {}).get("id"),
        "acknowledgements": list(acknowledgements or []),
        "partial_reason": (partial_reason or "").strip() if mode == "partial" else "",
        "line_awards": [
            {
                "line_no": r["line_no"],
                "description": r.get("description"),
                "winner": r.get("winner"),
                "winner_id": r.get("winner_id"),
                "unit_inr": r.get("unit_inr"),
                "unit_paise": r.get("unit_paise"),
                "extended_inr": r.get("extended_inr"),
                "extended_paise": r.get("extended_paise"),
                "runner_up": r.get("runner_up") or None,
                "gap_pct": r.get("gap_pct"),
                "blocked_reason": r.get("blocked_reason"),
            }
            for r in proposal["split"]["rows"]
        ],
        "notices": notices,
        "regrets": regrets,
        "gates_summary": proposal["gates"]["summary"],
        "exclusion_summary": proposal.get("exclusion_summary"),
        "blockers_total": proposal["blockers"]["total"],
        "selected_award_blockers": scen.get("selected_award_blockers") or [],
        "assumed_blocked_lines": [r["line_no"] for r in assumed_in_split],
        "readiness": scen.get("readiness"),
    }

    # Supersede prior freezes
    for prev in state.get("freeze_packs") or []:
        if prev.get("status") == "frozen":
            prev["status"] = "historical"
            prev["superseded_at"] = pack["frozen_at"]
            prev["superseded_reason"] = "A newer freeze was created."

    state.setdefault("freeze_packs", []).append(pack)
    state["freeze"] = pack  # pointer to current
    state["status"] = "award_frozen"

    # Mark recommendation lifecycle
    if rec:
        rec["status"] = (
            scenario.REC_FROZEN_COMPLETE if mode == "complete" else scenario.REC_FROZEN_PARTIAL
        )
        rec["freeze_id"] = pack["id"]
    return pack


def refresh_freeze_staleness(state: dict) -> None:
    """Mark freeze historical when vendor data moved past its version."""
    snapshots.ensure_snapshot_fields(state)
    cur = snapshots.current_version(state)
    for pack in state.get("freeze_packs") or []:
        if pack.get("status") != "frozen":
            continue
        if pack.get("vendor_data_version", 0) < cur:
            pack["status"] = "historical"
            pack["superseded_at"] = _now()
            pack["superseded_reason"] = (
                f"Vendor data changed after freeze (freeze v{pack.get('vendor_data_version')}, current v{cur})."
            )
    if state.get("freeze") and state["freeze"].get("status") != "frozen":
        # Keep pointer but UI will show historical
        pass


def current_freeze(state: dict) -> dict | None:
    refresh_freeze_staleness(state)
    pack = state.get("freeze")
    if pack and pack.get("status") == "frozen":
        return pack
    frozen = [p for p in (state.get("freeze_packs") or []) if p.get("status") == "frozen"]
    return frozen[-1] if frozen else None


def export_freeze_zip(state: dict, pack: dict | None = None) -> bytes:
    """Zip memo + xlsx + notices/regrets clearly linked to the freeze snapshot."""
    pack = pack or current_freeze(state)
    if not pack:
        raise ValueError("No frozen award pack to export.")

    # Build memo text bound to freeze ids
    memo_lines = [
        f"# Frozen award pack — {state['rfx'].get('title', state['id'])}",
        "",
        f"- Freeze id: `{pack['id']}`",
        f"- Calculation snapshot: `{pack['calculation_snapshot_id']}`",
        f"- Vendor data version: {pack['vendor_data_version']}",
        f"- Frozen at: {pack['frozen_at']}",
        f"- Strategy: {pack['strategy']}",
        f"- Total: {engine.fmt_inr(pack['total_extended_inr'])} / yr",
        f"- Lines covered: {pack['covered_line_count']}",
        f"- Status: {pack['status']}",
        "",
        "## Vendor share",
    ]
    for name, s in (pack.get("share_by_vendor") or {}).items():
        memo_lines.append(f"- {name}: {s['lines']} lines · {engine.fmt_inr(s['extended_inr'])}")
    if pack.get("uncovered_lines"):
        memo_lines.append("")
        memo_lines.append(f"## Uncovered lines\n{', '.join(str(x) for x in pack['uncovered_lines'])}")
    memo_lines.append("")
    memo_lines.append("## Award notices")
    for n in pack.get("notices") or []:
        memo_lines.append(f"### {n['vendor']}\n{n['body']}\n")
    memo_lines.append("## Regrets")
    for n in pack.get("regrets") or []:
        memo_lines.append(f"### {n['vendor']}\n{n['body']}\n")
    if pack.get("exclusion_summary"):
        memo_lines.append("")
        memo_lines.append("## Exclusions (why totals are not 'full coverage')")
        memo_lines.append(pack["exclusion_summary"].get("headline", ""))

    # Workbook from current export helpers (stamped via snapshots)
    xlsx = export.award_workbook(state, provisional=False)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"freeze-{pack['id']}-memo.md", "\n".join(memo_lines))
        zf.writestr(f"freeze-{pack['id']}-award.xlsx", xlsx)
        zf.writestr(
            f"freeze-{pack['id']}-notices.json",
            json.dumps({"notices": pack.get("notices"), "regrets": pack.get("regrets")}, indent=2),
        )
        zf.writestr(
            f"freeze-{pack['id']}-manifest.json",
            json.dumps(
                {
                    "freeze_id": pack["id"],
                    "calculation_snapshot_id": pack["calculation_snapshot_id"],
                    "vendor_data_version": pack["vendor_data_version"],
                    "strategy": pack["strategy"],
                    "total_extended_inr": pack["total_extended_inr"],
                    "status": pack["status"],
                },
                indent=2,
            ),
        )
    return buf.getvalue()


# Aliases used by routes
validate_freeze_request = validate_freeze_request
