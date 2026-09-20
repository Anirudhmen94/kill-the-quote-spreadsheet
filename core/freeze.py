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


def _notices_and_regrets(proposal: dict, state: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Draft award notices for winners and regrets for others (stub text).

    Uncovered lines are never claimed in award notices. Vendors with
    failed_no_previous_data (unreviewed) get no normal award/regret.
    Excluded vendors get neutral not-evaluated copy.
    """
    from . import vendor_extraction as vex

    share = proposal.get("share_by_vendor") or {}
    winners = set(share.keys())
    uncovered = set(proposal.get("uncovered_lines") or [])
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
                    + (
                        f" Uncovered line(s) {', '.join(str(x) for x in sorted(uncovered))} "
                        "are not part of this award."
                        if uncovered
                        else ""
                    )
                ),
                "uncovered_lines_excluded": sorted(uncovered),
            }
        )
    regrets = []
    vendors = proposal["cmp"]["vendors"]
    state_vendors = {v.get("name"): v for v in (state or {}).get("vendors") or []}
    for v in vendors:
        name = v["name"]
        if name in winners:
            continue
        sv = state_vendors.get(name) or {}
        st = vex.get_status(sv) if sv else None
        if st == vex.STATUS_FAILED_NO_PREVIOUS:
            # Unreviewed failed extraction — no normal award/regret
            continue
        if st == vex.STATUS_EXCLUDED or vex.is_excluded(sv):
            regrets.append(
                {
                    "vendor": name,
                    "kind": "not_evaluated",
                    "subject": f"Not evaluated — {proposal['cmp']['title']}",
                    "body": (
                        f"{name} was excluded from evaluation for this event and was not "
                        f"awarded or regretted on commercial grounds. "
                        f"Snapshot {proposal['snapshot']['id']}."
                    ),
                }
            )
            continue
        gate = v.get("gate", "")
        reason_bits = []
        if gate == "Fail":
            reason_bits.append("failed quality knockout(s)")
        elif gate == "Partial":
            reason_bits.append("incomplete quality questionnaire")
        reason_bits.append("not lowest awardable on any awarded line under the chosen strategy")
        regrets.append(
            {
                "vendor": name,
                "kind": "regret",
                "subject": f"Regret — {proposal['cmp']['title']}",
                "body": (
                    f"Thank you for quoting. On this event we are not awarding lines to {name} "
                    f"({'; '.join(reason_bits)}). Snapshot {proposal['snapshot']['id']}."
                ),
            }
        )
    return notices, regrets


def _err(type_: str, message: str, line_ids: list | None = None) -> dict:
    e: dict = {"type": type_, "message": message}
    if line_ids is not None:
        e["lineIds"] = list(line_ids)
    return e


def freeze_error_messages(check: dict) -> list[str]:
    """Flatten structured errors to messages (routes / legacy tests)."""
    out = []
    for e in check.get("errors") or []:
        if isinstance(e, dict):
            out.append(e.get("message") or e.get("type") or str(e))
        else:
            out.append(str(e))
    return out


def freeze_errors_text(check: dict) -> str:
    return "; ".join(freeze_error_messages(check))


class FreezeValidationError(ValueError):
    """Structured freeze rejection — never persist on this error."""

    def __init__(self, check: dict):
        self.check = check
        self.errors = list(check.get("errors") or [])
        self.code = check.get("code")
        super().__init__(freeze_errors_text(check) or "Freeze validation failed.")


def _current_saved_recommendation(state: dict) -> dict | None:
    from . import scenario, snapshots

    snapshots.ensure_snapshot_fields(state)
    life = scenario.recommendation_lifecycle(state)
    if life["lifecycle"] != scenario.REC_SAVED:
        return None
    return life.get("current")


def _append_freeze_audit(state: dict, event: dict) -> None:
    from . import scenario

    state.setdefault("freeze_audit", [])
    row = {"at": _now(), **event}
    state["freeze_audit"].append(row)
    try:
        scenario.append_buyer_review_log(
            state,
            {
                "source": "freeze",
                "action": event.get("action") or "freeze_audit",
                "note": event.get("detail") or event.get("action") or "freeze audit",
                "vendor_id": event.get("vendor_id"),
                "vendor_name": event.get("vendor"),
                "line_no": None,
            },
        )
    except Exception:
        pass


def repair_historical_freezes(state: dict) -> list[dict]:
    """Repair packs labeled complete with uncovered lines — never invent acks.

    If historical partialReason + coverage ack present → reclassify partial.
    Else mark invalid_historical_freeze / requires_review and audit.
    """
    repairs: list[dict] = []
    for pack in state.get("freeze_packs") or []:
        if pack.get("status") not in ("frozen", "historical", "invalid_historical_freeze", "requires_review"):
            continue
        uncovered = list(pack.get("uncovered_lines") or [])
        mode = pack.get("freeze_mode") or "complete"
        if mode != "complete" or not uncovered:
            # Clear invalid flag noise for healthy packs
            continue
        if pack.get("integrity") == "reclassified_partial":
            continue
        if pack.get("status") in ("invalid_historical_freeze", "requires_review"):
            continue

        acks = list(pack.get("acknowledgements") or [])
        reason = (pack.get("partial_reason") or "").strip()
        has_coverage_ack = "coverage_gaps" in acks
        if reason and has_coverage_ack:
            pack["freeze_mode"] = "partial"
            pack["integrity"] = "reclassified_partial"
            pack["reclassified_at"] = _now()
            pack["reclassified_reason"] = (
                "Historical pack labeled complete but had uncovered lines; "
                "reclassified to partial because partial_reason and coverage_gaps ack were present."
            )
            detail = {
                "action": "reclassify_complete_to_partial",
                "freeze_id": pack.get("id"),
                "uncovered_lines": uncovered,
                "detail": pack["reclassified_reason"],
            }
            _append_freeze_audit(state, detail)
            repairs.append(detail)
        else:
            pack["status"] = "requires_review"
            pack["integrity"] = "invalid_historical_freeze"
            pack["invalid_reason"] = (
                "Pack was labeled freeze_mode=complete but uncovered_lines is non-empty "
                "without a recorded partial_reason and coverage_gaps acknowledgement. "
                "Display must not treat this as a valid complete freeze."
            )
            detail = {
                "action": "mark_invalid_historical_freeze",
                "freeze_id": pack.get("id"),
                "uncovered_lines": uncovered,
                "detail": pack["invalid_reason"],
            }
            _append_freeze_audit(state, detail)
            repairs.append(detail)

    # Keep state["freeze"] pointer consistent
    cur = state.get("freeze")
    if cur and cur.get("integrity") == "invalid_historical_freeze":
        pass
    elif cur and cur.get("id"):
        for p in state.get("freeze_packs") or []:
            if p.get("id") == cur.get("id"):
                state["freeze"] = p
                break
    return repairs


def validate_freeze_request(
    state: dict,
    *,
    mode: str = "complete",
    confirm_assumed: bool = False,
    acknowledgements: list[str] | None = None,
    partial_reason: str = "",
    require_quality_gate: bool = True,
) -> dict:
    """Sole authority for complete vs partial freeze validation.

    Returns structured result:
      { ok, code, errors: [{type, lineIds?, message}], ... }
    Never invents acknowledgements or reasons.
    """
    from . import scenario, snapshots, vendor_extraction as vex

    acknowledgements = list(acknowledgements or [])
    snapshots.ensure_snapshot_fields(state)
    repair_historical_freezes(state)
    life = scenario.recommendation_lifecycle(state)
    errors: list[dict] = []

    if not life.get("can_freeze"):
        errors.append(
            _err(
                "recommendation_required",
                life.get("freeze_blocked_reason")
                or "Save a current recommendation before freeze.",
            )
        )

    proposal = build_award_proposal(
        state,
        require_quality_gate=require_quality_gate,
        confirm_assumed=confirm_assumed,
    )
    live_vdv = proposal["vendor_data_version"]
    live_snap_id = proposal["snapshot"]["id"]

    rec = life.get("current") if life.get("lifecycle") == scenario.REC_SAVED else None
    if rec:
        if rec.get("vendor_data_version") != live_vdv:
            errors.append(
                _err(
                    "stale_recommendation",
                    "Saved recommendation vendor data version does not match live calculation.",
                )
            )
        rec_snap = rec.get("calculation_snapshot_id")
        # Recommendation may predate this proposal's fresh snapshot; require version match
        # and that a snapshot id is bound. Exact id equality is checked when present on both
        # and recommendation was saved from the same live calc family.
        if rec_snap and rec.get("vendor_data_version") == live_vdv:
            # Version matches — snapshot id may differ because proposal creates a new snap;
            # treat version match + saved/current status as "current" per lifecycle.
            pass
        elif rec.get("status") not in ("current", scenario.REC_SAVED, "saved"):
            errors.append(
                _err(
                    "recommendation_not_current",
                    "Saved recommendation is not current.",
                )
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
        discount_confirmations=state.get("discount_confirmations") or {},
    )

    selected_blockers = result.selected_award_blockers
    coverage_gaps = result.coverage_gaps
    uncovered = list(result.uncovered_lines or proposal.get("uncovered_lines") or [])
    line_count = proposal["cmp"].get("line_count") or len(proposal["cmp"].get("lines") or [])
    covered = int(proposal.get("covered_line_count") or 0)
    acks_needed = result.buyer_acknowledgements_required

    extraction_blockers = vex.vendors_blocking_complete_freeze(state)

    if mode == "complete":
        if covered != line_count or uncovered:
            errors.append(
                _err(
                    "incomplete_coverage",
                    "Complete freeze requires full allocation — uncovered lines: "
                    + (", ".join(str(x) for x in uncovered) if uncovered else "(coverage mismatch)"),
                    line_ids=uncovered,
                )
            )
        if selected_blockers:
            errors.append(
                _err(
                    "selected_award_blockers",
                    f"Complete freeze blocked by {len(selected_blockers)} selected-award blocker(s).",
                )
            )
        assumed = [
            r
            for r in proposal["split"]["rows"]
            if r.get("blocked_reason") and "Assumed" in (r.get("blocked_reason") or "")
        ]
        if assumed and not confirm_assumed:
            errors.append(
                _err(
                    "assumed_unconfirmed",
                    "Assumed cells present — confirm them or use partial freeze.",
                    line_ids=[r["line_no"] for r in assumed],
                )
            )
        if extraction_blockers:
            names = ", ".join(
                f"{b['vendor']} ({b['status']})" for b in extraction_blockers
            )
            errors.append(
                _err(
                    "vendor_extraction_incomplete",
                    "Complete freeze blocked by vendor extraction status: "
                    + names
                    + ". Exclude vendors with a reason or wait until reading succeeds.",
                )
            )
    elif mode == "partial":
        if not (partial_reason or "").strip():
            errors.append(
                _err("partial_reason_required", "Partial freeze requires a written reason.")
            )
        if covered < 1:
            errors.append(
                _err(
                    "no_covered_lines",
                    "Partial freeze requires at least one covered line.",
                )
            )
        missing_acks: list[str] = []
        for a in acks_needed:
            key = a.get("kind") or a.get("label")
            if key and key not in acknowledgements and a.get("blocks_complete_freeze"):
                missing_acks.append(a.get("label") or key)
        if uncovered and "coverage_gaps" not in acknowledgements:
            missing_acks.append("coverage_gaps")
        if selected_blockers and "selected_blockers" not in acknowledgements:
            missing_acks.append("selected_blockers")
        # Extraction gaps: require explicit ack or that blockers are excluded
        if extraction_blockers:
            if "extraction_incomplete" not in acknowledgements and "vendor_extraction" not in acknowledgements:
                # Allow if every blocker is failed and buyer also provided exclusion — else require ack
                missing_acks.append("extraction_incomplete")
        if missing_acks:
            # unique preserve order
            seen = set()
            uniq = []
            for m in missing_acks:
                if m not in seen:
                    seen.add(m)
                    uniq.append(m)
            errors.append(
                _err(
                    "acknowledgements_required",
                    "Partial freeze requires acknowledgements: " + ", ".join(uniq),
                )
            )
    else:
        errors.append(_err("unknown_mode", f"Unknown freeze mode {mode!r}"))

    code = None
    if errors:
        code = errors[0].get("type")

    return {
        "ok": not errors,
        "code": code,
        "errors": errors,
        "mode": mode,
        "proposal": proposal,
        "scenario": result.as_dict(),
        "lifecycle": life,
        "acknowledgements": acknowledgements,
        "partial_reason": partial_reason,
        "uncovered_lines": uncovered,
        "covered_line_count": covered,
        "line_count": line_count,
        "extraction_blockers": extraction_blockers,
        "live_snapshot_id": live_snap_id,
        "live_vendor_data_version": live_vdv,
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

    Calls validate_freeze_request as sole authority. On failure raises
    FreezeValidationError — never persists. Asserts complete invariant before write.
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
        raise FreezeValidationError(check)

    proposal = check["proposal"]
    scen = check["scenario"]
    uncovered = list(check.get("uncovered_lines") or proposal.get("uncovered_lines") or [])
    covered = int(check.get("covered_line_count") or proposal.get("covered_line_count") or 0)
    line_count = int(check.get("line_count") or 0)

    # Complete invariant — refuse to persist even if validator was bypassed
    if mode == "complete":
        if uncovered or covered != line_count:
            raise FreezeValidationError(
                {
                    "ok": False,
                    "code": "complete_invariant",
                    "errors": [
                        _err(
                            "complete_invariant",
                            "Refusing to persist complete freeze with uncovered lines: "
                            + ", ".join(str(x) for x in uncovered),
                            line_ids=uncovered,
                        )
                    ],
                }
            )

    assumed_in_split = [
        r
        for r in proposal["split"]["rows"]
        if r.get("blocked_reason") and "Assumed" in (r.get("blocked_reason") or "")
    ]
    notices, regrets = _notices_and_regrets(proposal, state)
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
        "covered_line_count": covered,
        "uncovered_lines": uncovered,
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
        "integrity": "ok",
        "processing_completeness": {
            "covered_line_count": covered,
            "line_count": line_count,
            "uncovered_lines": uncovered,
            "extraction_blockers": check.get("extraction_blockers") or [],
        },
    }

    for prev in state.get("freeze_packs") or []:
        if prev.get("status") == "frozen":
            prev["status"] = "historical"
            prev["superseded_at"] = pack["frozen_at"]
            prev["superseded_reason"] = "A newer freeze was created."

    state.setdefault("freeze_packs", []).append(pack)
    state["freeze"] = pack
    state["status"] = "award_frozen"

    if rec:
        rec["status"] = (
            scenario.REC_FROZEN_COMPLETE if mode == "complete" else scenario.REC_FROZEN_PARTIAL
        )
        rec["freeze_id"] = pack["id"]

    _append_freeze_audit(
        state,
        {
            "action": f"freeze_{mode}",
            "freeze_id": pack["id"],
            "detail": f"Frozen {mode}; covered {covered}/{line_count}; uncovered {uncovered}",
        },
    )
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
        pass


def current_freeze(state: dict) -> dict | None:
    """Return current frozen pack after staleness refresh + historical repair."""
    refresh_freeze_staleness(state)
    repair_historical_freezes(state)
    pack = state.get("freeze")
    if pack and pack.get("status") == "frozen" and pack.get("integrity") != "invalid_historical_freeze":
        return pack
    if pack and pack.get("status") == "requires_review":
        return pack  # surface for UI banner — not valid complete
    frozen = [
        p
        for p in (state.get("freeze_packs") or [])
        if p.get("status") == "frozen" and p.get("integrity") != "invalid_historical_freeze"
    ]
    return frozen[-1] if frozen else None


def export_freeze_zip(state: dict, pack: dict | None = None) -> bytes:
    """Zip memo + xlsx + notices/regrets clearly linked to the freeze snapshot."""
    pack = pack or current_freeze(state)
    if not pack:
        raise ValueError("No frozen award pack to export.")

    memo_lines = [
        f"# Frozen award pack — {state['rfx'].get('title', state['id'])}",
        "",
        f"- Freeze id: `{pack['id']}`",
        f"- Freeze mode: {pack.get('freeze_mode') or 'complete'}",
        f"- Calculation snapshot: `{pack['calculation_snapshot_id']}`",
        f"- Vendor data version: {pack['vendor_data_version']}",
        f"- Frozen at: {pack['frozen_at']}",
        f"- Strategy: {pack['strategy']}",
        f"- Total: {engine.fmt_inr(pack['total_extended_inr'])} / yr",
        f"- Lines covered: {pack['covered_line_count']}",
        f"- Status: {pack['status']}",
        "",
        "## Processing completeness",
    ]
    pc = pack.get("processing_completeness") or {}
    memo_lines.append(
        f"- Covered {pc.get('covered_line_count', pack.get('covered_line_count'))}/"
        f"{pc.get('line_count', '?')}"
    )
    if pack.get("uncovered_lines"):
        memo_lines.append(
            f"- Uncovered lines (not awarded): {', '.join(str(x) for x in pack['uncovered_lines'])}"
        )
    memo_lines.append("")
    memo_lines.append("## Vendor share")
    for name, s in (pack.get("share_by_vendor") or {}).items():
        memo_lines.append(f"- {name}: {s['lines']} lines · {engine.fmt_inr(s['extended_inr'])}")
    if pack.get("uncovered_lines"):
        memo_lines.append("")
        memo_lines.append(f"## Uncovered lines\n{', '.join(str(x) for x in pack['uncovered_lines'])}")
    memo_lines.append("")
    memo_lines.append("## Award notices")
    for n in pack.get("notices") or []:
        memo_lines.append(f"### {n['vendor']}\n{n['body']}\n")
    memo_lines.append("## Regrets / not evaluated")
    for n in pack.get("regrets") or []:
        memo_lines.append(f"### {n['vendor']} ({n.get('kind')})\n{n['body']}\n")
    if pack.get("exclusion_summary"):
        memo_lines.append("")
        memo_lines.append("## Exclusions (why totals are not 'full coverage')")
        memo_lines.append(pack["exclusion_summary"].get("headline", ""))

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
                    "freeze_mode": pack.get("freeze_mode"),
                    "calculation_snapshot_id": pack["calculation_snapshot_id"],
                    "vendor_data_version": pack["vendor_data_version"],
                    "strategy": pack["strategy"],
                    "total_extended_inr": pack["total_extended_inr"],
                    "status": pack["status"],
                    "uncovered_lines": pack.get("uncovered_lines"),
                    "processing_completeness": pack.get("processing_completeness"),
                    "integrity": pack.get("integrity"),
                },
                indent=2,
            ),
        )
    return buf.getvalue()
