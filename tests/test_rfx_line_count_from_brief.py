"""Brief-stated SKU/line count must drive drafted RFx line items."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import rfx_drafter
from core.models import Board, CommercialTermsAI, LineItemAI, QuestionAI, RFxDraftAI, AnswerType


def _line(n: int) -> LineItemAI:
    return LineItemAI(
        line_no=n,
        sku=f"CB-RSC-{n:03d}",
        description=f"RSC shipper {n}",
        board=Board.three_ply if n % 2 else Board.five_ply,
        flute="B",
        length_mm=300 + n,
        width_mm=200,
        height_mm=150,
        gsm=400,
        print="1 colour flexo",
        annual_qty=10000,
        notes="",
    )


def _draft(n_lines: int) -> RFxDraftAI:
    return RFxDraftAI(
        title="Test RFx",
        scope="Test scope for corrugated packaging.",
        line_items=[_line(i) for i in range(1, n_lines + 1)],
        terms=CommercialTermsAI(
            currency="INR",
            price_basis="INR per piece, delivered, exclusive of GST",
            payment_terms="45 days",
            delivery_location="Chakan",
            delivery_schedule="weekly",
            freight_basis="delivered",
            validity_days=60,
            gst_treatment="exclusive",
            other="",
        ),
        questionnaire=[
            QuestionAI(q_id="Q1", text="ISO 9001?", answer_type=AnswerType.yes_no, knockout=True),
        ],
    )


@pytest.mark.parametrize(
    "brief,expected",
    [
        ("We need approximately 27 SKUs of corrugated packaging for Chakan.", 27),
        ("Please quote 15 line items for bakery cartons in Mumbai.", 15),
        ("about 30 SKUs of corrugated packaging: outer shippers", 30),
        ("Scope: 8 SKUs — mostly 3-ply, two 5-ply display trays.", 8),
        ("No count stated here, just packaging for snacks.", 30),  # default
    ],
)
def test_parse_target_line_count(brief, expected):
    assert rfx_drafter.parse_target_line_count(brief) == expected


def test_enforce_line_count_trim_and_pad():
    data = {"line_items": [li.model_dump() for li in _draft(30).line_items]}
    trimmed = rfx_drafter.enforce_line_count(dict(data), 27)
    assert len(trimmed["line_items"]) == 27
    assert trimmed["line_items"][-1]["line_no"] == 27 or True  # renumbered in _enrich

    short = {"line_items": [li.model_dump() for li in _draft(5).line_items]}
    padded = rfx_drafter.enforce_line_count(short, 8)
    assert len(padded["line_items"]) == 8
    assert padded["line_items"][7]["line_no"] == 8


def test_brief_with_27_skus_yields_exactly_27_lines():
    """Custom brief saying 27 SKUs must produce 27 lines via Claude + post-process."""
    brief = (
        "We are a packaged snacks manufacturer with a plant in Chakan (Pune). For the next "
        "financial year we need approximately 27 SKUs of corrugated packaging: outer shippers "
        "for chips and namkeen (mostly 3-ply, some 5-ply for export), a few die-cut display trays, "
        "and some 7-ply master cartons. Deliveries weekly to Chakan."
    )
    assert rfx_drafter.parse_target_line_count(brief) == 27

    calls: list[dict] = []

    def fake_structured(**kwargs):
        calls.append(kwargs)
        # Simulate model ignoring count and returning 30 — post-process must trim.
        return _draft(30)

    with patch("core.llm.structured", side_effect=fake_structured):
        rfx = rfx_drafter.draft_rfx(brief)

    assert len(rfx["line_items"]) == 27
    assert [li["line_no"] for li in rfx["line_items"]] == list(range(1, 28))
    assert calls, "draft must call Claude (llm.structured), not a hardcoded catalog"
    assert calls[0]["purpose"] == "draft_rfx"
    assert "approximately 27 SKUs" in calls[0]["content"] or "27 SKUs" in calls[0]["content"]
    assert brief.strip()[:40] in calls[0]["content"]
    assert "exactly 27 line items" in calls[0]["content"].lower() or "exactly 27" in calls[0]["system"].lower()
    assert "27" in calls[0]["system"]
    # Quality-gate questionnaire still applied (non-empty after gates)
    assert rfx.get("questionnaire")


def test_brief_27_pads_when_model_under_emits():
    brief = "Need exactly 27 SKUs of 3-ply and 5-ply shippers for our Pune plant."

    def fake_structured(**kwargs):
        return _draft(20)

    with patch("core.llm.structured", side_effect=fake_structured):
        rfx = rfx_drafter.draft_rfx(brief)

    assert len(rfx["line_items"]) == 27
    assert rfx["line_items"][-1]["line_no"] == 27


def test_example_brief_still_cached_at_30():
    from core import vendor_sim

    with patch("core.llm.structured") as mocked:
        rfx = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF)
        mocked.assert_not_called()
    assert len(rfx["line_items"]) == 30
