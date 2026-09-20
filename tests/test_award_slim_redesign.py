"""Slim Award redesign: vendor packs, why-reasons, no filter/blockers chrome."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import award_packs, demo_ops, snapshots, storage

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def test_line_why_reason_deterministic():
    row = {
        "winner": "Kraftline Industries",
        "winner_id": "v2",
        "runner_up": "Sri Balaji Packaging",
        "gap_pct": 5.2,
    }
    why = award_packs.line_why_reason(row, gate_grade="Pass")
    assert why == (
        "Lowest usable INR/pc among Pass vendors · "
        "runner-up Sri Balaji Packaging +5.2% · gate Pass"
    )
    # Sole usable quote
    sole = award_packs.line_why_reason(
        {"runner_up": None, "gap_pct": None}, gate_grade="Pass"
    )
    assert "sole usable quote" in sole
    assert "gate Pass" in sole
    # Em-dash runner-up treated as missing
    dash = award_packs.line_why_reason({"runner_up": "—", "gap_pct": 1.0}, gate_grade="Pass")
    assert "sole usable quote" in dash
    # Gap formatting strips trailing zeros
    assert award_packs.format_gap_pct(10.0) == "+10%"
    assert award_packs.format_gap_pct(5.2) == "+5.2%"
    assert award_packs.format_gap_pct(None) is None


def test_vendor_award_packs_group_by_winner():
    st = demo_ops.build_golden_seed()
    live = snapshots.live_award_calculation(st)
    packs = award_packs.vendor_award_packs(
        live["split"], cmp=live["cmp"], gates=live["gates"], total_extended_inr=live["total_extended_inr"]
    )
    assert packs, "expected at least one winning vendor pack"
    names = [p["vendor"] for p in packs]
    assert "Kraftline Industries" in names
    assert "Sri Balaji Packaging" in names
    # Uncovered never mixed into packs
    uncovered_nos = set(live["uncovered_lines"] or [])
    for p in packs:
        assert p["lines_won"] == len(p["lines"])
        assert p["extended_inr"] > 0
        assert 0 <= p["share_pct"] <= 100
        for ln in p["lines"]:
            assert ln["line_no"] not in uncovered_nos
            assert ln["why_reason"]
            assert "Lowest usable INR/pc" in ln["why_reason"]
            assert "gate Pass" in ln["why_reason"]
    # Share percents roughly sum near 100
    assert abs(sum(p["share_pct"] for p in packs) - 100) < 1.5
    # Uncovered block separate
    uncovered = award_packs.uncovered_line_rows(live["split"])
    assert any(u["line_no"] == 30 for u in uncovered)


def test_award_page_slim_layout_no_old_chrome():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    html = r.text

    # Three steps present
    assert "Step 1 · Recommendation" in html
    assert "Step 2 · Award by vendor" in html
    assert "Step 3 · Lock" in html

    # Strategy + trust line
    assert "Quality-gated cheapest per line" in html
    assert "Vendor data v" in html

    # Vendor packs with why
    assert "Kraftline Industries" in html
    assert "Lowest usable INR/pc among Pass vendors" in html

    # Uncovered separate
    assert "Uncovered lines" in html

    # No Award filter bar / URL sync keys
    assert 'id="award-filters"' not in html
    assert "data-award-filter-item" not in html
    assert "awcov" not in html
    assert "awblock" not in html

    # Old aside sections gone
    assert "Data quality" not in html
    assert "Buyer review log" not in html
    # Blockers list heading (aside) gone — freeze advanced may still mention blockers
    assert ">Blockers" not in html and "Blockers (" not in html

    # Crowded header primaries gone
    assert "AI call log" not in html.split("Step 3")[0]  # not in header / steps 1–2
    assert "Export award workbook" not in html  # renamed / demoted
    assert "Freeze this award" in html
    assert "Send award" in html
    assert "Preview notices" in html
    assert "Advanced…" in html or "Advanced..." in html

    # Optional auditor table
    assert "All lines table" in html


def test_notice_preview_lists_winner_lines():
    st = demo_ops.build_golden_seed()
    live = snapshots.live_award_calculation(st)
    packs = award_packs.vendor_award_packs(live["split"], cmp=live["cmp"], gates=live["gates"])
    preview = award_packs.notice_previews(st, freeze_pack=None, live=live, vendor_packs=packs)
    assert preview["frozen"] is False
    assert preview["notices"]
    for n in preview["notices"]:
        assert n["line_nos"], f"expected line list for {n['vendor']}"
    assert preview["regrets"]
