"""Polish gaps: Ask single-flight, discount confirm official vs potential, Award banner once."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from core import awardability, demo_ops, gates, scenario, snapshots
from core.web import templates
from routes import ask as ask_routes


def _seed():
    return demo_ops.build_golden_seed()


def test_ask_single_flight_rejects_duplicate():
    """Second Ask while first is in-flight gets a visible 409 error (no duplicate run)."""
    st = _seed()
    ok1, _ = ask_routes._acquire_ask(st["id"])
    assert ok1 is True
    ok2, msg = ask_routes._acquire_ask(st["id"])
    assert ok2 is False
    assert "already running" in msg.lower()
    ask_routes._release_ask(st["id"])
    ok3, _ = ask_routes._acquire_ask(st["id"])
    assert ok3 is True
    ask_routes._release_ask(st["id"])


def test_ask_route_single_flight_via_http(tmp_path, monkeypatch):
    """HTTP Ask while locked returns error fragment; successful path always yields one message."""
    from core import storage
    from app import app

    st = _seed()
    # Point storage at an isolated dir for this test
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path, raising=False)
    if hasattr(storage, "_data_dir"):
        monkeypatch.setattr(storage, "_data_dir", tmp_path, raising=False)

    # Use real save/load against tmp if storage is filesystem-based
    storage.save_state(st["id"], st)

    # Force in-flight lock
    ask_routes._acquire_ask(st["id"])
    try:
        client = TestClient(app)
        r = client.post(f"/rfx/{st['id']}/ask", data={"question": "Who is cheapest?"})
        assert r.status_code == 409
        assert "already running" in r.text.lower() or "Something went wrong" in r.text
    finally:
        ask_routes._release_ask(st["id"])


def test_discount_confirm_official_vs_potential():
    st = _seed()
    live0 = snapshots.live_award_calculation(st)
    disc0 = live0["conditional_discounts"]
    assert disc0["potential_paise"] > 0
    assert disc0["applied_paise"] == 0
    assert live0["total_extended_paise"] == disc0["total_before_paise"]
    kraft = next(d for d in disc0["details"] if d["vendor_id"] == "v2")
    assert kraft["pct"] == 5.0
    assert kraft["confirmed"] is False

    # Confirm Kraftline only
    st["discount_confirmations"] = {
        "v2": {
            "confirmed": True,
            "confirmed_at": "2026-09-20T12:00:00+00:00",
            "confirmed_by": "Priya",
            "condition": kraft["condition"],
            "pct": 5.0,
            "vendor_name": kraft["vendor"],
        }
    }
    live1 = snapshots.live_award_calculation(st)
    disc1 = live1["conditional_discounts"]
    assert disc1["applied_paise"] == kraft["potential_saving_paise"]
    assert live1["total_extended_paise"] == disc0["total_before_paise"] - kraft["potential_saving_paise"]
    # Sri Balaji still potential-only
    balaji = next(d for d in disc1["details"] if d["vendor_id"] == "v1")
    assert balaji["confirmed"] is False
    kraft1 = next(d for d in disc1["details"] if d["vendor_id"] == "v2")
    assert kraft1["confirmed"] is True
    assert kraft1["confirmed_by"] == "Priya"
    # Potential (all confirmed) still lower than official after partial confirm
    assert disc1["potential_official_paise"] < live1["total_extended_paise"]


def test_discount_confirm_route_persists_actor_and_snapshot(monkeypatch, tmp_path):
    from core import storage
    from app import app

    st = _seed()
    storage.save_state(st["id"], st)
    client = TestClient(app)
    before = snapshots.current_version(st)
    r = client.post(
        f"/rfx/{st['id']}/award/confirm-discount",
        data={"vendor_id": "v2", "confirmed_by": "Priya Deshpande"},
        follow_redirects=False,
    )
    assert r.status_code in (303, 200)
    st2 = storage.load_state(st["id"])
    conf = (st2.get("discount_confirmations") or {}).get("v2")
    assert conf and conf.get("confirmed") is True
    assert conf.get("confirmed_by") == "Priya Deshpande"
    assert conf.get("confirmed_at")
    assert conf.get("condition")
    assert snapshots.current_version(st2) > before
    # Buyer review log entry
    log = st2.get("buyer_review_log") or []
    assert any(e.get("action") == "confirm_discount" for e in log)


def test_award_unsaved_banner_appears_once():
    """Template smoke: with live calc and no saved rec, the Ask CTA appears once (slim Award)."""
    from core import award_packs

    st = _seed()
    st["recommendations"] = []
    st["recommendation"] = None
    live = snapshots.live_award_calculation(st)
    life = scenario.recommendation_lifecycle(st)
    assert life.get("banner") in (None, "")  # freeze panel must not duplicate
    packs = award_packs.vendor_award_packs(live["split"], cmp=live["cmp"], gates=live["gates"])
    uncovered = award_packs.uncovered_line_rows(live["split"])
    unconfirmed = award_packs.unconfirmed_discounts(live.get("conditional_discounts"))
    html = templates.get_template("award.html").render(
        {
            "request": mock.Mock(),
            "state": st,
            "cmp": live["cmp"],
            "split": live["split"],
            "live": live,
            "current_rec": None,
            "historical_recs": [],
            "gates": live.get("gates"),
            "freeze_pack": None,
            "callouts": [],
            "active": "award",
            "has_blocking_exceptions": False,
            "flash": None,
            "recommendation_lifecycle": life,
            "market_quote_coverage": live["cmp"].get("market_quote_coverage"),
            "freeze_check_complete": {"ok": False, "errors": []},
            "discount_confirmations": {},
            "conditional_discounts": live.get("conditional_discounts"),
            "vendor_packs": packs,
            "uncovered_lines": uncovered,
            "notice_preview": {"frozen": False, "notices": [], "regrets": []},
            "unconfirmed_discounts": unconfirmed,
            "blended_rate_banner": None,
            "ai_ok": False,
            "model": "",
            "storage": "local",
            "rfx_id": st["id"],
            "data_status": None,
            "version_events": [],
            "vendor_data_version": live["vendor_data_version"],
            "demo_mode": True,
            "lifecycle": "award",
            "demo_prompts": [],
        }
    )
    phrase = "No recommendation saved yet"
    assert html.count(phrase) == 1, f"expected once, got {html.count(phrase)}"
    assert "Ask on Compare and save" in html
    # Confirm control present for unconfirmed discounts
    assert "Confirm into official total" in html
    assert "Conditional discounts not yet confirmed" in html


def test_narrative_validation_fallback_on_contradiction():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    eligible = gates.gate_filter_vendors(cmp, cmp["gates"], True)
    res = scenario.compute_award_scenario(
        cmp, vendor_ids=eligible, require_quality_gate=True, discounts_confirmed=False
    )
    # Invent a wildly wrong total + claim award-ready when not
    fake = (
        f"I recommend awarding now. The total is ₹{res.total_extended_inr * 3:,.2f}. "
        "This is award ready and ready to award."
    )
    # Force not_executable readiness for readiness check if needed
    check = scenario.validate_narrative_vs_engine(fake, res)
    # At least total mismatch should fire
    assert check["ok"] is False
    assert "narrative_total_mismatch" in check["problems"]
    assert check["fallback"]["total_extended_inr"] == res.total_extended_inr
    assert "deterministic fallback" in check["fallback"]["note"].lower()


def test_ask_panel_disables_while_running():
    """Template smoke: Ask form disables button+textarea and drops duplicate submits."""
    html = templates.get_template("partials/ask_panel.html").render(
        {
            "state": {"id": "rfx_test", "chat": []},
            "suggested": ["Who is cheapest?"],
            "ready": True,
            "ai_ok": True,
        }
    )
    assert 'hx-disabled-elt="#ask-btn, #ask-form textarea' in html
    assert "hx-sync=\"this:drop\"" in html or "hx-sync='this:drop'" in html
    assert "window.__askBusy" in html
