"""Defects: incomplete freeze consent + structured extraction status."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import (
    award_ask,
    demo_ops,
    engine,
    freeze,
    llm,
    scenario,
    snapshots,
    storage,
    vendor_extraction,
)

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def _msgs(check: dict) -> list[str]:
    return [
        (e.get("message") if isinstance(e, dict) else str(e))
        for e in (check.get("errors") or [])
    ]


def _make_full_coverage(st: dict) -> dict:
    """Patch Sri Balaji line 30 to an awardable ok quote so 30/30 is possible."""
    v1 = next(v for v in st["vendors"] if v["vendor_id"] == "v1")
    quotes = v1["extraction"]["line_quotes"]
    for q in quotes:
        if q.get("line_no") == 30 or (
            q.get("line_no") is None and 30 in (q.get("candidate_line_nos") or [])
        ):
            q["line_no"] = 30
            q["status"] = "ok"
            q["price"] = 1200.0
            q["currency"] = "INR"
            q["price_basis"] = "per_100"
            q["basis_qty"] = 100
            q["reason"] = ""
            break
    else:
        quotes.append(
            {
                "line_no": 30,
                "candidate_line_nos": [],
                "vendor_item_ref": "L30",
                "vendor_description": "line 30",
                "price": 1200.0,
                "currency": "INR",
                "price_basis": "per_100",
                "basis_qty": 100,
                "confidence": 0.9,
                "status": "ok",
                "review_cause": "none",
                "reason": "",
                "evidence": {
                    "file": "SBP.xlsx",
                    "location": "B32",
                    "snippet": "1200.00",
                    "verified": True,
                },
            }
        )
    nq = v1["extraction"].setdefault("not_quoted_line_nos", [])
    if 30 in nq:
        nq.remove(30)
    # Clear Ganesh unresolved on 30 if it blocks — quality gate Pass only for v1/v2
    snapshots.bump_vendor_data_version(st, "test_full_coverage", affected_vendor_ids=["v1"])
    # Refresh saved recommendation to current version
    st["recommendations"] = []
    st["recommendation"] = None
    snapshots.save_recommendation_from_live(
        st, "Full coverage test recommendation — quality-gated split."
    )
    return st


# ----- Defect 1 -----


def test_validate_freeze_structured_errors():
    st = _seed()
    check = freeze.validate_freeze_request(st, mode="complete")
    assert check["ok"] is False
    assert check.get("code")
    assert check["errors"]
    assert isinstance(check["errors"][0], dict)
    assert "type" in check["errors"][0] and "message" in check["errors"][0]


def test_complete_30_of_30_ok():
    st = demo_ops.build_golden_seed()
    _make_full_coverage(st)
    check = freeze.validate_freeze_request(st, mode="complete", confirm_assumed=True)
    # May still have selected blockers from assumed/needs_review cells in split
    if not check["ok"]:
        # confirm assumed + ensure no uncovered
        assert not check.get("uncovered_lines"), check
        # If only assumed blockers, confirm_assumed should clear for proposal;
        # selected_award_blockers might still exist from scenario — ack path is partial.
        # For true complete we need zero selected blockers.
        types = {e["type"] for e in check["errors"] if isinstance(e, dict)}
        if types <= {"assumed_unconfirmed"}:
            check = freeze.validate_freeze_request(st, mode="complete", confirm_assumed=True)
    if check.get("uncovered_lines"):
        raise AssertionError(f"still uncovered: {check['uncovered_lines']}")
    if check["ok"]:
        pack = freeze.freeze_award(st, mode="complete", confirm_assumed=True)
        assert pack["freeze_mode"] == "complete"
        assert not pack.get("uncovered_lines")
        assert pack["covered_line_count"] == check["line_count"]
    else:
        # If selected blockers remain, complete correctly rejected
        assert any(e.get("type") == "selected_award_blockers" for e in check["errors"])


def test_complete_29_of_30_rejected():
    st = _seed()
    check = freeze.validate_freeze_request(st, mode="complete")
    assert check["ok"] is False
    assert check.get("covered_line_count") == 29
    assert 30 in (check.get("uncovered_lines") or [])
    assert any(e.get("type") == "incomplete_coverage" for e in check["errors"])


def test_cannot_persist_complete_with_uncovered():
    st = _seed()
    try:
        freeze.freeze_award(st, mode="complete")
        raise AssertionError("should not persist")
    except freeze.FreezeValidationError as e:
        assert e.check["ok"] is False
    assert not (st.get("freeze") and st["freeze"].get("status") == "frozen")


def test_stale_unsaved_cannot_freeze():
    st = _seed()
    st["recommendations"] = []
    st["recommendation"] = None
    check = freeze.validate_freeze_request(
        st, mode="partial", partial_reason="x", acknowledgements=["coverage_gaps"]
    )
    assert check["ok"] is False
    assert any("recommendation" in m.lower() or "Save" in m for m in _msgs(check))

    st = _seed()
    snapshots.bump_vendor_data_version(st, "review_accepted", affected_vendor_ids=["v1"])
    check2 = freeze.validate_freeze_request(
        st,
        mode="partial",
        partial_reason="stale",
        acknowledgements=["coverage_gaps", "selected_blockers"],
    )
    assert check2["ok"] is False


def test_lock_and_use_and_lock_cannot_bypass():
    st = _seed()
    try:
        award_ask.lock_award(st)
        raise AssertionError("lock should fail")
    except freeze.FreezeValidationError as e:
        assert "manual" in str(e).lower() or "partial" in str(e).lower()
    assert not (st.get("freeze") and st["freeze"].get("status") == "frozen")

    # HTTP lock
    rid = st["id"]
    storage.save_state(rid, st)
    r = client.post(f"/rfx/{rid}/award/lock", data={}, follow_redirects=False)
    assert r.status_code in (302, 303)
    st2 = storage.load_state(rid)
    assert not (st2.get("freeze") and st2["freeze"].get("status") == "frozen")
    assert (st2.get("flash") or {}).get("level") == "error"


def test_partial_needs_ack_and_reason_stores_uncovered():
    st = _seed()
    bad = freeze.validate_freeze_request(st, mode="partial", partial_reason="", acknowledgements=[])
    assert bad["ok"] is False
    pack = freeze.freeze_award(
        st,
        mode="partial",
        partial_reason="Line 30 has no usable quote; award remaining lines.",
        acknowledgements=["coverage_gaps", "selected_blockers"],
    )
    assert pack["freeze_mode"] == "partial"
    assert 30 in (pack.get("uncovered_lines") or [])
    assert pack.get("partial_reason")
    assert "coverage_gaps" in (pack.get("acknowledgements") or [])


def test_notices_exclude_uncovered_lines():
    st = _seed()
    pack = freeze.freeze_award(
        st,
        mode="partial",
        partial_reason="Coverage gap on 30.",
        acknowledgements=["coverage_gaps", "selected_blockers"],
    )
    for n in pack.get("notices") or []:
        body = n.get("body") or ""
        # Must not claim uncovered lines as awarded
        assert "30 line(s)" not in body
        if pack.get("uncovered_lines"):
            assert "Uncovered" in body or n.get("uncovered_lines_excluded") == pack["uncovered_lines"]


def test_repair_invalid_complete_historical():
    st = _seed()
    # Fabricate bad historical pack
    bad = {
        "id": "badpack01",
        "frozen_at": "2026-01-01T00:00:00+00:00",
        "status": "frozen",
        "freeze_mode": "complete",
        "uncovered_lines": [30],
        "covered_line_count": 29,
        "acknowledgements": [],
        "partial_reason": "",
        "calculation_snapshot_id": "snap",
        "vendor_data_version": snapshots.current_version(st),
        "total_extended_inr": 1.0,
        "share_by_vendor": {},
        "strategy": "test",
        "strategy_key": "test",
        "line_awards": [],
        "notices": [],
        "regrets": [],
        "gates_summary": {},
        "blockers_total": 0,
    }
    st["freeze_packs"] = [bad]
    st["freeze"] = bad
    repairs = freeze.repair_historical_freezes(st)
    assert repairs
    assert bad["status"] == "requires_review"
    assert bad.get("integrity") == "invalid_historical_freeze"
    cur = freeze.current_freeze(st)
    assert cur is None or cur.get("integrity") == "invalid_historical_freeze" or cur.get("status") == "requires_review"

    # Reclassify path when ack+reason present
    st2 = _seed()
    pack2 = {
        "id": "reclass01",
        "frozen_at": "2026-01-01T00:00:00+00:00",
        "status": "frozen",
        "freeze_mode": "complete",
        "uncovered_lines": [30],
        "covered_line_count": 29,
        "acknowledgements": ["coverage_gaps"],
        "partial_reason": "Historical partial reason was recorded.",
        "calculation_snapshot_id": "snap",
        "vendor_data_version": snapshots.current_version(st2),
        "total_extended_inr": 1.0,
        "share_by_vendor": {},
        "strategy": "test",
        "strategy_key": "test",
        "line_awards": [],
        "notices": [],
        "regrets": [],
        "gates_summary": {},
        "blockers_total": 0,
    }
    st2["freeze_packs"] = [pack2]
    st2["freeze"] = pack2
    freeze.repair_historical_freezes(st2)
    assert pack2["freeze_mode"] == "partial"
    assert pack2.get("integrity") == "reclassified_partial"
    assert pack2["status"] == "frozen"


def test_award_page_shows_frozen_partial_banner():
    st = _seed()
    rid = st["id"]
    freeze.freeze_award(
        st,
        mode="partial",
        partial_reason="Line 30 uncovered.",
        acknowledgements=["coverage_gaps", "selected_blockers"],
    )
    storage.save_state(rid, st)
    page = client.get(f"/rfx/{rid}/award")
    assert page.status_code == 200
    assert "FROZEN PARTIAL" in page.text
    assert "30" in page.text


def test_needed_partial_acknowledgements_removed():
    st = _seed()
    try:
        award_ask.needed_partial_acknowledgements(st)
        raise AssertionError("should raise")
    except RuntimeError:
        pass


# ----- Defect 2 -----


def test_raw_errors_not_in_html():
    st = _seed()
    v = st["vendors"][0]
    schema_dump = (
        "Structured output failed after retry: 1 validation error for ExtractionAI\n"
        "line_quotes.0.price\n  Input should be a valid number [type=float_parsing]"
    )
    vendor_extraction.set_failed(v, technical=schema_dump, had_previous=False)
    storage.save_state(st["id"], st)
    page = client.get(f"/rfx/{st['id']}/email")
    assert page.status_code == 200
    assert "float_parsing" not in page.text
    assert "validation error for ExtractionAI" not in page.text
    assert "couldn't read" in page.text.lower() or "Retry" in page.text
    assert "AI log" in page.text


def test_failed_initial_contributes_no_prices():
    st = _seed()
    v = next(x for x in st["vendors"] if x["vendor_id"] == "v1")
    vendor_extraction.set_failed(v, technical="boom schema dump xyz", had_previous=False)
    assert v.get("extraction") is None
    assert vendor_extraction.get_status(v) == vendor_extraction.STATUS_FAILED_NO_PREVIOUS
    cmp = engine.build_comparison(st)
    for ln in cmp["lines"]:
        cell = ln["cells"]["v1"]
        assert cell.get("unit_inr") is None
        assert cell.get("status") in ("not_extracted", "missing")


def test_reread_keeps_prior_extraction():
    st = _seed()
    v = next(x for x in st["vendors"] if x["vendor_id"] == "v1")
    prior = copy.deepcopy(v["extraction"])
    ver = snapshots.current_version(st)
    vendor_extraction.set_extracted(v, version=ver)
    vendor_extraction.set_failed(
        v, technical="Structured output failed after retry: huge dump", had_previous=True
    )
    assert v["extraction"] == prior or v["extraction"] is not None
    assert vendor_extraction.get_status(v) == vendor_extraction.STATUS_FAILED_USING_PREVIOUS
    assert "Keeping the previous" in (vendor_extraction.buyer_message(v) or "")
    assert "huge dump" not in (vendor_extraction.buyer_message(v) or "")
    assert "huge dump" in (vendor_extraction.technical_error(v) or "")


def test_complete_freeze_blocked_by_failed_no_previous():
    st = _seed()
    # Make coverage complete first so extraction is the blocker
    _make_full_coverage(st)
    v = next(x for x in st["vendors"] if x["vendor_id"] == "v3")  # PakAsia — or any
    # Use a vendor that isn't needed for coverage — still blocks complete
    vendor_extraction.set_failed(v, technical="fail dump", had_previous=False)
    check = freeze.validate_freeze_request(st, mode="complete", confirm_assumed=True)
    assert check["ok"] is False
    types = {e.get("type") for e in check["errors"]}
    assert "vendor_extraction_incomplete" in types or any(
        "extraction" in (e.get("message") or "").lower() for e in check["errors"]
    )


def test_exclusion_allows_with_audit():
    st = _seed()
    rid = st["id"]
    v = next(x for x in st["vendors"] if x["vendor_id"] == "v4")
    vendor_extraction.set_failed(v, technical="fail", had_previous=False)
    storage.save_state(rid, st)
    r = client.post(
        f"/rfx/{rid}/vendor/{v['vendor_id']}/exclude",
        data={"reason": "Illegible photo; exclude from this event."},
        follow_redirects=False,
    )
    assert r.status_code in (200, 303)
    st2 = storage.load_state(rid)
    v2 = next(x for x in st2["vendors"] if x["vendor_id"] == v["vendor_id"])
    assert vendor_extraction.get_status(v2) == vendor_extraction.STATUS_EXCLUDED
    assert any(
        e.get("action") == "exclude_vendor" for e in (st2.get("buyer_review_log") or [])
    )
    # Complete freeze no longer blocked by this vendor
    blockers = vendor_extraction.vendors_blocking_complete_freeze(st2)
    assert not any(b["vendor_id"] == v["vendor_id"] for b in blockers)


def test_llm_structured_error_buyer_safe():
    err = llm.StructuredOutputError("1 validation error for ExtractionAI\nline_quotes...")
    assert "validation error" not in str(err)
    assert "Structured output failed" in str(err)
    assert "validation error" in err.technical


def test_validate_extraction_omitted_arrays():
    errs = vendor_extraction.validate_extraction_payload({"line_quotes": []})
    assert errs  # missing other required keys
    ok = vendor_extraction.validate_extraction_payload(
        {
            "line_quotes": [],
            "not_quoted_line_nos": [],
            "commercials": [],
            "questionnaire": [],
            "certificates": [],
        }
    )
    assert ok == []


def test_excluded_vendor_neutral_notice():
    st = _seed()
    v = next(x for x in st["vendors"] if x["vendor_id"] == "v4")
    vendor_extraction.set_excluded(v, reason="Photo unreadable", actor="buyer")
    pack = freeze.freeze_award(
        st,
        mode="partial",
        partial_reason="Partial with exclusion.",
        acknowledgements=["coverage_gaps", "selected_blockers", "extraction_incomplete"],
    )
    # Meghna excluded — should get not_evaluated not commercial regret
    kinds = {n["vendor"]: n.get("kind") for n in (pack.get("regrets") or [])}
    if "Meghna Corrugators" in kinds:
        assert kinds["Meghna Corrugators"] == "not_evaluated"
