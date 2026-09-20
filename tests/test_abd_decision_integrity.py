"""Phases A/B/D — decision integrity, scenario exceptions, seeded demo QA."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import (
    awardability,
    demo_ops,
    engine,
    exceptions,
    freeze,
    gates,
    scenario,
    snapshots,
)


def _seed():
    return demo_ops.build_golden_seed()


def test_excluded_vendor_issues_do_not_block_selected_award():
    st = _seed()
    items = exceptions.list_exceptions(st)
    excluded = [i for i in items if i.get("class") == scenario.EXC_EXCLUDED_VENDOR]
    assert excluded, "expected excluded-vendor issues from Partial/Fail vendors"
    for i in excluded:
        assert i.get("blocks_freeze") is False
        assert i.get("affects_current_recommendation") is False


def test_selected_quote_problems_are_blockers():
    st = _seed()
    items = exceptions.list_exceptions(st)
    selected = [i for i in items if i.get("class") == scenario.EXC_SELECTED_BLOCKER]
    assert selected
    assert all(i.get("blocks_freeze") for i in selected)


def test_coverage_gaps():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    assert 30 in (cmp.get("coverage_gaps") or [])
    gaps = [i for i in exceptions.list_exceptions(st) if i.get("class") == scenario.EXC_COVERAGE_GAP]
    assert gaps and gaps[0]["line_no"] == 30


def test_analyst_readiness_labels():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    eligible = gates.gate_filter_vendors(cmp, cmp["gates"], True)
    res = scenario.compute_award_scenario(
        cmp,
        strategy="quality_gated_cheapest",
        vendor_ids=eligible,
        require_quality_gate=True,
        vendor_data_version=snapshots.current_version(st),
    )
    assert res.readiness in {
        scenario.READY_AWARD,
        scenario.READY_CONDITIONAL,
        scenario.READY_COMMERCIAL_ONLY,
        scenario.READY_NOT_EXECUTABLE,
    }
    assert res.readiness_headline
    assert res.readiness != scenario.READY_AWARD or not res.coverage_gaps


def test_save_before_freeze():
    st = _seed()
    st["recommendations"] = []
    st["recommendation"] = None
    life = scenario.recommendation_lifecycle(st)
    assert life.get("can_freeze") is False
    check = freeze.validate_freeze_request(
        st, mode="partial", partial_reason="x", acknowledgements=["coverage_gaps"]
    )
    assert check["ok"] is False
    assert any("Save" in e or "recommendation" in e.lower() for e in check["errors"])


def test_stale_on_data_change():
    st = _seed()
    life = scenario.recommendation_lifecycle(st)
    assert life["lifecycle"] in (scenario.REC_SAVED, "saved", "current")
    snapshots.bump_vendor_data_version(st, "review_accepted", affected_vendor_ids=["v1"])
    life2 = scenario.recommendation_lifecycle(st)
    assert life2["lifecycle"] == scenario.REC_STALE
    assert life2.get("can_freeze") is False


def test_complete_freeze_fails_incomplete():
    st = _seed()
    check = freeze.validate_freeze_request(st, mode="complete")
    assert check["ok"] is False
    assert any("allocation" in e.lower() or "uncovered" in e.lower() for e in check["errors"])
    try:
        freeze.freeze_award(st, mode="complete")
        raise AssertionError("complete freeze should fail")
    except ValueError:
        pass


def test_partial_freeze_needs_ack_and_reason():
    st = _seed()
    bad = freeze.validate_freeze_request(st, mode="partial", partial_reason="", acknowledgements=[])
    assert bad["ok"] is False
    acks = ["coverage_gaps"]
    preview = freeze.validate_freeze_request(
        st, mode="partial", partial_reason="gap", acknowledgements=acks
    )
    if not preview["ok"]:
        for e in preview["errors"]:
            if "acknowledgements:" in e:
                acks = list({*acks, *[x.strip() for x in e.split("acknowledgements:")[-1].split(",")]})
    ok = freeze.validate_freeze_request(
        st,
        mode="partial",
        partial_reason="Line 30 has no usable quote; award 29 lines.",
        acknowledgements=acks,
    )
    assert ok["ok"], ok["errors"]
    pack = freeze.freeze_award(
        st,
        mode="partial",
        partial_reason="Line 30 has no usable quote; award 29 lines.",
        acknowledgements=acks,
    )
    assert pack["status"] == "frozen"
    assert pack["freeze_mode"] == "partial"
    assert pack.get("immutable") is True


def test_unconfirmed_discounts_excluded():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    eligible = gates.gate_filter_vendors(cmp, cmp["gates"], True)
    res = scenario.compute_award_scenario(
        cmp,
        strategy="quality_gated_cheapest",
        vendor_ids=eligible,
        require_quality_gate=True,
        discounts_confirmed=False,
    )
    disc = res.conditional_discounts
    assert disc.get("potential_paise", 0) > 0
    assert disc.get("applied_paise", 0) == 0
    assert res.total_extended_paise == disc.get("total_before_paise")


def test_blended_rate_across_dissimilar_specs():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    flags = cmp.get("blended_rate_flags") or scenario.detect_blended_rates(cmp)
    pak = [f for f in flags if "Pak" in f["vendor"]]
    assert pak, "PakAsia lines 3–15 must be flagged as blended"
    assert pak[0]["line_count"] >= 4
    vid = pak[0]["vendor_id"]
    for ln in cmp["lines"]:
        if ln["line_no"] in pak[0]["line_nos"]:
            assert ln["cells"][vid].get("excluded_blended_rate")


def test_pakasia_style_recompute_and_gated_premium():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    live = snapshots.live_award_calculation(st)
    un = engine.cheapest_per_line(cmp, None, False, False)
    gated = live["total_extended_inr"]
    assert gated > un["total_extended_inr"]
    assert 30 in (live.get("uncovered_lines") or [])
    pass_names = {v["name"] for v in cmp["gates"]["vendors"] if v["grade"] == "Pass"}
    for name in live.get("share_by_vendor") or {}:
        assert name in pass_names


def test_override_log_from_multiple_entry_points():
    st = _seed()
    items = exceptions.list_exceptions(st)
    target = next(i for i in items if i.get("vendor_id") == "v1" and i.get("line_no") == 5)
    exceptions.override_exception(st, target["key"], "exceptions override note")
    # Second entry point: evidence-style log for another open cell
    other = next(i for i in exceptions.list_exceptions(st) if i.get("vendor_id") == "v1" and i.get("line_no") == 8)
    scenario.append_buyer_review_log(
        st,
        {
            "source": "evidence",
            "vendor_id": "v1",
            "vendor_name": "Sri Balaji Packaging",
            "line_no": 8,
            "action": "override",
            "value_inr": 10.0,
            "note": "evidence accept",
            "exception_key": other.get("key"),
        },
    )
    log = st.get("buyer_review_log") or []
    sources = {e.get("source") for e in log}
    assert "evidence" in sources
    assert "exceptions" in sources
    assert any(r.get("line_no") == 5 and r.get("vendor_id") == "v1" for r in st.get("reviews") or [])
    assert any(r.get("line_no") == 8 and r.get("vendor_id") == "v1" for r in st.get("reviews") or [])


def test_market_vs_scenario_coverage():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    mkt = cmp.get("market_quote_coverage") or scenario.market_quote_coverage(cmp).as_dict()
    assert mkt["covered"] == 29
    assert mkt["total"] == 30
    live = snapshots.live_award_calculation(st)
    assert live["covered_line_count"] == 29


def test_seed_gate_mix_and_from_app_import():
    st = _seed()
    g = gates.evaluate_gates(st)
    assert g["summary"]["pass"] >= 2
    assert g["summary"]["fail"] >= 1
    assert g["summary"]["partial"] >= 1
    by = {v["name"]: v["grade"] for v in g["vendors"]}
    assert by["Sri Balaji Packaging"] == "Pass"
    assert by["Kraftline Industries"] == "Pass"
    assert by["Meghna Corrugators"] == "Fail"
    assert by["PakAsia Global"] == "Partial"
    fresh = demo_ops.interview_reset(st["id"]) if "existing_id" not in str(demo_ops.interview_reset.__code__.co_varnames) else demo_ops.interview_reset(existing_id=st["id"])
    assert fresh.get("is_golden_seed") or fresh.get("demo_mode") or fresh.get("is_demo_seed")
    from app import app as fastapi_app
    assert fastapi_app is not None


def test_award_scenario_result_paise_ssot():
    st = _seed()
    cmp = awardability.enrich_state_comparison(st)
    eligible = gates.gate_filter_vendors(cmp, cmp["gates"], True)
    res = scenario.compute_award_scenario(cmp, vendor_ids=eligible, require_quality_gate=True)
    assert isinstance(res.total_extended_paise, int)
    assert abs(res.total_extended_inr * 100 - res.total_extended_paise) < 1
    d = res.as_dict()
    assert "total_extended_paise" in d
    assert "market_quote_coverage" in d and "scenario_award_coverage" in d
