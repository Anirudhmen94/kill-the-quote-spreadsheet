"""Exception workflows for messy cells and gate Fail/Partial knockouts.

Open exceptions are derived from awardability blockers + gate Fail/Partial
(SSOT). Persisted overrides / approval requests live in state["exceptions"].

Cell overrides also write state["reviews"] so Compare/Award treat the cell as
reviewed. Gate overrides are consulted by gates.evaluate_gates via
cleared_knockouts().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import awardability, scenario, snapshots
from .gates import evaluate_gates

OPEN_STATUSES = {"open", "rejected"}
PENDING_STATUSES = {"pending_approval"}
RESOLVED_STATUSES = {"overridden", "approved"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _eid() -> str:
    return "exc_" + uuid.uuid4().hex[:10]


def ensure_exceptions(state: dict) -> None:
    if "exceptions" not in state or state["exceptions"] is None:
        state["exceptions"] = []


def exception_key(
    kind: str,
    vendor_id: str | None,
    line_no: int | None,
    gate_q_id: str | None = None,
) -> str:
    if kind.startswith("gate"):
        return f"gate:{vendor_id}:{gate_q_id or ''}"
    if kind == "coverage_gap":
        return f"coverage:{line_no}"
    return f"cell:{vendor_id}:{line_no}:{kind}"


def _eligible_ids_for_state(state: dict) -> set[str]:
    cmp = awardability.enrich_state_comparison(state)
    gates = cmp.get("gates") or {}
    return {
        v["vendor_id"]
        for v in (gates.get("vendors") or [])
        if v.get("eligible_for_quality_gated_award") or v.get("grade") == "Pass"
    }


def _annotate_exception_class(item: dict, eligible_ids: set[str], selected_pairs: set[tuple]) -> dict:
    vid = item.get("vendor_id")
    line_no = item.get("line_no")
    is_selected = (vid, line_no) in selected_pairs if vid and line_no is not None else False
    gate = None
    cls = scenario.classify_exception(
        kind=item.get("kind") or "needs_review",
        vendor_id=vid,
        vendor_gate=gate,
        eligible_vendor_ids=eligible_ids,
        is_selected=is_selected,
        line_no=line_no,
    )
    item["class"] = cls
    item["affects_current_recommendation"] = cls in (
        scenario.EXC_SELECTED_BLOCKER,
        scenario.EXC_COVERAGE_GAP,
        scenario.EXC_BUYER_ACK,
    )
    item["blocks_freeze"] = cls in (scenario.EXC_SELECTED_BLOCKER, scenario.EXC_COVERAGE_GAP)
    return item



def derive_open_exceptions(state: dict) -> list[dict]:
    """Candidate open exceptions from comparison SSOT + gates."""
    if not any(v.get("extraction") for v in state.get("vendors", [])):
        return []
    cmp = awardability.enrich_state_comparison(state)
    derived: list[dict] = []
    rid = state["id"]

    for b in cmp.get("blockers") or []:
        kind = b.get("kind") or "needs_review"
        vid = b.get("vendor_id")
        line_no = b.get("line_no")
        if kind == "coverage_gap":
            derived.append(
                {
                    "key": exception_key("coverage_gap", None, line_no),
                    "kind": "coverage_gap",
                    "vendor_id": None,
                    "vendor_name": None,
                    "line_no": line_no,
                    "gate_q_id": None,
                    "reason": b.get("detail") or b.get("label") or "No awardable quote",
                    "evidence_href": None,
                    "label": b.get("label") or "Coverage gap",
                }
            )
            continue
        derived.append(
            {
                "key": exception_key(kind, vid, line_no),
                "kind": kind,
                "vendor_id": vid,
                "vendor_name": b.get("vendor"),
                "line_no": line_no,
                "gate_q_id": None,
                "reason": b.get("detail") or b.get("label") or kind,
                "evidence_href": f"/rfx/{rid}/evidence/{vid}/{line_no}" if vid and line_no else None,
                "label": b.get("label") or kind.replace("_", " ").title(),
            }
        )

    gates = cmp.get("gates") or evaluate_gates(state)
    for gv in gates.get("vendors") or []:
        if gv.get("grade") not in ("Fail", "Partial"):
            continue
        vid = gv["vendor_id"]
        name = gv.get("name")
        for qid in gv.get("knockout_failed") or []:
            derived.append(
                {
                    "key": exception_key("gate_fail", vid, None, qid),
                    "kind": "gate_fail",
                    "vendor_id": vid,
                    "vendor_name": name,
                    "line_no": None,
                    "gate_q_id": qid,
                    "reason": f"Knockout {qid} failed — {gv.get('reason') or 'gate Fail'}",
                    "evidence_href": f"/rfx/{rid}/vendor/{vid}/questionnaire",
                    "label": f"Gate Fail · {qid}",
                }
            )
        for qid in gv.get("knockout_open") or []:
            derived.append(
                {
                    "key": exception_key("gate_partial", vid, None, qid),
                    "kind": "gate_partial",
                    "vendor_id": vid,
                    "vendor_name": name,
                    "line_no": None,
                    "gate_q_id": qid,
                    "reason": f"Knockout {qid} unanswered — {gv.get('reason') or 'gate Partial'}",
                    "evidence_href": f"/rfx/{rid}/vendor/{vid}/questionnaire",
                    "label": f"Gate Partial · {qid}",
                }
            )
    eligible_ids = _eligible_ids_for_state(state)
    # Selected pairs from live quality-gated split
    selected_pairs: set[tuple] = set()
    try:
        from . import snapshots
        live = snapshots.live_award_calculation(state)
        for r in (live.get("split") or {}).get("rows") or []:
            if r.get("winner_id") and r.get("line_no") is not None:
                selected_pairs.add((r["winner_id"], r["line_no"]))
    except Exception:
        pass
    return [_annotate_exception_class(d, eligible_ids, selected_pairs) for d in derived]



def _persisted_by_key(state: dict) -> dict[str, dict]:
    ensure_exceptions(state)
    return {e["key"]: e for e in state["exceptions"] if e.get("key")}


def _bucket(status: str) -> str:
    if status in OPEN_STATUSES or status == "open":
        return "open"
    if status in PENDING_STATUSES:
        return "pending"
    return "resolved"


def list_exceptions(state: dict, filter_status: str | None = None) -> list[dict]:
    """Merge derived open items with persisted exceptions for the UI."""
    ensure_exceptions(state)
    persisted = _persisted_by_key(state)
    derived = derive_open_exceptions(state)
    items: list[dict] = []
    seen: set[str] = set()

    for d in derived:
        p = persisted.get(d["key"])
        if p and p.get("status") in RESOLVED_STATUSES | PENDING_STATUSES:
            items.append({**d, **p, "source": "persisted"})
        elif p and p.get("status") == "rejected":
            items.append({**d, **p, "status": "open", "source": "rejected_reopen"})
        else:
            items.append(
                {
                    **d,
                    "id": (p or {}).get("id"),
                    "status": "open",
                    "note": (p or {}).get("note") or "",
                    "source": "derived",
                }
            )
        seen.add(d["key"])

    for key, p in persisted.items():
        if key in seen:
            continue
        if p.get("status") in RESOLVED_STATUSES | PENDING_STATUSES:
            items.append({**p, "source": "persisted_orphan"})

    if filter_status in ("open", "pending", "resolved"):
        items = [i for i in items if _bucket(i.get("status") or "open") == filter_status]
    if filter_status == "affects_current_recommendation":
        items = [i for i in items if i.get("affects_current_recommendation")]

    order = {"open": 0, "rejected": 0, "pending_approval": 1, "overridden": 2, "approved": 2}
    items.sort(
        key=lambda i: (
            order.get(i.get("status") or "open", 9),
            i.get("vendor_name") or "",
            i.get("line_no") or 0,
            i.get("gate_q_id") or "",
        )
    )
    return items


def counts(state: dict) -> dict:
    items = list_exceptions(state)
    c = {"open": 0, "pending": 0, "resolved": 0, "total": len(items)}
    for i in items:
        c[_bucket(i.get("status") or "open")] += 1
    return c


def has_blocking_exceptions(state: dict) -> bool:
    """True only when open items affect the *selected* award (Phase B.1).

    Excluded/non-selected vendor issues must NOT block freeze of a valid
    selected allocation.
    """
    for i in list_exceptions(state):
        if (i.get("status") or "open") not in ("open", "rejected", "pending_approval"):
            continue
        if i.get("blocks_freeze") or i.get("class") in (
            "selected_award_blocker",
            "coverage_gap",
        ):
            return True
    return False


def cleared_knockouts(state: dict) -> set[tuple[str, str]]:
    """(vendor_id, q_id) pairs cleared via overridden/approved gate exceptions."""
    ensure_exceptions(state)
    out: set[tuple[str, str]] = set()
    for e in state["exceptions"]:
        if e.get("status") not in RESOLVED_STATUSES:
            continue
        if e.get("kind") not in ("gate_fail", "gate_partial"):
            continue
        vid, qid = e.get("vendor_id"), e.get("gate_q_id")
        if vid and qid:
            out.add((vid, qid))
    return out


def _upsert(state: dict, record: dict) -> dict:
    ensure_exceptions(state)
    key = record["key"]
    for i, e in enumerate(state["exceptions"]):
        if e.get("key") == key:
            state["exceptions"][i] = record
            return record
    state["exceptions"].append(record)
    return record


def _find_derived(state: dict, key: str) -> dict | None:
    for d in derive_open_exceptions(state):
        if d["key"] == key:
            return d
    return None


def _base_fields(base: dict) -> dict:
    keys = (
        "key",
        "kind",
        "vendor_id",
        "vendor_name",
        "line_no",
        "gate_q_id",
        "reason",
        "evidence_href",
        "label",
    )
    return {k: base.get(k) for k in keys}


def _apply_cell_review(state: dict, record: dict, note: str) -> None:
    """Write a reviews entry so build_comparison marks the cell reviewed/usable."""
    from . import engine

    vid = record.get("vendor_id")
    line_no = record.get("line_no")
    if not vid or line_no is None:
        return
    vendor = next((v for v in state.get("vendors", []) if v["vendor_id"] == vid), None)
    if not vendor:
        return

    clean = [
        r
        for r in state.get("reviews", [])
        if not (r["vendor_id"] == vid and r["line_no"] == line_no)
    ]
    cmp = engine.build_comparison({**state, "reviews": clean})
    cell = None
    for ln in cmp["lines"]:
        if ln["line_no"] == line_no:
            cell = ln["cells"].get(vid)
            break
    value = None
    if cell:
        value = cell.get("unit_inr")
        if value is None:
            value = cell.get("unit_inr_candidate")

    state["reviews"] = clean
    if value is not None:
        entry = {
            "vendor_id": vid,
            "vendor_name": vendor.get("name") or record.get("vendor_name") or vid,
            "line_no": line_no,
            "action": "override",
            "value_inr": float(value),
            "note": note,
            "at": _now(),
            "via": "exceptions",
        }
    else:
        entry = {
            "vendor_id": vid,
            "vendor_name": vendor.get("name") or record.get("vendor_name") or vid,
            "line_no": line_no,
            "action": "accept",
            "value_inr": None,
            "note": note,
            "at": _now(),
            "via": "exceptions",
        }
    state["reviews"].append(entry)


def override_exception(state: dict, key: str, note: str) -> dict:
    note = (note or "").strip()
    if not note:
        raise ValueError("A note is required to override an exception.")
    derived = _find_derived(state, key)
    persisted = _persisted_by_key(state).get(key)
    base = derived or persisted
    if not base:
        raise ValueError("Exception not found.")
    if (persisted or {}).get("status") == "pending_approval":
        raise ValueError("This exception is pending manager approval. Approve or reject it first.")

    record = {
        **_base_fields(base),
        "id": (persisted or {}).get("id") or _eid(),
        "status": "overridden",
        "note": note,
        "manager_name": "",
        "manager_email": "",
        "created_at": (persisted or {}).get("created_at") or _now(),
        "resolved_at": _now(),
        "approval_note": "",
    }
    if record.get("vendor_id") and record.get("line_no") is not None:
        _apply_cell_review(state, record, note)
        scenario.append_buyer_review_log(
            state,
            {
                "source": "exceptions",
                "vendor_id": record.get("vendor_id"),
                "vendor_name": record.get("vendor_name"),
                "line_no": record.get("line_no"),
                "action": "override",
                "note": note,
                "exception_key": record.get("key"),
            },
        )
    _upsert(state, record)
    vids = [record["vendor_id"]] if record.get("vendor_id") else []
    snapshots.bump_vendor_data_version(state, "review_overridden", affected_vendor_ids=vids)
    return record


def request_approval(
    state: dict,
    key: str,
    note: str,
    manager_name: str = "",
    manager_email: str = "",
) -> dict:
    note = (note or "").strip()
    if not note:
        raise ValueError("A note is required to request approval.")
    derived = _find_derived(state, key)
    persisted = _persisted_by_key(state).get(key)
    base = derived or persisted
    if not base:
        raise ValueError("Exception not found.")

    record = {
        **_base_fields(base),
        "id": (persisted or {}).get("id") or _eid(),
        "status": "pending_approval",
        "note": note,
        "manager_name": (manager_name or "").strip(),
        "manager_email": (manager_email or "").strip(),
        "created_at": (persisted or {}).get("created_at") or _now(),
        "resolved_at": None,
        "approval_note": "",
    }
    _upsert(state, record)
    scenario.append_buyer_review_log(
        state,
        {
            "source": "approval",
            "vendor_id": record.get("vendor_id"),
            "vendor_name": record.get("vendor_name"),
            "line_no": record.get("line_no"),
            "action": "request_approval",
            "note": note,
            "exception_key": record.get("key"),
        },
    )

    to = record["manager_email"] or "manager@example.com"
    mgr = record["manager_name"] or "Manager"
    title = (state.get("rfx") or {}).get("title") or state["id"]
    subject = f"Approval needed: exception on {title}"
    where = (
        f"L{record['line_no']}"
        if record.get("line_no") is not None
        else (record.get("gate_q_id") or "—")
    )
    body = (
        f"Dear {mgr},\n\n"
        f"A buyer has requested approval to override an exception on RFx {state['id']}.\n\n"
        f"Vendor: {record.get('vendor_name') or '—'}\n"
        f"Line / gate: {where}\n"
        f"Reason: {record.get('reason')}\n"
        f"Buyer note: {note}\n\n"
        f"Open the Exceptions tab to Approve or Reject (demo — no real email).\n"
    )
    state.setdefault("outbox", []).append(
        {
            "kind": "exception_approval",
            "to": to,
            "vendor_id": record.get("vendor_id"),
            "vendor_name": record.get("vendor_name") or mgr,
            "subject": subject,
            "body": body,
            "sent_at": _now(),
            "delivery": "stubbed (no SMTP)",
            "exception_id": record["id"],
            "exception_key": record["key"],
        }
    )
    return record


def approve_exception(state: dict, key: str, note: str = "") -> dict:
    ensure_exceptions(state)
    persisted = _persisted_by_key(state).get(key)
    if not persisted or persisted.get("status") != "pending_approval":
        raise ValueError("No pending approval request for this exception.")
    note = (note or "").strip() or persisted.get("note") or "Approved by manager"
    record = {**persisted, "status": "approved", "approval_note": note, "resolved_at": _now()}
    if record.get("vendor_id") and record.get("line_no") is not None:
        _apply_cell_review(state, record, note)
    _upsert(state, record)
    scenario.append_buyer_review_log(
        state,
        {
            "source": "approval",
            "vendor_id": record.get("vendor_id"),
            "vendor_name": record.get("vendor_name"),
            "line_no": record.get("line_no"),
            "action": "approved",
            "note": note,
            "exception_key": record.get("key"),
        },
    )
    vids = [record["vendor_id"]] if record.get("vendor_id") else []
    snapshots.bump_vendor_data_version(state, "review_overridden", affected_vendor_ids=vids)
    return record


def reject_exception(state: dict, key: str, note: str = "") -> dict:
    ensure_exceptions(state)
    persisted = _persisted_by_key(state).get(key)
    if not persisted or persisted.get("status") != "pending_approval":
        raise ValueError("No pending approval request for this exception.")
    note = (note or "").strip() or "Rejected by manager"
    record = {**persisted, "status": "rejected", "approval_note": note, "resolved_at": _now()}
    _upsert(state, record)
    return record


def counts_by_class(state: dict) -> dict:
    items = list_exceptions(state)
    out = {"total": len(items), "affects_current_recommendation": 0}
    for i in items:
        cls = i.get("class") or "informational"
        out[cls] = out.get(cls, 0) + 1
        if i.get("affects_current_recommendation") and (i.get("status") or "open") in ("open", "rejected", "pending_approval"):
            out["affects_current_recommendation"] += 1
    return out
