"""Ask + finalize plan: Compare premades cached; Award use-and-lock; manual lock."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import compare_ask, demo_ops, freeze, llm, scenario, snapshots, storage
from core.web import templates

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


def test_compare_premade_no_anthropic_and_caches():
    st = _seed()
    rid = st["id"]
    with mock.patch.object(llm, "agent_loop", side_effect=AssertionError("Claude must not run for compare premade")):
        r = client.post(
            f"/rfx/{rid}/ask/premade",
            data={"prompt_id": "quality_gated_split"},
        )
    assert r.status_code == 200
    assert "Recommendation" in r.text or "Pass" in r.text or "quality" in r.text.lower()
    assert 'id="msg-' in r.text or "Based on vendor data version" in r.text
    assert "premade" in r.text.lower()

    st2 = storage.load_state(rid)
    cache = st2.get("compare_ask_cache") or {}
    assert any(str(k).startswith("quality_gated_split:v") for k in cache)

    with mock.patch.object(llm, "agent_loop", side_effect=AssertionError("Claude must not run for cached compare premade")):
        r2 = client.post(
            f"/rfx/{rid}/ask/premade",
            data={"prompt_id": "quality_gated_split"},
        )
    assert r2.status_code == 200
    assert "cached" in r2.text.lower() or "premade" in r2.text.lower()


def test_compare_all_premades_engine_only():
    st = _seed()
    with mock.patch.object(llm, "agent_loop", side_effect=AssertionError("no Claude")):
        for p in compare_ask.COMPARE_PREMADES:
            r = client.post(f"/rfx/{st['id']}/ask/premade", data={"prompt_id": p["id"]})
            assert r.status_code == 200, p["id"]
            assert p["question"][:20] in r.text or "premade" in r.text.lower()


def test_compare_panel_labels_premade_vs_free():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    html = r.text
    assert "Premade (fast)" in html
    assert "Free ask (live)" in html
    assert 'data-testid="compare-ask-premade-quality_gated_split"' in html
    assert 'hx-post="/rfx/' in html and "/ask/premade" in html
    # Chips must POST premade, not fill textarea
    assert "t.value=this.textContent" not in html


def test_ask_panel_template_disables_premades_while_busy():
    html = templates.get_template("partials/ask_panel.html").render(
        {
            "state": {"id": "rfx_test", "chat": []},
            "compare_premades": [{"id": "quality_gated_split", "label": "QG"}],
            "ready": True,
            "ai_ok": True,
        }
    )
    assert 'hx-disabled-elt="#ask-btn, #ask-form textarea, #ask-premades button"' in html
    assert "hx-sync=\"this:drop\"" in html or "hx-sync='this:drop'" in html
    assert "window.__askBusy" in html
    assert "Premade (fast)" in html
    assert "Free ask (live)" in html


def test_award_premade_still_cached_free_ask_live_path_exists():
    st = _seed()
    with mock.patch.object(llm, "agent_loop", side_effect=AssertionError("Claude must not run for award premade")):
        r = client.post(
            f"/rfx/{st['id']}/award/ask-premade",
            data={"prompt_id": "best_split"},
        )
    assert r.status_code == 200
    assert "Apply" in r.text
    assert 'data-testid="award-ask-use-and-lock"' not in r.text

    page = client.get(f"/rfx/{st['id']}/award")
    assert "Premade (fast)" in page.text
    assert "Free ask (live)" in page.text
    assert 'hx-post="/rfx/' in page.text and "/award/ask\"" in page.text.replace("'", '"')


def test_use_and_lock_deprecated_redirects():
    """Use & lock removed — redirects with Apply + Send guidance; no freeze."""
    st = _seed()
    _clear_recs(st)
    rid = st["id"]
    r0 = client.post(f"/rfx/{rid}/award/ask-premade", data={"prompt_id": "best_split"})
    assert r0.status_code == 200
    st1 = storage.load_state(rid)
    idx = len(st1["chat"]) - 1

    r = client.post(f"/rfx/{rid}/award/use-and-lock/{idx}", follow_redirects=False)
    assert r.status_code in (200, 302, 303)
    loc = r.headers.get("location") or r.headers.get("HX-Redirect") or ""
    if r.status_code in (302, 303):
        assert "/award" in loc

    st2 = storage.load_state(rid)
    pack = st2.get("freeze") or {}
    assert pack.get("status") != "frozen"
    flash = st2.get("flash") or {}
    assert flash.get("level") == "error"


def test_recommendation_from_award_returns_award():
    st = _seed()
    _clear_recs(st)
    rid = st["id"]
    client.post(f"/rfx/{rid}/award/ask-premade", data={"prompt_id": "who_to_drop"})
    st1 = storage.load_state(rid)
    idx = len(st1["chat"]) - 1
    r = client.post(
        f"/rfx/{rid}/recommendation/{idx}",
        data={"return_to": "award"},
        follow_redirects=False,
    )
    assert r.status_code in (200, 302, 303)
    dest = r.headers.get("location") or r.headers.get("HX-Redirect") or ""
    assert "/award" in dest
    st2 = storage.load_state(rid)
    assert st2.get("recommendation", {}).get("status") == "current"


def test_award_page_has_send_not_manual_lock():
    st = _seed()
    rid = st["id"]
    page = client.get(f"/rfx/{rid}/award")
    assert page.status_code == 200
    assert "Send award drafts" in page.text
    assert 'data-testid="award-send-btn"' in page.text
    assert "Manual lock / freeze" not in page.text
    assert "Freeze complete" not in page.text

    r = client.post(f"/rfx/{rid}/award/lock", data={}, follow_redirects=False)
    assert r.status_code in (302, 303)
    st2 = storage.load_state(rid)
    assert (st2.get("freeze") or {}).get("status") != "frozen"
    assert (st2.get("flash") or {}).get("level") == "error"


def test_manual_freeze_partial_route():
    st = _seed()
    rid = st["id"]
    # Golden seed already has current rec; complete freeze blocked by coverage → use partial
    r = client.post(
        f"/rfx/{rid}/award/freeze",
        data={
            "freeze_mode": "partial",
            "acknowledgement": ["coverage_gaps", "selected_blockers"],
            "partial_reason": "Line 30 uncovered — accepting partial freeze for demo.",
            "confirm_assumed": "true",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text[:500]
    st2 = storage.load_state(rid)
    pack = st2.get("freeze") or {}
    assert pack.get("status") == "frozen"
    assert pack.get("freeze_mode") == "partial"


def test_compare_ask_module_cache_invalidates():
    st = demo_ops.build_golden_seed()
    a1 = compare_ask.get_or_build_premade(st, "usd_fx")
    assert a1.get("cache_hit") is False
    a2 = compare_ask.get_or_build_premade(st, "usd_fx")
    assert a2.get("cache_hit") is True
    snapshots.bump_vendor_data_version(st, "test_bump", affected_vendor_ids=["v1"])
    a3 = compare_ask.get_or_build_premade(st, "usd_fx")
    assert a3.get("cache_hit") is False
