"""Simple Award draft workflow: top-2 shortlist → line assign → acknowledgements → send.

Replaces freeze/lock as the primary buyer path on the Award page.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from . import engine, snapshots

ACKNOWLEDGEMENT_ITEMS: list[dict[str, str]] = [
    {
        "id": "quality_gates_reviewed",
        "question": "Have you reviewed quality gates for the selected vendors?",
        "label": "I acknowledge I have reviewed quality gates for the selected vendors.",
    },
    {
        "id": "pricing_present",
        "question": "Confirm pricing is present on all awarded lines?",
        "label": "I confirm pricing is present on all awarded lines.",
    },
    {
        "id": "anomalies_ack",
        "question": "Do you acknowledge there are no unresolved anomalies on selected lines (or you accept remaining risk)?",
        "label": "I acknowledge there are no unresolved anomalies on selected lines, or I accept remaining risk.",
    },
    {
        "id": "draft_ready",
        "question": "Confirm you are ready to send award drafts to vendors and regret notices to others?",
        "label": "I confirm I am ready to send award drafts to vendors and regret notices to others.",
    },
]

REQUIRED_ACK_IDS = [c["id"] for c in ACKNOWLEDGEMENT_ITEMS]
# Back-compat aliases for older tests/imports
CHECKLIST_ITEMS = ACKNOWLEDGEMENT_ITEMS
REQUIRED_CHECK_IDS = REQUIRED_ACK_IDS

# Extraction / processing statuses that cannot be awarded
_BLOCKED_VENDOR_STATUSES = frozenset(
    {
        "failed_no_previous_data",
        "failed_using_previous_version",
        "error",
        "awaiting",
        "extracting",
        "excluded",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _live(state: dict, live: dict | None = None) -> dict:
    if live and live.get("available"):
        return live
    if any(v.get("extraction") for v in state.get("vendors", [])):
        return snapshots.live_award_calculation(state)
    return {"available": False}


def _vendor_status(v: dict) -> str:
    from . import vendor_extraction as vex

    try:
        return vex.get_status(v) or v.get("status") or ""
    except Exception:
        st = (v.get("extraction_status") or {}).get("status") or v.get("status") or ""
        return st


def _gate_rows(live: dict) -> list[dict]:
    gates = live.get("gates") or {}
    return list(gates.get("vendors") or gates.get("rows") or [])


def _pass_vendors(state: dict, live: dict) -> list[dict]:
    """Pass-grade vendors that are extracted and not blocked."""
    by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
    out: list[dict] = []
    for row in _gate_rows(live):
        if (row.get("grade") or "") != "Pass":
            continue
        if not row.get("eligible_for_quality_gated_award", True):
            continue
        v = by_id.get(row.get("vendor_id") or "")
        if not v:
            continue
        status = _vendor_status(v)
        if status in _BLOCKED_VENDOR_STATUSES:
            continue
        if status not in ("extracted",) and not v.get("extraction"):
            continue
        out.append(
            {
                "vendor_id": v["vendor_id"],
                "name": v.get("name") or row.get("name"),
                "grade": "Pass",
                "reason": row.get("reason") or "Cleared quality gates",
                "gate_row": row,
            }
        )
    return out


def _usable_unit(cmp: dict, line_no: int, vendor_id: str) -> float | None:
    for ln in cmp.get("lines") or []:
        if ln.get("line_no") != line_no:
            continue
        cell = (ln.get("cells") or {}).get(vendor_id) or {}
        if cell.get("unit_inr") is None:
            return None
        if cell.get("status") in ("ok", "converted", "reviewed"):
            return float(cell["unit_inr"])
        # awardability may allow reviewed etc.
        if cell.get("awardability") == "awardable" and cell.get("unit_inr") is not None:
            return float(cell["unit_inr"])
        return None
    return None


def _sole_vendor_total(cmp: dict, vendor_id: str) -> float:
    """Sum extended INR if this vendor alone supplied every usable line they quote."""
    total = 0.0
    for ln in cmp.get("lines") or []:
        unit = _usable_unit(cmp, ln["line_no"], vendor_id)
        if unit is None:
            continue
        total += unit * float(ln.get("annual_qty") or 0)
    return round(total, 2)


def suggest_top2(state: dict, live: dict | None = None) -> dict[str, Any]:
    """Shortlist up to 2 Pass vendors with best overall pricing.

    Prefer Pass vendors. Rank by sole-vendor extended total (lower = better pricing),
    then by lines covered. If fewer than 2 Pass, explain and return only eligibles.
    """
    live = _live(state, live)
    if not live.get("available"):
        return {
            "vendors": [],
            "shortlist_ids": [],
            "explanation": "Extract vendor responses before suggesting a shortlist.",
            "pass_count": 0,
            "enough": False,
        }

    cmp = live.get("cmp") or engine.build_comparison(state)
    pass_vs = _pass_vendors(state, live)
    ranked = []
    for pv in pass_vs:
        vid = pv["vendor_id"]
        sole = _sole_vendor_total(cmp, vid)
        covered = sum(
            1
            for ln in cmp.get("lines") or []
            if _usable_unit(cmp, ln["line_no"], vid) is not None
        )
        why_bits = [
            "Cleared quality gates (Pass)",
            "Passed knockout checks",
        ]
        if sole > 0:
            why_bits.append(
                f"Strong overall pricing among Pass vendors ({engine.fmt_inr(sole)} if sole-vendor on quoted lines)"
            )
        else:
            why_bits.append("Pass vendor with usable quotes")
        why_bits.append(f"Usable quotes on {covered}/{cmp.get('line_count') or '?'} lines")
        ranked.append(
            {
                **pv,
                "sole_total_inr": sole,
                "covered_lines": covered,
                "why": why_bits,
                "why_summary": " · ".join(why_bits[:3]),
            }
        )
    # Best pricing = lower sole total among those with coverage; prefer more coverage first
    ranked.sort(key=lambda x: (-x["covered_lines"], x["sole_total_inr"], x["name"]))

    shortlist = ranked[:2]
    pass_count = len(ranked)
    if pass_count >= 2:
        explanation = (
            f"Shortlisted the 2 Pass vendors with the best overall pricing "
            f"({shortlist[0]['name']} and {shortlist[1]['name']})."
        )
        enough = True
    elif pass_count == 1:
        explanation = (
            f"Only 1 vendor cleared quality gates ({shortlist[0]['name']}). "
            "Shortlist includes only that eligible vendor — Fail / incomplete cannot be selected."
        )
        enough = False
    else:
        explanation = (
            "No vendors cleared quality gates (Pass). "
            "Cannot shortlist until knockout / quality issues are resolved on Compare or Anomalies."
        )
        enough = False

    return {
        "vendors": shortlist,
        "shortlist_ids": [v["vendor_id"] for v in shortlist],
        "all_pass": ranked,
        "explanation": explanation,
        "pass_count": pass_count,
        "enough": enough,
    }


def eligible_vendor_ids_for_line(
    state: dict, line_no: int, live: dict | None = None, *, shortlist_ids: list[str] | None = None
) -> list[dict]:
    """Vendors selectable for a line: Pass + usable price; never Fail/unextracted/blocked.

    At minimum includes shortlisted vendors who are eligible for the line.
    """
    live = _live(state, live)
    if not live.get("available"):
        return []
    cmp = live.get("cmp") or engine.build_comparison(state)
    pass_vs = _pass_vendors(state, live)
    out: list[dict] = []
    for pv in pass_vs:
        unit = _usable_unit(cmp, line_no, pv["vendor_id"])
        if unit is None:
            continue
        out.append({**pv, "unit_inr": unit})
    out.sort(key=lambda x: x["unit_inr"])
    return out


def default_allocation(
    state: dict, live: dict | None = None, *, shortlist_ids: list[str] | None = None
) -> dict[str, str]:
    """Map line_no(str) → vendor_id: cheapest eligible Pass on each line.

    Prefer shortlisted vendors when they have a usable quote; otherwise any Pass.
    """
    live = _live(state, live)
    if not live.get("available"):
        return {}
    cmp = live.get("cmp") or engine.build_comparison(state)
    if shortlist_ids is None:
        shortlist_ids = suggest_top2(state, live)["shortlist_ids"]
    short_set = set(shortlist_ids or [])
    alloc: dict[str, str] = {}
    for ln in cmp.get("lines") or []:
        line_no = ln["line_no"]
        elig = eligible_vendor_ids_for_line(state, line_no, live)
        if not elig:
            continue
        # Prefer cheapest among shortlist intersection; else cheapest Pass
        short_elig = [e for e in elig if e["vendor_id"] in short_set] if short_set else []
        pick = (short_elig or elig)[0]
        alloc[str(line_no)] = pick["vendor_id"]
    return alloc


def empty_acknowledgements() -> dict[str, bool]:
    return {c["id"]: False for c in ACKNOWLEDGEMENT_ITEMS}


def empty_checklist() -> dict[str, bool]:
    """Back-compat alias."""
    return empty_acknowledgements()


def draft_acknowledgements(draft: dict | None) -> dict[str, bool]:
    """Read acknowledgements from draft; migrate legacy checklist key."""
    d = draft or {}
    acks = d.get("acknowledgements")
    if isinstance(acks, dict):
        return dict(acks)
    legacy = d.get("checklist")
    if isinstance(legacy, dict):
        return dict(legacy)
    return empty_acknowledgements()


def ensure_award_draft(state: dict, live: dict | None = None) -> dict:
    """Ensure state['award_draft'] exists with shortlist + allocation + acknowledgements."""
    live = _live(state, live)
    draft = state.get("award_draft")
    if not isinstance(draft, dict):
        draft = {}
        state["award_draft"] = draft

    top = suggest_top2(state, live)
    # Refresh shortlist when vendor data version changes or missing
    vdv = live.get("vendor_data_version") if live.get("available") else snapshots.current_version(state)
    version_changed = draft.get("vendor_data_version") != vdv
    needs_init = (
        not draft.get("allocation")
        or version_changed
        or not draft.get("shortlist_ids")
    )
    if needs_init and live.get("available"):
        alloc = default_allocation(state, live, shortlist_ids=top["shortlist_ids"])
        keep_sent = bool(draft.get("sent")) and not version_changed
        draft.update(
            {
                "shortlist_ids": list(top["shortlist_ids"]),
                "shortlist_meta": [
                    {
                        "vendor_id": v["vendor_id"],
                        "name": v["name"],
                        "why": v.get("why") or [],
                        "why_summary": v.get("why_summary") or "",
                    }
                    for v in top["vendors"]
                ],
                "shortlist_explanation": top["explanation"],
                "allocation": alloc,
                "acknowledgements": (
                    empty_acknowledgements()
                    if version_changed
                    else draft_acknowledgements(draft)
                ),
                "vendor_data_version": vdv,
                "updated_at": _now(),
                "sent": keep_sent,
                "sent_at": draft.get("sent_at") if keep_sent else None,
                "send_confirmation": draft.get("send_confirmation") if keep_sent else None,
            }
        )
        draft.pop("checklist", None)
    elif not draft.get("acknowledgements") and not draft.get("checklist"):
        draft["acknowledgements"] = empty_acknowledgements()
    elif draft.get("checklist") and not draft.get("acknowledgements"):
        draft["acknowledgements"] = dict(draft.get("checklist") or {})
        draft.pop("checklist", None)

    # Always refresh explanation/meta display helpers from current top2 when not sent
    if live.get("available") and not draft.get("sent"):
        draft["shortlist_explanation"] = top["explanation"]
        if top["shortlist_ids"] and not draft.get("shortlist_ids"):
            draft["shortlist_ids"] = list(top["shortlist_ids"])

    state["award_draft"] = draft
    return draft


def line_assignment_rows(state: dict, live: dict | None = None) -> list[dict]:
    """Rows for the Assign-by-line table."""
    live = _live(state, live)
    draft = ensure_award_draft(state, live)
    if not live.get("available"):
        return []
    cmp = live.get("cmp") or engine.build_comparison(state)
    by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
    short_ids = set(draft.get("shortlist_ids") or [])
    alloc = draft.get("allocation") or {}
    rows = []
    for ln in cmp.get("lines") or []:
        line_no = ln["line_no"]
        elig = eligible_vendor_ids_for_line(state, line_no, live)
        # Ensure shortlisted vendors appear in picker when eligible
        elig_ids = {e["vendor_id"] for e in elig}
        selected = alloc.get(str(line_no))
        if selected and selected not in elig_ids:
            selected = None
        if not selected and elig:
            # default already in alloc usually; fallback cheapest shortlist/pass
            short_elig = [e for e in elig if e["vendor_id"] in short_ids]
            selected = (short_elig or elig)[0]["vendor_id"]
        options = []
        for e in elig:
            options.append(
                {
                    "vendor_id": e["vendor_id"],
                    "name": e["name"],
                    "unit_inr": e["unit_inr"],
                    "shortlisted": e["vendor_id"] in short_ids,
                }
            )
        sel_name = (by_id.get(selected) or {}).get("name") if selected else None
        sel_unit = next((o["unit_inr"] for o in options if o["vendor_id"] == selected), None)
        rows.append(
            {
                "line_no": line_no,
                "line_id": str(line_no),
                "description": ln.get("description") or "",
                "sku": ln.get("sku") or "",
                "annual_qty": ln.get("annual_qty"),
                "selected_vendor_id": selected,
                "selected_vendor_name": sel_name,
                "selected_unit_inr": sel_unit,
                "extended_inr": round(sel_unit * float(ln.get("annual_qty") or 0), 2)
                if sel_unit is not None
                else None,
                "options": options,
                "uncovered": not options,
            }
        )
    return rows


def update_allocation(state: dict, allocation: dict[str, str], live: dict | None = None) -> dict:
    """Validate and set allocation map. Raises ValueError on illegal assignments."""
    live = _live(state, live)
    draft = ensure_award_draft(state, live)
    cleaned: dict[str, str] = {}
    errors: list[str] = []
    for line_key, vendor_id in (allocation or {}).items():
        if not vendor_id:
            continue
        try:
            line_no = int(line_key)
        except (TypeError, ValueError):
            errors.append(f"Invalid line id {line_key!r}")
            continue
        elig = eligible_vendor_ids_for_line(state, line_no, live)
        elig_ids = {e["vendor_id"] for e in elig}
        if vendor_id not in elig_ids:
            vname = next(
                (v.get("name") for v in state.get("vendors") or [] if v["vendor_id"] == vendor_id),
                vendor_id,
            )
            errors.append(
                f"Cannot assign {vname} on line {line_no} — not an eligible Pass vendor with pricing."
            )
            continue
        cleaned[str(line_no)] = vendor_id
    if errors:
        raise ValueError("; ".join(errors[:5]))
    draft["allocation"] = cleaned
    draft["updated_at"] = _now()
    if draft.get("sent"):
        # Editing after send creates a new draft revision
        draft["sent"] = False
        draft["sent_at"] = None
        draft["send_confirmation"] = None
        draft["acknowledgements"] = empty_acknowledgements()
        draft.pop("checklist", None)
    state["award_draft"] = draft
    return draft


def update_acknowledgements(state: dict, ticks: dict[str, bool] | list[str]) -> dict:
    draft = ensure_award_draft(state)
    current = draft_acknowledgements(draft)
    if isinstance(ticks, list):
        for cid in REQUIRED_ACK_IDS:
            current[cid] = cid in ticks
    else:
        for cid in REQUIRED_ACK_IDS:
            if cid in ticks:
                current[cid] = bool(ticks[cid])
    draft["acknowledgements"] = current
    draft.pop("checklist", None)
    draft["updated_at"] = _now()
    state["award_draft"] = draft
    return draft


def update_checklist(state: dict, ticks: dict[str, bool] | list[str]) -> dict:
    """Back-compat alias."""
    return update_acknowledgements(state, ticks)


def acknowledgements_complete(draft: dict | None) -> bool:
    acks = draft_acknowledgements(draft)
    return all(acks.get(cid) for cid in REQUIRED_ACK_IDS)


def checklist_complete(draft: dict | None) -> bool:
    """Back-compat alias."""
    return acknowledgements_complete(draft)


def validate_for_send(state: dict, live: dict | None = None) -> dict[str, Any]:
    live = _live(state, live)
    draft = ensure_award_draft(state, live)
    errors: list[str] = []
    if not live.get("available"):
        errors.append("Extract vendor responses before sending award drafts.")
    if not acknowledgements_complete(draft):
        acks = draft_acknowledgements(draft)
        missing = [c["label"] for c in ACKNOWLEDGEMENT_ITEMS if not acks.get(c["id"])]
        errors.append("Confirm all required acknowledgements before Send: " + "; ".join(missing))
    alloc = draft.get("allocation") or {}
    if not alloc:
        errors.append("Assign at least one line to an eligible vendor before sending.")
    # Re-validate each assignment
    for line_key, vendor_id in alloc.items():
        try:
            line_no = int(line_key)
        except (TypeError, ValueError):
            errors.append(f"Invalid line {line_key}")
            continue
        elig_ids = {e["vendor_id"] for e in eligible_vendor_ids_for_line(state, line_no, live)}
        if vendor_id not in elig_ids:
            errors.append(f"Line {line_no} has an ineligible vendor assignment.")
    return {"ok": not errors, "errors": errors, "draft": draft}


def build_notices_from_draft(state: dict, live: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Award notices (winners, their lines only) + regret notices (others)."""
    live = _live(state, live)
    draft = ensure_award_draft(state, live)
    cmp = live.get("cmp") or engine.build_comparison(state)
    by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
    alloc = draft.get("allocation") or {}
    lines_by_vendor: dict[str, list[int]] = {}
    for line_key, vid in alloc.items():
        lines_by_vendor.setdefault(vid, []).append(int(line_key))
    for vid in lines_by_vendor:
        lines_by_vendor[vid].sort()

    title = (state.get("rfx") or {}).get("title") or state.get("id") or "RFx"
    snap_id = ((live.get("snapshot") or {}) if live.get("available") else {}).get("id") or "—"
    notices: list[dict] = []
    for vid, line_nos in lines_by_vendor.items():
        v = by_id.get(vid) or {}
        name = v.get("name") or vid
        # Extended for their lines only
        total = 0.0
        for ln in cmp.get("lines") or []:
            if ln["line_no"] not in line_nos:
                continue
            unit = _usable_unit(cmp, ln["line_no"], vid)
            if unit is not None:
                total += unit * float(ln.get("annual_qty") or 0)
        line_list = ", ".join(f"L{n}" for n in line_nos)
        notices.append(
            {
                "vendor": name,
                "vendor_id": vid,
                "kind": "award_notice",
                "subject": f"Award draft — {len(line_nos)} line(s) · {engine.fmt_inr(round(total, 2))}",
                "body": (
                    f"Subject to final commercial confirmation, we intend to award you "
                    f"{len(line_nos)} line(s) ({line_list}) on {title}. "
                    f"Draft total for your lines: {engine.fmt_inr(round(total, 2))}. "
                    f"Snapshot {snap_id}."
                ),
                "line_nos": line_nos,
            }
        )

    winner_ids = set(lines_by_vendor.keys())
    regrets: list[dict] = []
    for v in state.get("vendors") or []:
        if v["vendor_id"] in winner_ids:
            continue
        # Skip vendors with no extraction / never invited? Still send regret if they quoted.
        if not v.get("extraction") and not v.get("files"):
            continue
        name = v.get("name") or v["vendor_id"]
        regrets.append(
            {
                "vendor": name,
                "vendor_id": v["vendor_id"],
                "kind": "regret",
                "subject": f"Regret — {title}",
                "body": (
                    f"Thank you for quoting on {title}. "
                    f"On this event we are not awarding lines to {name}."
                ),
                "line_nos": [],
            }
        )
    return notices, regrets


