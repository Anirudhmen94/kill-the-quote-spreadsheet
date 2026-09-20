"""Freeze API remains for back-compat; Award page no longer exposes lock/freeze UX."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import demo_ops, freeze, scenario, snapshots, storage

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def _clear_recs(st: dict) -> dict:
    st["recommendations"] = []
    st["recommendation"] = None
    storage.save_state(st["id"], st)
    return st


def test_award_page_has_ask_not_lock():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    html = r.text
    assert "Ask the analyst" in html
    assert "Lock award" not in html
    assert 'data-testid="award-lock-btn"' not in html
    assert "Ready to freeze?" not in html
    assert 'data-testid="ready-to-freeze"' not in html
    assert 'data-testid="award-lock-rationale"' not in html
    assert "Send award drafts" in html


def test_save_recommendation_api_still_works():
    st = _seed()
    _clear_recs(st)
    life0 = scenario.recommendation_lifecycle(storage.load_state(st["id"]))
    assert life0.get("can_freeze") is False

    r = client.post(
        f"/rfx/{st['id']}/award/save-recommendation",
        data={
            "rationale": "Quality-gated split is defensible; line 30 uncovered acknowledged for partial path.",
            "summary": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (303, 302), r.text[:500]
    assert "/award" in (r.headers.get("location") or "")

    st2 = storage.load_state(st["id"])
    rec = st2.get("recommendation")
    assert rec and rec.get("status") == "current"
    assert rec.get("rationale") or "Buyer rationale" in (rec.get("recommendation_markdown") or "")
    assert rec.get("vendor_data_version") == snapshots.current_version(st2)
    assert rec.get("calculation_snapshot_id")
    life = scenario.recommendation_lifecycle(st2)
    assert life.get("can_freeze") is True
    assert life.get("freeze_blocked_reason") is None


def test_save_recommendation_requires_rationale():
    st = _seed()
    _clear_recs(st)
    r = client.post(
        f"/rfx/{st['id']}/award/save-recommendation",
        data={"rationale": "   ", "summary": "ignored"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Rationale" in r.text or "rationale" in r.text.lower()
    st2 = storage.load_state(st["id"])
    assert not any(x.get("status") == "current" for x in (st2.get("recommendations") or []))
    assert scenario.recommendation_lifecycle(st2).get("can_freeze") is False


def test_freeze_still_blocked_without_saved_recommendation():
    st = _seed()
    _clear_recs(st)
    check = freeze.validate_freeze_request(
        storage.load_state(st["id"]),
        mode="partial",
        partial_reason="gap",
        acknowledgements=["coverage_gaps", "selected_blockers"],
    )
    assert check["ok"] is False
    msgs = [e["message"] if isinstance(e, dict) else str(e) for e in check["errors"]]
    assert any("recommendation" in e.lower() or "Save" in e for e in msgs)


def test_freeze_blocked_human_copy():
    st = demo_ops.build_golden_seed()
    st["recommendations"] = []
    st["recommendation"] = None
    life = scenario.recommendation_lifecycle(st)
    assert life.get("can_freeze") is False
    assert life.get("freeze_blocked_reason")


def test_freeze_api_partial_still_works():
    """Leftover freeze API for tests/back-compat — not exposed on Award UI."""
    st = _seed()
    rid = st["id"]
    r = client.post(
        f"/rfx/{rid}/award/freeze",
        data={
            "freeze_mode": "partial",
            "partial_reason": "Line 30 uncovered; API back-compat test.",
            "acknowledgement": ["coverage_gaps", "selected_blockers"],
            "confirm_assumed": "true",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st2 = storage.load_state(rid)
    pack = st2.get("freeze") or {}
    assert pack.get("status") == "frozen"
    assert pack.get("freeze_mode") == "partial"
    # Award page still must not advertise freeze/lock as the buyer path
    page = client.get(f"/rfx/{rid}/award")
    assert "Lock award" not in page.text
    assert "Freeze complete" not in page.text
