"""Award redesign: Ask card, top-2, assign, checks, send (no freeze/lock UX)."""
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
    sole = award_packs.line_why_reason(
        {"runner_up": None, "gap_pct": None}, gate_grade="Pass"
    )
    assert "sole usable quote" in sole
    assert "gate Pass" in sole
    dash = award_packs.line_why_reason({"runner_up": "—", "gap_pct": 1.0}, gate_grade="Pass")
    assert "sole usable quote" in dash
    assert award_packs.format_gap_pct(10.0) == "+10%"
    assert award_packs.format_gap_pct(5.2) == "+5.2%"
    assert award_packs.format_gap_pct(None) is None


def test_vendor_award_packs_group_by_winner():
    st = demo_ops.build_golden_seed()
    live = snapshots.live_award_calculation(st)
    packs = award_packs.vendor_award_packs(
        live["split"],
        cmp=live["cmp"],
        gates=live["gates"],
        total_extended_inr=live["total_extended_inr"],
    )
    assert packs, "expected at least one winning vendor pack"
    names = [p["vendor"] for p in packs]
    assert "Kraftline Industries" in names
    assert "Sri Balaji Packaging" in names
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
    assert abs(sum(p["share_pct"] for p in packs) - 100) < 1.5
    uncovered = award_packs.uncovered_line_rows(live["split"])
    assert any(u["line_no"] == 30 for u in uncovered)


def test_award_page_ask_top2_assign_send():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    html = r.text

    assert "Ask the analyst" in html
    assert 'data-testid="award-ask-card"' in html
    assert 'data-testid="award-ask-premade-best_split"' in html
    assert 'data-testid="award-ask-premade-who_to_drop"' in html
    assert 'data-testid="award-ask-premade-biggest_risks"' in html
    assert 'data-testid="award-ask-textarea"' in html

    assert "Suggest top 2" in html
    assert "Kraftline Industries" in html
    assert "Sri Balaji Packaging" in html
    assert "Assign by line" in html
    assert "Send award drafts" in html
    assert 'data-testid="award-send-btn"' in html

    assert "Lock award" not in html
    assert "Ready to freeze?" not in html
    assert "Manual lock / freeze" not in html
    assert "Freeze complete" not in html
    assert "Preview notices" not in html
    assert "Step 1 · Recommendation" not in html
    assert 'id="award-filters"' not in html


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