def draft_totals(state: dict, live: dict | None = None) -> dict[str, Any]:
    live = _live(state, live)
    rows = line_assignment_rows(state, live)
    total = round(sum(r["extended_inr"] or 0 for r in rows if r.get("extended_inr") is not None), 2)
    covered = sum(1 for r in rows if r.get("selected_vendor_id"))
    uncovered = [r["line_no"] for r in rows if not r.get("selected_vendor_id")]
    share: dict[str, dict] = {}
    by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
    for r in rows:
        vid = r.get("selected_vendor_id")
        if not vid:
            continue
        name = (by_id.get(vid) or {}).get("name") or vid
        s = share.setdefault(name, {"vendor_id": vid, "lines": 0, "extended_inr": 0.0})
        s["lines"] += 1
        s["extended_inr"] = round(s["extended_inr"] + float(r.get("extended_inr") or 0), 2)
    return {
        "total_extended_inr": total,
        "covered_line_count": covered,
        "uncovered_lines": uncovered,
        "share_by_vendor": share,
        "rows": rows,
    }


def apply_vendor_to_lines(
    state: dict,
    vendor_id: str,
    line_nos: list[int] | None = None,
    live: dict | None = None,
) -> dict:
    """Apply a vendor to given lines (or all lines where they are eligible)."""
    live = _live(state, live)
    draft = ensure_award_draft(state, live)
    by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
    if vendor_id not in by_id:
        raise ValueError(f"Unknown vendor {vendor_id}")
    # Block Fail / unextracted
    v = by_id[vendor_id]
    status = _vendor_status(v)
    if status in _BLOCKED_VENDOR_STATUSES:
        raise ValueError(f"Cannot assign {v.get('name')} — status {status}.")
    gate = next((g for g in _gate_rows(live) if g.get("vendor_id") == vendor_id), None)
    if gate and (gate.get("grade") or "") != "Pass":
        raise ValueError(
            f"Cannot assign {v.get('name')} — gate {gate.get('grade')} (only Pass vendors)."
        )

    alloc = dict(draft.get("allocation") or {})
    cmp = (live.get("cmp") if live.get("available") else None) or engine.build_comparison(state)
    targets = list(line_nos) if line_nos else [ln["line_no"] for ln in cmp.get("lines") or []]
    applied: list[int] = []
    skipped: list[int] = []
    for line_no in targets:
        elig_ids = {e["vendor_id"] for e in eligible_vendor_ids_for_line(state, line_no, live)}
        if vendor_id not in elig_ids:
            skipped.append(line_no)
            continue
        alloc[str(line_no)] = vendor_id
        applied.append(line_no)
    if not applied:
        raise ValueError(
            f"{v.get('name')} is not eligible on the requested line(s)."
            + (f" Skipped: {skipped}" if skipped else "")
        )
    draft["allocation"] = alloc
    draft["updated_at"] = _now()
    if draft.get("sent"):
        draft["sent"] = False
        draft["sent_at"] = None
        draft["send_confirmation"] = None
    state["award_draft"] = draft
    return {
        "draft": draft,
        "vendor_id": vendor_id,
        "vendor_name": v.get("name"),
        "applied_lines": applied,
        "skipped_lines": skipped,
    }


