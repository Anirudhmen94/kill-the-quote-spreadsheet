"""Award-page Ask: premade (engine-first, version-bound cache) + free-ask (live Claude).

Premade clicks never wait on Claude. Cache key = vendor_data_version + prompt id;
invalidate/rebuild when vendor_data_version changes. Free-ask uses analyst.ask.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import award_packs, engine, snapshots

PREMADES: list[dict[str, str]] = [
    {
        "id": "best_split",
        "label": "What's the best split among Pass vendors?",
        "question": "What's the best split among Pass vendors?",
    },
    {
        "id": "who_to_drop",
        "label": "Who should we drop and why?",
        "question": "Who should we drop and why?",
    },
    {
        "id": "biggest_risks",
        "label": "What are the biggest risks if we send now?",
        "question": "What are the biggest risks if we send award drafts now?",
    },
]

_CACHE_KEY = "award_ask_cache"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def premade_by_id(prompt_id: str) -> dict[str, str] | None:
    for p in PREMADES:
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


def _packs(live: dict) -> list[dict]:
    return award_packs.vendor_award_packs(
        live.get("split"),
        cmp=live.get("cmp"),
        gates=live.get("gates"),
        total_extended_inr=live.get("total_extended_inr"),
    )


def _non_pass(gates: dict | None, cmp: dict | None) -> list[dict]:
    out: list[dict] = []
    if gates:
        for v in gates.get("vendors") or []:
            grade = (v.get("grade") or v.get("gate") or "").strip() or "—"
            if grade != "Pass":
                out.append({**v, "grade": grade})
        return out
    for v in (cmp or {}).get("vendors") or []:
        grade = (v.get("gate") or "").strip() or "—"
        if grade != "Pass":
            out.append({**v, "grade": grade})
    return out


def _engine_tables(live: dict, packs: list[dict]) -> list[dict]:
    tables: list[dict] = []
    if packs:
        cols = ["vendor", "lines_won", "extended_inr", "share_pct", "gate_grade"]
        tables.append(
            {
                "title": "award_split_share",
                "columns": cols,
                "rows": [[p.get(c) for c in cols] for p in packs],
            }
        )
    uncovered = award_packs.uncovered_line_rows(live.get("split"))
    if uncovered:
        cols = ["line_no", "description", "reason"]
        tables.append(
            {
                "title": "uncovered_lines",
                "columns": cols,
                "rows": [[u.get(c) for c in cols] for u in uncovered],
            }
        )
    return tables


def _narrative_best_split(live: dict, packs: list[dict]) -> str:
    total = _fmt_inr(live.get("total_extended_inr"))
    covered = live.get("covered_line_count")
    line_count = (live.get("cmp") or {}).get("line_count")
    uncovered = live.get("uncovered_lines") or []
    bits = [
        f"- **{p.get('vendor')}**: {p.get('lines_won')} line(s), "
        f"{_fmt_inr(p.get('extended_inr'))} ({p.get('share_pct')}%)"
        for p in packs
    ] or ["- No Pass-vendor winners yet."]
    uncovered_note = (
        f" Uncovered: line(s) {', '.join(str(x) for x in uncovered)}."
        if uncovered
        else " Full coverage among Pass vendors."
    )
    return (
        "## Recommendation\n\n"
        "Quality-gated cheapest-per-line among **Pass** vendors.\n\n"
        f"**Official total:** {total} / yr · **Covered:** {covered}/{line_count or '?'}."
        f"{uncovered_note}\n\n"
        "### Split\n\n"
        + "\n".join(bits)
        + "\n\n_Numbers from the engine (version-bound). Short template narrative — not a live model call._"
    )


def _narrative_who_to_drop(live: dict, packs: list[dict]) -> str:
    winners = {p.get("vendor") for p in packs}
    drop = _non_pass(live.get("gates"), live.get("cmp"))
    lines = []
    for v in drop:
        name = v.get("name") or v.get("vendor") or v.get("vendor_id")
        grade = v.get("grade") or "—"
        reason = v.get("reason") or (
            "Failed knockout quality checks"
            if grade == "Fail"
            else "Partial — not eligible for the quality-gated default split"
        )
        lines.append(f"- **{name}** ({grade}): {reason}")
    if not lines:
        lines.append("- No vendors to drop under the current gates.")
    keep = ", ".join(sorted(x for x in winners if x)) or "—"
    return (
        "## Recommendation\n\n"
        f"**Keep on the award:** {keep}.\n\n"
        "**Drop / leave out of the quality-gated split:**\n\n"
        + "\n".join(lines)
        + "\n\nDefault rule: cheapest usable INR/pc among vendors who cleared knockout quality checks. "
        "_Engine grades + split; template narrative._"
    )


def _narrative_biggest_risks(live: dict, packs: list[dict]) -> str:
    uncovered = live.get("uncovered_lines") or []
    blockers = live.get("blockers") or {}
    by_kind = blockers.get("by_kind") or {}
    readiness = live.get("readiness_headline") or live.get("readiness") or "—"
    excl = ((live.get("exclusion_summary") or {}).get("headline") or "").strip()
    risks: list[str] = []
    if uncovered:
        risks.append(
            f"**Coverage gap** — line(s) {', '.join(str(x) for x in uncovered)} have no "
            "awardable quote among Pass vendors. Those lines stay uncovered in the draft "
            "until resolved on Compare / Anomalies."
        )
    if by_kind.get("needs_review"):
        risks.append(
            f"**Needs-review cells** ({by_kind['needs_review']}) are held out of official totals."
        )
    if by_kind.get("unresolved"):
        risks.append(
            f"**Unresolved quotes** ({by_kind['unresolved']}) — clarify before defending the memo."
        )
    if by_kind.get("assumed"):
        risks.append(
            f"**Assumed mappings** ({by_kind['assumed']}) — confirm before treating as final."
        )
    if excl:
        risks.append(f"**Exclusions:** {excl}")
    if not risks:
        risks.append("No major engine-flagged risks on the current snapshot.")
    risk_md = "\n".join(f"{i}. {r}" for i, r in enumerate(risks, 1))
    return (
        "## Recommendation\n\n"
        f"**Readiness:** {readiness}\n\n"
        f"### Biggest risks if you send award drafts now\n\n{risk_md}\n\n"
        "_Derived from engine readiness / blockers — not a live model call._"
    )


_NARRATORS = {
    "best_split": _narrative_best_split,
    "who_to_drop": _narrative_who_to_drop,
    "biggest_risks": _narrative_biggest_risks,
}


def compute_premade(state: dict, prompt_id: str) -> dict:
    """Engine-first premade answer (no Claude). Suitable to cache."""
    meta = premade_by_id(prompt_id)
    if not meta:
        raise ValueError(f"Unknown premade prompt: {prompt_id}")
    if not any(v.get("extraction") for v in state.get("vendors", [])):
        raise ValueError("Extract at least one vendor response before asking.")

    live = snapshots.live_award_calculation(state)
    if not live.get("available"):
        raise ValueError("No calculation available yet.")

    packs = _packs(live)
    answer_md = _NARRATORS[prompt_id](live, packs)
    tables = _engine_tables(live, packs)
    caveats = list((live.get("split") or {}).get("caveats") or [])
    snap = live.get("snapshot") or {}
    vdv = live.get("vendor_data_version")
    raw = {
        "id": f"premade-{prompt_id}-v{vdv}",
        "question": meta["question"],
        "answer": answer_md,
        "trace": [
            {
                "tool": "award_ask_premade",
                "input": {"prompt_id": prompt_id, "vendor_data_version": vdv},
                "ok": True,
                "output_preview": "engine-first template narrative",
            }
        ],
        "tables": tables,
        "charts": [],
        "caveats": caveats,
        "source": "award_ask_premade",
        "premade_id": prompt_id,
        "award_shaped": True,
        "at": _now(),
        "status": "current",
    }
    from . import award_draft

    out = snapshots.attach_answer_metadata(state, raw, snap)
    return award_draft.attach_apply_actions(state, out, live)


def get_or_build_premade(state: dict, prompt_id: str) -> dict:
    """Return cached premade for current vendor_data_version, or compute + store."""
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


def looks_award_shaped(answer: dict) -> bool:
    if answer.get("award_shaped") or answer.get("premade_id"):
        return True
    text = ((answer.get("answer") or "") + "\n" + (answer.get("question") or "")).lower()
    return "recommendation" in text or "award" in text


def default_lock_rationale(state: dict, live: dict | None = None) -> str:
    """One-line rationale prefilled on Lock when no current recommendation."""
    if live is None and any(v.get("extraction") for v in state.get("vendors", [])):
        live = snapshots.live_award_calculation(state)
    live = live or {}
    strategy = live.get("strategy") or "quality-gated cheapest per line"
    uncovered = live.get("uncovered_lines") or []
    base = f"Proceed with {strategy}."
    if uncovered:
        base += (
            f" Partial lock: uncovered line(s) "
            f"{', '.join(str(x) for x in uncovered)} acknowledged."
        )
    return base


def needed_partial_acknowledgements(state: dict) -> tuple[list[str], str]:
    """Deprecated: auto-invented acks are no longer used by lock_award.

    Kept for import compatibility; returns empty and raises if called for locking.
    """
    raise RuntimeError(
        "needed_partial_acknowledgements is removed — lock_award no longer invents "
        "partial acknowledgements. Use Manual freeze partial with explicit acks + reason."
    )


def lock_award(
    state: dict,
    *,
    rationale: str | None = None,
    confirm_assumed: bool = False,
) -> dict[str, Any]:
    """Use & lock / Lock: save recommendation if needed, then complete freeze only.

    Does NOT auto-invent acknowledgements or silently freeze as partial/complete when
    incomplete. If complete validation fails, raises FreezeValidationError instructing
    the buyer to use Manual freeze partial with real coverage ack + reason.
    """
    from . import freeze, scenario

    if not any(v.get("extraction") for v in state.get("vendors", [])):
        raise ValueError("Extract vendor responses before locking an award.")

    life = scenario.recommendation_lifecycle(state)
    saved_rec = False
    if not life.get("can_freeze"):
        text_r = (rationale or "").strip() or default_lock_rationale(state)
        snapshots.save_recommendation_from_live(state, text_r)
        saved_rec = True

    check = freeze.validate_freeze_request(
        state,
        mode="complete",
        confirm_assumed=confirm_assumed,
        require_quality_gate=True,
    )
    if not check["ok"]:
        msgs = freeze.freeze_error_messages(check)
        detail = "; ".join(msgs)
        uncovered = check.get("uncovered_lines") or []
        raise freeze.FreezeValidationError(
            {
                **check,
                "errors": list(check.get("errors") or [])
                + [
                    {
                        "type": "use_manual_freeze_partial",
                        "message": (
                            "Lock / Use & lock only freezes when complete validation passes. "
                            "This award is not complete"
                            + (
                                f" (uncovered line(s) {', '.join(str(x) for x in uncovered)})"
                                if uncovered
                                else ""
                            )
                            + ". Open Freeze complete / Freeze partial… under Manual lock, "
                            "acknowledge coverage gaps (and selected blockers if any), and "
                            "enter a non-empty partial reason — acknowledgements are never invented. "
                            f"Blockers: {detail}"
                        ),
                    }
                ],
                "code": check.get("code") or "incomplete_for_lock",
                "ok": False,
            }
        )

    pack = freeze.freeze_award(
        state,
        confirm_assumed=confirm_assumed,
        require_quality_gate=True,
        mode="complete",
    )
    return {"pack": pack, "mode": "complete", "saved_recommendation": saved_rec}
