"""Award Ask (premade/live) + one-click Lock redesign."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import award_ask, demo_ops, freeze, llm, scenario, snapshots, storage

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


def test_premade_returns_without_anthropic():
    st = _seed()
    with mock.patch.object(llm, "agent_loop", side_effect=AssertionError("Claude must not run for premade")):
        r = client.post(
            f"/rfx/{st['id']}/award/ask-premade",
            data={"prompt_id": "best_split"},
        )
    assert r.status_code == 200
    assert "Recommendation" in r.text or "Pass" in r.text
    assert 'data-testid="award-ask-answer"' in r.text
    assert "Use this recommendation" in r.text
    st2 = storage.load_state(st["id"])
    cache = st2.get("award_ask_cache") or {}
    assert any(str(k).startswith("best_split:v") for k in cache)

    with mock.patch.object(llm, "agent_loop", side_effect=AssertionError("Claude must not run for cached premade")):
        r2 = client.post(
            f"/rfx/{st['id']}/award/ask-premade",
            data={"prompt_id": "best_split"},
        )
    assert r2.status_code == 200
    assert "cached" in r2.text.lower() or "premade" in r2.text.lower()


def test_premade_who_to_drop_and_risks():
    st = _seed()
    for pid in ("who_to_drop", "biggest_risks"):
        r = client.post(f"/rfx/{st['id']}/award/ask-premade", data={"prompt_id": pid})
        assert r.status_code == 200, pid
        assert "Recommendation" in r.text


def test_lock_one_click_auto_partial_with_existing_rec():
    st = _seed()
    rid = st["id"]
    assert scenario.recommendation_lifecycle(storage.load_state(rid)).get("can_freeze") is True
    assert freeze.validate_freeze_request(storage.load_state(rid), mode="complete")["ok"] is False

    r = client.post(f"/rfx/{rid}/award/lock", data={}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/award" in (r.headers.get("location") or "")

    st2 = storage.load_state(rid)
    pack = st2.get("freeze") or {}
    assert pack.get("status") == "frozen"
    assert pack.get("freeze_mode") == "partial"
    flash = st2.get("flash") or {}
    assert flash.get("level") == "success"
    assert "locked successfully" in (flash.get("message") or "").lower()
    assert "partial" in (flash.get("message") or "").lower()

    page = client.get(f"/rfx/{rid}/award")
    assert page.status_code == 200
    assert 'data-testid="award-flash"' in page.text
    assert "Success" in page.text


def test_lock_one_click_saves_rec_then_auto_partial():
    st = _seed()
    _clear_recs(st)
    rid = st["id"]
    assert scenario.recommendation_lifecycle(storage.load_state(rid)).get("can_freeze") is False

    r = client.post(
        f"/rfx/{rid}/award/lock",
        data={"rationale": "Quality-gated split; uncovered line 30 acknowledged for partial lock."},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st2 = storage.load_state(rid)
    assert st2.get("recommendation")
    assert st2["recommendation"].get("rationale") or "Buyer rationale" in (
        st2["recommendation"].get("recommendation_markdown") or ""
    )
    pack = st2.get("freeze") or {}
    assert pack.get("status") == "frozen"
    assert pack.get("freeze_mode") == "partial"
    flash = st2.get("flash") or {}
    assert flash.get("level") == "success"
    assert "Recommendation saved" in (flash.get("message") or "")


def test_lock_requires_rationale_when_no_rec():
    st = _seed()
    _clear_recs(st)
    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert 'data-testid="award-lock-rationale"' in page.text
    assert 'name="rationale"' in page.text


def test_send_success_flash_still_works_after_lock():
    st = _seed()
    rid = st["id"]
    client.post(f"/rfx/{rid}/award/lock", data={}, follow_redirects=False)
    r = client.post(f"/rfx/{rid}/award/send-notices", follow_redirects=False)
    assert r.status_code in (302, 303)
    st2 = storage.load_state(rid)
    flash = st2.get("flash") or {}
    assert flash.get("level") == "success"
    assert "stub-sent" in (flash.get("message") or "").lower()
    page = client.get(f"/rfx/{rid}/award")
    assert 'data-testid="award-flash"' in page.text
    assert "Success" in page.text


def test_award_ask_module_cache_invalidates_on_version_bump():
    st = demo_ops.build_golden_seed()
    a1 = award_ask.get_or_build_premade(st, "best_split")
    assert a1.get("cache_hit") is False
    a2 = award_ask.get_or_build_premade(st, "best_split")
    assert a2.get("cache_hit") is True
    snapshots.bump_vendor_data_version(st, "test_bump", affected_vendor_ids=["v1"])
    a3 = award_ask.get_or_build_premade(st, "best_split")
    assert a3.get("cache_hit") is False
    assert a3.get("vendor_data_version") == snapshots.current_version(st)