def analyst_reason_snippet(answer: dict, vendor_name: str | None = None, limit: int = 420) -> str:
    """Buyer-facing reason pulled from the analyst answer (engine prose, not invented)."""
    raw = (answer.get("answer") or "").strip()
    if not raw:
        q = (answer.get("question") or "").strip()
        if vendor_name and q:
            return f"{vendor_name} — from analyst answer to: {q}"[:limit]
        return q[:limit] if q else "Analyst suggested this vendor."
    lower = raw.lower()
    chunk = raw
    for marker in ("## recommendation", "### recommendation", "**recommendation"):
        idx = lower.find(marker)
        if idx >= 0:
            chunk = raw[idx:]
            break
    lines: list[str] = []
    for line in chunk.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"[*_`]+", "", s)
        s = re.sub(r"^\s*[-•]\s*", "", s)
        s = re.sub(r"^\d+\.\s*", "", s)
        if not s:
            continue
        if vendor_name and vendor_name.lower() not in s.lower() and lines:
            if sum(len(x) for x in lines) > 120:
                break
        lines.append(s)
        if sum(len(x) + 1 for x in lines) >= limit:
            break
    text_out = " ".join(lines).strip()
    if vendor_name and vendor_name.lower() not in text_out.lower():
        text_out = f"{vendor_name}: {text_out}" if text_out else f"Analyst suggested {vendor_name}."
    if len(text_out) > limit:
        text_out = text_out[: limit - 1].rstrip() + "…"
    return text_out or (f"Analyst suggested {vendor_name}." if vendor_name else "Analyst suggestion.")


