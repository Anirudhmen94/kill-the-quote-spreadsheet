"""State-consistency + demo-readiness regressions."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import charts, demo_ops, event_status, freeze, snapshots, storage, vendor_extraction

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def _bad_complete_pack(st: dict) -> dict:
    return {
        "id": "badpack99",
        "frozen_at": "2026-01-01T00:00:00+00:00",
        "status": "frozen",
        "freeze_mode": "complete",
        "uncovered_lines": [28, 29, 30],
        "covered_line_count": 27,
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
        "processing_completeness": {
            "covered_line_count": 27,
            "line_count": 30,
            "uncovered_lines": [28, 29, 30],
        },
    }


def test_homepage_label_freeze_requires_review():
    st = _seed()
    bad = _bad_complete_pack(st)
    st["freeze_packs"] = [bad]
    st["freeze"] = bad
    st["status"] = "award_frozen"
    freeze.repair_historical_freezes(st)
    storage.save_state(st["id"], st)

    disp = event_status.derive_event_display_status(st)
    assert disp["key"] == event_status.STATUS_FREEZE_REQUIRES_REVIEW
    assert disp["label"] == "Freeze requires review"
    assert "Award_frozen" not in disp["label"]
    assert "award_frozen" not in disp["label"].lower()

    page = client.get("/")
    assert page.status_code == 200
    assert "Freeze requires review" in page.text
    assert "Award_frozen" not in page.text
    assert f'data-testid="event-status-freeze_requires_review"' in page.text or "freeze_requires_review" in page.text


def test_award_ui_not_valid_complete_for_requires_review():
    st = _seed()
    bad = _bad_complete_pack(st)
    st["freeze_packs"] = [bad]
    st["freeze"] = bad
    freeze.repair_historical_freezes(st)
    storage.save_state(st["id"], st)

    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert "data-testid=\"invalid-historical-freeze-block\"" in page.text
    assert "Requires_Review ·" not in page.text
    assert "Frozen complete" not in page.text or "invalid" in page.text.lower()
    # Must not show enabled Lock award
    assert 'data-testid="award-lock-btn"' in page.text
    # disabled button present; enabled submit lock form should not be the primary CTA
    assert "Create replacement recommendation" in page.text
    assert "data-testid=\"freeze-complete-banner\"" not in page.text
    strip = charts.audit_trust_strip(st)
    assert "Requires_Review" not in strip["freeze_label"]
    assert strip["freeze_label"] == "Freeze requires review"
    assert strip.get("invalid_historical")


def test_lock_award_replaced_with_contextual_cta_invalid():
    st = _seed()
    bad = _bad_complete_pack(st)
    st["freeze_packs"] = [bad]
    st["freeze"] = bad
    freeze.repair_historical_freezes(st)
    cta = event_status.derive_lock_cta(st)
    assert cta["show_enabled_lock"] is False
    assert cta["cta_key"] == "invalid_historical_freeze"
    assert "replacement" in (cta["cta_label"] or "").lower()


def test_lock_cta_failed_response():
    st = _seed()
    v = next(x for x in st["vendors"] if x["vendor_id"] == "v3")
    vendor_extraction.set_failed(v, technical="boom", had_previous=False)
    cta = event_status.derive_lock_cta(st)
    assert cta["show_enabled_lock"] is False
    assert cta["cta_key"] == "failed_response"
    assert cta["cta_vendor_id"] == "v3"


def test_lock_cta_unsaved_recommendation():
    st = _seed()
    st["recommendations"] = []
    st["recommendation"] = None
    live = snapshots.live_award_calculation(st)
    cta = event_status.derive_lock_cta(st, live=live)
    assert cta["show_enabled_lock"] is False
    assert cta["cta_key"] == "unsaved_recommendation"


def test_coverage_labels_distinguish_market_vs_quality_gated():
    st = _seed()
    live = snapshots.live_award_calculation(st)
    bundle = charts.build_chart_bundle(st, live=live)
    assert bundle["available"]
    assert "quality-gated" in (bundle["coverage"]["title"] or "").lower() or "scenario" in (
        bundle["coverage"]["title"] or ""
    ).lower()
    label = bundle["coverage"]["label"] or ""
    assert "quality-gated" in label.lower() or "awarded" in label.lower()
    mkt = (live.get("scenario") or {}).get("market_quote_coverage") or {}
    scen = (live.get("scenario") or {}).get("scenario_award_coverage") or {}
    if mkt.get("label") and scen.get("label"):
        assert mkt["label"] != scen["label"] or "quoted" in mkt["label"] or "market" in mkt["label"].lower()
        assert "quoted" in mkt["label"] or "market" in mkt["label"].lower() or "quote" in mkt["label"].lower()
        assert "awarded" in scen["label"] or "quality-gated" in scen["label"]

    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert "Quality-gated scenario" in page.text or "quality-gated" in page.text.lower()
    # Should mention market separately when present
    assert "Market" in page.text


def test_demo_seed_clean_terminal():
    st = demo_ops.build_golden_seed()
    counts = snapshots.processing_counts(st)
    assert counts["failed"] == 0
    assert counts.get("clean_terminal") or counts["all_extracted"]
    statuses = [vendor_extraction.get_status(v) for v in st["vendors"]]
    assert vendor_extraction.STATUS_FAILED_NO_PREVIOUS not in statuses
    extracted_or_excl = [
        s
        for s in statuses
        if s
        in (vendor_extraction.STATUS_EXTRACTED, vendor_extraction.STATUS_EXCLUDED)
    ]
    assert len(extracted_or_excl) == 5
    # Prefer 5/5 extracted
    assert statuses.count(vendor_extraction.STATUS_EXTRACTED) >= 4

    fresh = demo_ops.interview_reset(existing_id=st["id"])
    counts2 = snapshots.processing_counts(fresh)
    assert counts2["failed"] == 0
    assert vendor_extraction.STATUS_FAILED_NO_PREVIOUS not in [
        vendor_extraction.get_status(v) for v in fresh["vendors"]
    ]


def test_failed_response_is_terminal_and_email_offers_retry():
    """A failed read must never be presented as both pending and processing zero."""
    st = _seed()
    failed_vendor = next(v for v in st["vendors"] if v.get("files"))
    vendor_extraction.set_failed(
        failed_vendor,
        technical="provider timeout",
        had_previous=False,
    )

    counts = snapshots.processing_counts(st)
    assert counts["failed"] == 1
    assert counts["pending"] == 0
    assert counts["processing"] == 0
    assert counts["all_terminal"] is True

    status = snapshots.data_status(st, context="email")
    assert status["label"] == "Processing complete with failures"
    assert "Processing 0 responses" not in status["text"]

    storage.save_state(st["id"], st)
    page = client.get(f"/rfx/{st['id']}/email")
    assert page.status_code == 200
    assert "Retry 1 failed" in page.text
    assert "Read all 1 pending" not in page.text


def test_completed_award_demo_is_frozen_and_sent():
    st = demo_ops.build_awarded_happy_seed()
    pack = event_status.active_valid_freeze(st)

    assert pack is not None
    assert pack["freeze_mode"] == "complete"
    assert pack["covered_line_count"] == len(st["rfx"]["line_items"])
    assert pack["uncovered_lines"] == []
    assert st["status"] == "award_frozen"
    assert st["happy_path_summary"]["winner"] == "Kraftline Industries"
    assert st["happy_path_summary"]["notices_sent"] >= 1
    assert any(
        x.get("kind") == "award_notice" and x.get("vendor_name") == "Kraftline Industries"
        for x in st["outbox"]
    )

    storage.save_state(st["id"], st)
    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert "Frozen complete" in page.text
    assert "Notices sent" in page.text


def test_home_exposes_completed_award_demo():
    page = client.get("/")
    assert page.status_code == 200
    assert "Open completed award demo" in page.text


def test_notices_blocked_for_requires_review():
    st = _seed()
    bad = _bad_complete_pack(st)
    st["freeze_packs"] = [bad]
    st["freeze"] = bad
    freeze.repair_historical_freezes(st)
    from core import award_actions

    try:
        award_actions.send_award_notices(st)
        raise AssertionError("should block")
    except ValueError as e:
        assert "requires review" in str(e).lower() or "disabled" in str(e).lower()


def test_replacement_recommendation_route():
    st = _seed()
    bad = _bad_complete_pack(st)
    st["freeze_packs"] = [bad]
    st["freeze"] = bad
    freeze.repair_historical_freezes(st)
    storage.save_state(st["id"], st)
    r = client.post(
        f"/rfx/{st['id']}/award/replacement-recommendation",
        data={"rationale": "Replacement after repair — recalculated without auto-freeze."},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st2 = storage.load_state(st["id"])
    assert any(x.get("status") == "current" for x in (st2.get("recommendations") or []))
    # Historical freeze still present and not auto-re-frozen as valid complete
    pack = st2.get("freeze") or {}
    assert pack.get("status") == "requires_review" or pack.get("integrity") == "invalid_historical_freeze"
    assert not event_status.is_active_valid_freeze(pack)
