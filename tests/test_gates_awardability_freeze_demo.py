"""Phases 1–3: gates, awardability, freeze, demo reset, exclusion SSOT."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import awardability, demo_ops, engine, freeze, gates, snapshots


def test_gates_pass_partial_fail():
    st = demo_ops.build_golden_seed()
    g = gates.evaluate_gates(st)
    assert g["summary"]["pass"] >= 1
    assert g["summary"]["partial"] >= 1
    # PakAsia / Meghna / possibly Ganesh are Partial — never silent Pass
    by_name = {r["name"]: r for r in g["vendors"]}
    assert by_name["Meghna Corrugators"]["grade"] == "Partial"
    assert by_name["PakAsia Global"]["grade"] == "Partial"
    assert by_name["Sri Balaji Packaging"]["grade"] == "Pass"
    assert by_name["Kraftline Industries"]["grade"] == "Pass"


def test_awardability_excludes_ugly_edges():
    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    ex = cmp["exclusion_summary"]
    assert ex["needs_review"] >= 1  # Sri Balaji alternate flute
    assert ex["assumed"] >= 1  # Sri Balaji currency assumed
    assert ex["unresolved"] >= 1  # Ganesh same-as-last-year
    assert ex["not_quoted"] >= 1  # Kraftline / Meghna gaps
    # Headline must not relabel needs-review as assumed, or unresolved as conversion-failed
    assert "needs review" in ex["headline"]
    assert "assumed" in ex["headline"]
    assert "unresolved" in ex["headline"]
    assert "conversion failed" not in ex["headline"] or ex["conversion_failed"] > 0
    # Every excluded cell must carry a buyer-facing reason
    assert all(e.get("reason") for e in cmp["exclusions"])
    # Assumed never may_freeze
    assumed_cells = [
        ln["cells"][v["vendor_id"]]
        for ln in cmp["lines"]
        for v in cmp["vendors"]
        if ln["cells"][v["vendor_id"]].get("awardability") == "assumed"
    ]
    assert assumed_cells
    assert all(not c.get("may_freeze") for c in assumed_cells)
    # Needs-review cells are their own bucket, not assumed
    nr_cells = [
        ln["cells"][v["vendor_id"]]
        for ln in cmp["lines"]
        for v in cmp["vendors"]
        if ln["cells"][v["vendor_id"]].get("awardability") == "needs_review"
    ]
    assert nr_cells
    assert all(c.get("status") == "needs_review" for c in nr_cells)


def test_quality_gated_live_matches_compare_exclusions():
    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    live = snapshots.live_award_calculation(st)
    assert live["available"]
    assert "quality-gated" in live["strategy"]
    # Same exclusion headline SSOT
    assert live["exclusion_summary"]["headline"] == cmp["exclusion_summary"]["headline"]
    # Only Pass-gate vendors eligible
    eligible = set(live["split"].get("eligible_vendors") or [])
    pass_names = {r["name"] for r in cmp["gates"]["vendors"] if r["grade"] == "Pass"}
    assert eligible <= pass_names
    # Partial vendors must not win lines under default strategy
    for r in live["split"]["rows"]:
        if r.get("winner"):
            assert r["winner"] in pass_names


def test_freeze_blocks_assumed_and_invalidates_on_bump():
    st = demo_ops.build_golden_seed()
    pack = freeze.freeze_award(st, confirm_assumed=False, require_quality_gate=True)
    assert pack["status"] == "frozen"
    assert pack["calculation_snapshot_id"]
    # Assumed lines listed as blocked (or absent from winners)
    assert "assumed_blocked_lines" in pack
    z = freeze.export_freeze_zip(st, pack)
    assert len(z) > 1000
    # Vendor edit → historical
    snapshots.bump_vendor_data_version(st, "review_accepted", affected_vendor_ids=["v1"])
    freeze.refresh_freeze_staleness(st)
    assert st["freeze"]["status"] == "historical"


def test_assumed_requires_confirm_to_enter_freeze_totals():
    st = demo_ops.build_golden_seed()
    without = freeze.build_award_proposal(st, require_quality_gate=True, confirm_assumed=False)
    with_confirm = freeze.build_award_proposal(st, require_quality_gate=True, confirm_assumed=True)
    # Confirming assumed should not reduce covered lines vs blocking them
    assert with_confirm["covered_line_count"] >= without["covered_line_count"]


def test_interview_reset_restores_messy_seed_and_clears_stale_ask():
    st = demo_ops.build_golden_seed()
    # Pollute with a stale-looking answer
    st["chat"].append(
        {
            "id": "old",
            "question": "stale",
            "answer": "should be wiped",
            "status": "stale",
            "vendor_data_version": 0,
            "trace": [],
            "tables": [],
            "caveats": [],
        }
    )
    rid = st["id"]
    fresh = demo_ops.interview_reset(existing_id=rid)
    assert fresh["id"] == rid
    assert fresh.get("is_golden_seed")
    assert len(fresh["vendors"]) == 5
    assert len(fresh["chat"]) == 1
    assert fresh["chat"][0]["status"] == "current"
    assert fresh["recommendation"]["status"] == "current"
    assert fresh["chat"][0]["vendor_data_version"] == fresh["vendor_data_version"]
    # Messy edges still present
    cmp = awardability.enrich_state_comparison(fresh)
    assert cmp["exclusion_summary"]["not_quoted"] >= 1
    assert cmp["exclusion_summary"]["unresolved"] >= 1  # Ganesh same-as-last-year
    assert any(v["gate"] == "Partial" for v in cmp["vendors"])


def test_demo_mode_guards_regenerate():
    st = demo_ops.build_golden_seed()
    assert demo_ops.is_demo_mode(st)
    # Overwrite blocked while seed already has files
    ok, msg = demo_ops.guard_destructive(st, "regenerate_replies")
    assert not ok
    assert "Demo mode" in msg
    # First simulate (no files) allowed even in demo mode
    empty = {**st, "vendors": [{**v, "files": [], "extraction": None, "status": "awaiting"} for v in st["vendors"]]}
    ok_first, _ = demo_ops.guard_destructive(empty, "regenerate_replies")
    assert ok_first
    demo_ops.set_demo_mode(st, False)
    ok2, _ = demo_ops.guard_destructive(st, "regenerate_replies")
    assert ok2
    # Missing key defaults to off
    assert not demo_ops.is_demo_mode({"id": "x"})


def test_compare_ask_award_share_exclusion_ssot():
    """Phase 1: same snapshot ⇒ same coverage / exclusions / totals language."""
    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    live = snapshots.live_award_calculation(st)
    assert cmp["exclusion_summary"]["headline"] == live["exclusion_summary"]["headline"]
    # Seeded Ask answer caveats mention the exclusion headline
    assert any(cmp["exclusion_summary"]["headline"] in (c or "") for c in st["chat"][0].get("caveats", []))



def test_kraftline_coverage_chip_adds_to_line_count():
    """Partial-quote chip math: usable + review + unresolved + not_quoted == 30."""
    from core import edge_callouts

    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    kraft = next(v for v in cmp["vendors"] if "Kraftline" in v["name"])
    parts = edge_callouts.vendor_coverage_parts(kraft, cmp["line_count"])
    assert parts["sums_to_total"]
    assert parts["usable"] + parts["needs_review"] + parts["unresolved"] + parts["not_quoted"] == 30
    assert parts["quoted"] == parts["usable"] + parts["needs_review"] + parts["unresolved"]
    assert parts["usable"] == 27
    assert parts["not_quoted"] == 3
    text = edge_callouts.coverage_chip_text(kraft["name"], parts)
    assert "27 usable" in text and "27 quoted" in text and "3 not quoted" in text
    # Must not claim "quoted 27/30 (3 not quoted)" without usable/review breakdown when review>0
    # (here review=0 so quoted==usable is fine)


def test_exclusion_banner_labels_map_true_states():
    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    ex = cmp["exclusion_summary"]
    # Fake a bulk needs-review mislabel scenario is impossible now: headline uses buckets
    assert ex["needs_review"] == sum(
        1 for e in cmp["exclusions"] if e["awardability"] == "needs_review"
    )
    assert ex["assumed"] == sum(1 for e in cmp["exclusions"] if e["awardability"] == "assumed")
    assert ex["unresolved"] == sum(1 for e in cmp["exclusions"] if e["awardability"] == "unresolved")
    # Status strip uses the same headline (cell-level, not "N lines excluded")
    from core import snapshots

    ds = snapshots.data_status(st, context="compare")
    assert "cells excluded from totals" in ds["text"]
    assert "lines excluded from totals" not in ds["text"]
    assert ex["headline"] in ds["text"]


def test_meghna_angled_photo_callout_on_compare_and_award():
    from core import edge_callouts

    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    callouts = edge_callouts.edge_callouts(cmp)
    photo = [c for c in callouts if c["kind"] == "angled_photo"]
    assert photo, "Meghna angled phone / rate card photo must be a first-class callout"
    assert any("angled phone photo" in c["text"].lower() and "rate card" in c["text"].lower() for c in photo)
    # Low-confidence / per-pack chips also name the photo source
    meghna_texts = [c["text"].lower() for c in callouts if c.get("vendor") and "Meghna" in (c.get("vendor") or "")]
    assert any("angled phone" in t or "rate card" in t for t in meghna_texts)


def test_award_and_compare_share_edge_callouts():
    """Award must not truncate edges that Compare shows (same helper, full list)."""
    from core import edge_callouts

    st = demo_ops.build_golden_seed()
    cmp = awardability.enrich_state_comparison(st)
    callouts = edge_callouts.edge_callouts(cmp)
    kinds = {c["kind"] for c in callouts}
    # Ganesh unresolved + per-kg + Meghna photo/low-conf must all be present
    assert "unresolved" in kinds
    assert "per_kg" in kinds
    assert "low_confidence" in kinds or "angled_photo" in kinds
    assert "angled_photo" in kinds
    # No silent truncation — Award template iterates the full list
    assert len(callouts) > 6


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