def _enrich_action(action: dict, answer: dict) -> dict:
    name = action.get("vendor_name") or action.get("vendor_id") or "vendor"
    reason = action.get("reason") or analyst_reason_snippet(answer, name)
    action["reason"] = reason
    action["send_label"] = action.get("send_label") or f"Send award to {name}"
    if not action.get("label"):
        lines = action.get("line_nos") or []
        action["label"] = (
            f"Apply {name}"
            + (f" to line(s) {', '.join(str(n) for n in lines)}" if lines else " to eligible lines")
        )
    return action


def attach_apply_actions(state: dict, answer: dict, live: dict | None = None) -> dict:
    """Attach apply_actions so Ask UI can offer Apply / Send award to vendor."""
    live = _live(state, live)
    actions: list[dict] = []
    premade_id = answer.get("premade_id")
    split_ids = {"best_split", "quality_gated_split"}
    if premade_id in split_ids and live.get("available"):
        split = live.get("split") or {}
        by_vendor: dict[str, list[int]] = {}
        for row in split.get("rows") or []:
            wid = row.get("winner_id")
            if not wid:
                continue
            by_vendor.setdefault(wid, []).append(row["line_no"])
        by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
        for vid, lines in by_vendor.items():
            name = (by_id.get(vid) or {}).get("name") or vid
            actions.append(
                {
                    "vendor_id": vid,
                    "vendor_name": name,
                    "line_nos": sorted(lines),
                    "label": f"Apply {name} to their {len(lines)} line(s)",
                    "reason": analyst_reason_snippet(answer, name),
                }
            )
        if by_vendor:
            answer["apply_all_split"] = {
                "allocation": {
                    str(row["line_no"]): row["winner_id"]
                    for row in (split.get("rows") or [])
                    if row.get("winner_id")
                },
                "label": "Apply suggested split to draft",
                "send_label": "Send awards from this split",
                "reason": analyst_reason_snippet(answer),
            }
    else:
        text_blob = f"{answer.get('question') or ''}\n{answer.get('answer') or ''}"
        line_nos = sorted(
            {int(n) for n in re.findall(r"\bl(?:ine)?\s*(\d+)\b", text_blob, flags=re.I)}
        )
        by_name = {v["name"].lower(): v for v in state.get("vendors") or []}
        found_vendors = []
        for name, v in by_name.items():
            if name.lower() in text_blob.lower():
                found_vendors.append(v)
        gate_by_id = {g.get("vendor_id"): g for g in _gate_rows(live)} if live.get("available") else {}
        for v in found_vendors:
            g = gate_by_id.get(v["vendor_id"]) or {}
            if g and g.get("grade") != "Pass":
                continue
            actions.append(
                {
                    "vendor_id": v["vendor_id"],
                    "vendor_name": v["name"],
                    "line_nos": line_nos,
                    "label": (
                        f"Apply {v['name']}"
                        + (
                            f" to line(s) {', '.join(str(n) for n in line_nos)}"
                            if line_nos
                            else " to eligible lines"
                        )
                    ),
                    "reason": analyst_reason_snippet(answer, v["name"]),
                }
            )
    for i, a in enumerate(actions):
        _enrich_action(a, answer)
        a["primary"] = False
    if len(actions) == 1:
        actions[0]["primary"] = True
    elif actions:
        # Overall / single-winner style questions: first Pass mention is primary
        actions[0]["primary"] = True
    answer["apply_actions"] = actions
    return answer


