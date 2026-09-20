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
