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


def test_complete_freeze_post_redirects_with_error_flash_when_blocked():
    """Plain form POST must not blank-page or silently no-op when complete freeze fails."""
    st = _seed()
    rid = st["id"]
    check = freeze.validate_freeze_request(storage.load_state(rid), mode="complete")
    assert check["ok"] is False
    assert scenario.recommendation_lifecycle(storage.load_state(rid)).get("can_freeze") is True

    r = client.post(
        f"/rfx/{rid}/award/freeze",
        data={"freeze_mode": "complete"},
        follow_redirects=False,
    )
    assert r.status_code in (303, 302), r.text[:500]
    loc = r.headers.get("location") or ""
    assert "/award" in loc
    assert not (r.headers.get("HX-Redirect") or r.headers.get("hx-redirect"))

    st2 = storage.load_state(rid)
    assert not (st2.get("freeze") and st2["freeze"].get("status") == "frozen")
    flash = st2.get("flash") or {}
    assert flash.get("level") == "error"
    assert "uncovered" in (flash.get("message") or "").lower() or "allocation" in (
        flash.get("message") or ""
    ).lower()
    assert "partial" in (flash.get("message") or "").lower()

    page = client.get(f"/rfx/{rid}/award")
    assert page.status_code == 200
    assert 'data-testid="award-flash"' in page.text
    assert "Error." in page.text
    assert "Not frozen yet" in page.text or "not frozen" in page.text.lower()


def test_partial_freeze_post_persists_and_redirects_for_plain_form():
    st = _seed()
    rid = st["id"]
    r = client.post(
        f"/rfx/{rid}/award/freeze",
        data={
            "freeze_mode": "partial",
            "partial_reason": "Line 30 uncovered; acknowledging coverage gap for demo.",
            "acknowledgement": ["coverage_gaps", "selected_blockers"],
        },
        follow_redirects=False,
    )
    assert r.status_code in (303, 302), (r.status_code, r.headers, r.text[:300])
    assert "/award" in (r.headers.get("location") or "")
    # Must not be empty HX-only success for a normal browser form
    assert r.text == "" or "HX-Redirect" not in r.headers or r.headers.get("location")

    st2 = storage.load_state(rid)
    pack = st2.get("freeze") or {}
    assert pack.get("status") == "frozen"
    assert pack.get("freeze_mode") == "partial"
    flash = st2.get("flash") or {}
    assert flash.get("level") == "success"
    assert "frozen" in (flash.get("message") or "").lower()

    page = client.get(f"/rfx/{rid}/award")
    assert page.status_code == 200
    assert "Frozen" in page.text
    assert 'data-testid="award-flash"' in page.text


def test_award_page_distinguishes_ready_attempt_vs_complete_clear():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    html = r.text
    # Golden seed: can_freeze true but complete blocked by uncovered line 30
    assert 'data-testid="freeze-ready-attempt"' in html
    assert "Checklist clear for complete freeze" not in html
    assert 'data-testid="freeze-complete-blocked"' in html
    assert "uncovered" in html.lower() or "30" in html
    checklist = scenario.freeze_ux_checklist(
        storage.load_state(st["id"]),
        live=snapshots.live_award_calculation(storage.load_state(st["id"])),
        life=scenario.recommendation_lifecycle(storage.load_state(st["id"])),
        has_blocking_exceptions=True,
    )
    assert checklist["ready_to_attempt"] is True
    assert checklist["complete_ok"] is False
    assert checklist["all_ready_for_freeze"] is False