def clear_award_from_ask(state: dict) -> None:
    state.pop("award_from_ask", None)


def preload_from_analyst(
    state: dict,
    vendor_id: str,
    *,
    line_nos: list[int] | None = None,
    reason: str = "",
    question: str = "",
    chat_idx: int | None = None,
    live: dict | None = None,
) -> dict:
    """Apply suggested vendor to draft + stash reason banner for Award page."""
    out = apply_vendor_to_lines(state, vendor_id, line_nos, live)
    reason_text = (reason or "").strip() or f"Analyst suggested {out['vendor_name']}."
    banner = {
        "vendor_id": vendor_id,
        "vendor_name": out["vendor_name"],
        "line_nos": list(out["applied_lines"]),
        "reason": reason_text,
        "question": question or "",
        "chat_idx": chat_idx,
        "at": _now(),
        "focus_send": True,
    }
    state["award_from_ask"] = banner
    return {"apply": out, "banner": banner}


def preload_split_from_analyst(
    state: dict,
    allocation: dict[str, str],
    *,
    reason: str = "",
    question: str = "",
    chat_idx: int | None = None,
) -> dict:
    """Apply a multi-vendor split from Ask, then stash reason for Award."""
    update_allocation(state, {str(k): str(v) for k, v in allocation.items()})
    counts: dict[str, int] = {}
    for vid in allocation.values():
        counts[str(vid)] = counts.get(str(vid), 0) + 1
    primary_id = max(counts, key=counts.get) if counts else None
    by_id = {v["vendor_id"]: v for v in state.get("vendors") or []}
    primary_name = (by_id.get(primary_id) or {}).get("name") if primary_id else None
    reason_text = (reason or "").strip() or "Analyst suggested this award split."
    banner = {
        "vendor_id": primary_id,
        "vendor_name": primary_name or "Suggested split",
        "line_nos": sorted(int(k) for k in allocation.keys() if str(k).isdigit()),
        "reason": reason_text,
        "question": question or "",
        "chat_idx": chat_idx,
        "at": _now(),
        "focus_send": True,
        "split": True,
    }
    state["award_from_ask"] = banner
    return {"allocation": allocation, "banner": banner}



def mark_sent(state: dict, confirmation: dict) -> dict:
    draft = ensure_award_draft(state)
    draft["sent"] = True
    draft["sent_at"] = _now()
    draft["send_confirmation"] = confirmation
    state["award_draft"] = draft
    # Buyer-friendly status when no freeze happened
    from . import event_status

    pack = event_status.active_valid_freeze(state)
    if not pack:
        state["status"] = "award_drafts_sent"
    return draft
