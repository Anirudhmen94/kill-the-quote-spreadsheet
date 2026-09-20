"""Draft cache for EXAMPLE_BRIEF + smoke checks for parallel extract helpers."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import demo_ops, rfx_cache, rfx_drafter, vendor_sim


def test_example_brief_draft_uses_cache_not_llm():
    log: list = []
    with patch("core.llm.structured") as mocked:
        rfx = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF, log=log)
        mocked.assert_not_called()
    assert len(rfx["line_items"]) == 30
    assert all("nominal_weight_g" in li for li in rfx["line_items"])
    assert log and log[0]["purpose"] == "draft_rfx_cached"
    assert log[0]["latency_s"] <= 0.05
    assert "Chakan" in rfx["title"] or "Chakan" in rfx["scope"]


def test_example_brief_whitespace_normalized():
    messy = "  " + vendor_sim.EXAMPLE_BRIEF.replace(" ", "  \n ") + "\n"
    assert rfx_cache.is_example_brief(messy)
    log: list = []
    with patch("core.llm.structured") as mocked:
        rfx_drafter.draft_rfx(messy, log=log)
        mocked.assert_not_called()
    assert log[0]["purpose"] == "draft_rfx_cached"


def test_custom_brief_calls_llm():
    with patch("core.llm.structured") as mocked:
        from core.models import RFxDraftAI

        # Minimal valid-ish dump via side effect is heavy; just assert call kwargs.
        mocked.side_effect = RuntimeError("stop-after-assert")
        try:
            rfx_drafter.draft_rfx("We need 30 corrugated cartons for a bakery in Mumbai next year.")
        except RuntimeError as e:
            assert "stop-after-assert" in str(e)
        mocked.assert_called()
        kwargs = mocked.call_args.kwargs
        assert kwargs["purpose"] == "draft_rfx"
        assert kwargs.get("model")  # draft model override passed


def test_simulate_non_hex_id_and_photo_faster():
    rfx = demo_ops._rfx()
    state = {
        "id": "testdemo01",  # contains non-hex letters beyond a-f? 's','t','m','o' — non hex
        "rfx": rfx,
        "vendors": [vendor_sim.empty_vendor(v) for v in vendor_sim.VENDORS],
        "fx": {"as_of": "2026-09-15", "source": "t", "rates_to_inr": {"INR": 1.0, "USD": 83.50}},
        "status": "sent",
        "demo_mode": False,
    }
    t0 = time.time()
    vendor_sim.simulate_replies(state)
    elapsed = time.time() - t0
    assert all(v.get("files") for v in state["vendors"])
    # Photo-heavy path should finish well under 15s locally after optimisation
    assert elapsed < 15.0, elapsed


def test_draft_model_env_default():
    from core import llm

    assert "haiku" in llm.draft_model_name().lower()


def test_extract_model_defaults_to_haiku():
    from core import llm

    assert "haiku" in llm.extract_model_name().lower()


def test_content_blocks_skip_image_after_vision_transcription():
    """Once transcribed, extract must not re-attach the photo (double vision)."""
    from core import extractor, llm

    vendor = {
        "name": "Meghna Corrugators",
        "city": "Ahmedabad",
        "files": [
            {
                "file_id": "img1",
                "name": "rate.jpg",
                "kind": "image",
                "role": "quote",
                "url": "blob://x",
            }
        ],
        "texts": {
            "img1": {
                "text": "[image line 1] MEGHNA | Rs/Box 400",
                "method": "vision-transcription",
                "legibility": 0.7,
                "caveats": "",
            }
        },
    }
    rfx = {
        "line_items": [],
        "terms": {"price_basis": "INR per piece", "currency": "INR"},
        "questionnaire": [],
    }
    blocks = extractor._content_blocks(vendor, rfx, get_bytes=lambda u: b"fake")
    assert not any(b.get("type") == "image" for b in blocks)
    assert any("MEGHNA" in (b.get("text") or "") for b in blocks)


def test_attachment_text_truncated_in_prompt_only():
    from core import extractor

    f = {"name": "ISO.pdf", "kind": "pdf", "role": "attachment", "file_id": "c1"}
    long_text = "CERT LINE\n" * 200
    t = {"text": long_text, "method": "pymupdf text layer"}
    prompted = extractor._file_text_for_prompt(f, t)
    assert len(prompted) < len(long_text)
    assert "truncated" in prompted.lower()
    # Full text for grounding stays on the vendor texts dict (caller responsibility)
    assert len(t["text"]) == len(long_text)


def test_image_only_quote_uses_combined_photo_schema():
    from unittest.mock import MagicMock, patch

    from core import extractor
    from core.models import ExtractionAI, PhotoQuoteAI

    vendor = {
        "name": "Meghna Corrugators",
        "city": "Ahmedabad",
        "files": [
            {
                "file_id": "img1",
                "name": "rate.jpg",
                "kind": "image",
                "role": "quote",
                "url": "blob://img",
                "content_type": "image/jpeg",
            }
        ],
        "texts": {},
    }
    rfx = {
        "line_items": [
            {
                "line_no": 1,
                "sku": "CB-001",
                "description": "box",
                "board": "3-ply",
                "flute": "B",
                "length_mm": 300,
                "width_mm": 200,
                "height_mm": 150,
                "gsm": 400,
                "print": "plain",
                "annual_qty": 1000,
            }
        ],
        "terms": {"price_basis": "INR per piece", "currency": "INR"},
        "questionnaire": [],
    }
    state = {"rfx": rfx}
    fake = PhotoQuoteAI(
        transcription="Sr | Item | Rs/Box\n1 | box | 400\n* Rates per box of 20 nos.",
        legibility=0.8,
        caveats="slight skew",
        extraction=ExtractionAI(
            vendor_name_in_document="Meghna",
            line_quotes=[
                {
                    "line_no": 1,
                    "candidate_line_nos": [],
                    "vendor_item_ref": "1",
                    "vendor_description": "box",
                    "price": 400.0,
                    "currency": "INR",
                    "price_basis": "per_box",
                    "basis_qty": 20,
                    "confidence": 0.8,
                    "status": "ok",
                    "review_cause": "none",
                    "reason": "",
                    "evidence": {
                        "file_name": "rate.jpg",
                        "location": "image line 2",
                        "snippet": "1 | box | 400",
                    },
                }
            ],
            not_quoted_line_nos=[],
            commercials=[],
            questionnaire=[],
            certificates=[],
            notes="",
        ),
    )
    log: list = []
    with patch("core.extractor.llm.structured", return_value=fake) as mocked:
        with patch("core.extractor.llm.extract_model_name", return_value="claude-haiku-4-5"):
            out = extractor.extract_vendor(state, vendor, get_bytes=lambda u: b"\xff\xd8\xfffake", log=log)
    assert mocked.call_args.kwargs["purpose"] == "extract_vendor_photo_combined"
    assert mocked.call_args.kwargs["schema"].__name__ == "PhotoQuoteAI"
    assert "haiku" in mocked.call_args.kwargs["model"].lower()
    assert vendor["texts"]["img1"]["method"].startswith("vision-transcription")
    assert out["line_quotes"][0]["price"] == 400.0
    # Grounding should verify snippet against stored transcription
    assert out["line_quotes"][0]["evidence"]["verified"] is True


def test_non_image_extract_uses_haiku_and_no_fake():
    from unittest.mock import patch

    from core import extractor
    from core.models import ExtractionAI

    vendor = {
        "name": "Ganesh Board Mills",
        "city": "Indore",
        "files": [
            {
                "file_id": "e1",
                "name": "re.eml",
                "kind": "email",
                "role": "quote",
                "url": "blob://e",
            }
        ],
        "texts": {},
    }
    rfx = {
        "line_items": [
            {
                "line_no": 1,
                "sku": "CB-001",
                "description": "3-ply",
                "board": "3-ply",
                "flute": "B",
                "length_mm": 300,
                "width_mm": 200,
                "height_mm": 150,
                "gsm": 400,
                "print": "plain",
                "annual_qty": 1000,
            }
        ],
        "terms": {"price_basis": "INR per piece", "currency": "INR"},
        "questionnaire": [],
    }
    eml = (
        b"From: a@b.com\nTo: x@y.com\nSubject: Re\n\n"
        b"Sir,\n\nRs 51/kg for the 3-ply, freight extra.\n"
    )
    fake = ExtractionAI(
        vendor_name_in_document="Ganesh",
        line_quotes=[
            {
                "line_no": 1,
                "candidate_line_nos": [],
                "vendor_item_ref": "",
                "vendor_description": "3-ply",
                "price": 51.0,
                "currency": "INR",
                "price_basis": "per_kg",
                "basis_qty": None,
                "confidence": 0.9,
                "status": "ok",
                "review_cause": "none",
                "reason": "",
                "evidence": {
                    "file_name": "re.eml",
                    "location": "line 3",
                    "snippet": "Rs 51/kg for the 3-ply, freight extra.",
                },
            }
        ],
        not_quoted_line_nos=[],
        commercials=[],
        questionnaire=[],
        certificates=[],
        notes="",
    )
    with patch("core.extractor.llm.structured", return_value=fake) as mocked:
        with patch("core.extractor.llm.extract_model_name", return_value="claude-haiku-4-5"):
            out = extractor.extract_vendor(
                {"rfx": rfx}, vendor, get_bytes=lambda u: eml, log=[]
            )
    assert mocked.call_args.kwargs["purpose"] == "extract_vendor_response"
    assert mocked.call_args.kwargs["model"] == "claude-haiku-4-5"
    assert mocked.call_args.kwargs["max_tokens"] == 12000
    assert out["line_quotes"][0]["price"] == 51.0
