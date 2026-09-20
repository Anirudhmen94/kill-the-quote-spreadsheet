"""Award freeze UX: checklist, inline save-recommendation, unlock can_freeze."""
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


def test_award_page_shows_ready_to_freeze_checklist():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    html = r.text
    assert "Ready to freeze?" in html
    assert 'data-testid="ready-to-freeze"' in html
    assert "Open anomalies" in html or "anomalies" in html.lower()
    assert "Current recommendation" in html or "recommendation saved" in html.lower()
    assert "Calculation available" in html
    # With golden seed rec present, freeze button is enabled (no disabled title-only path)
    assert 'data-testid="freeze-blocked-plain"' not in html or "can_freeze" in html
    assert "Freeze this award" in html
    # Must not rely only on title= tooltip for the block reason when blocked —
    # golden seed has a saved rec so button enabled; separate test covers blocked path.


def test_award_page_blocked_shows_plain_language_not_only_tooltip():
    st = _seed()
    _clear_recs(st)
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    html = r.text
    assert "Ready to freeze?" in html
    assert "Save recommendation to unlock freeze" in html
    assert 'data-testid="freeze-blocked-plain"' in html
    assert "write a short rationale" in html.lower()
    # Disabled freeze button should not be the only place the reason lives
    assert "title=\"Save the current calculation as a recommendation" not in html
    assert 'id="award-save-recommendation-form"' in html
    assert 'name="rationale"' in html
    assert "Ask the analyst instead" in html


def test_save_recommendation_from_award_unlocks_can_freeze():
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

    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert "Ready to freeze?" in page.text
    assert 'data-testid="freeze-blocked-plain"' not in page.text
    # Enabled primary freeze CTA (not the grey disabled button)
    assert 'cursor-not-allowed' not in page.text.split("Freeze this award")[0][-200:]


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
    assert any("recommendation" in e.lower() or "Save" in e for e in check["errors"])


def test_freeze_blocked_human_copy():
    st = demo_ops.build_golden_seed()
    st["recommendations"] = []
    st["recommendation"] = None
    life = scenario.recommendation_lifecycle(st)
    human = scenario.freeze_blocked_human(life)
    assert human
    assert "rationale" in human.lower()
    assert life["freeze_blocked_reason"].startswith("Save the current")
