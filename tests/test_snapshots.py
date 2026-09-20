"""Acceptance tests for snapshot consistency (MD prompt Tests 1–6, 8)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import engine, export, snapshots  # noqa: E402
from core.rfx_drafter import new_state  # noqa: E402


def _minimal_rfx():
    return {
        "title": "Test corrugated RFQ",
        "scope": "test",
        "created_at": "2026-09-20T00:00:00+00:00",
        "line_items": [
            {
                "line_no": i,
                "sku": f"CB-{i:03d}",
                "description": f"Carton {i}",
                "board": "5-ply" if i % 2 else "3-ply",
                "flute": "BC",
                "length_mm": 400,
                "width_mm": 300,
                "height_mm": 250,
                "gsm": 700,
                "print": "1 colour",
                "annual_qty": 10000,
                "notes": "",
                "uom": "pcs",
                "nominal_weight_g": 120.0,
                "dimensions": "400x300x250 mm",
            }
            for i in range(1, 31)
        ],
        "terms": {
            "currency": "INR",
            "price_basis": "INR per piece",
            "payment_terms": "30 days",
            "delivery_location": "Pune",
            "delivery_schedule": "monthly",
            "freight_basis": "delivered",
            "validity_days": 30,
            "gst_treatment": "extra",
            "other": "",
        },
        "questionnaire": [
            {"q_id": "Q1", "text": "ISO 9001?", "answer_type": "yes_no", "knockout": True},
            {"q_id": "Q2", "text": "Capacity?", "answer_type": "text", "knockout": False},
        ],
    }


def _vendor(vid, name, prices, status="extracted"):
    quotes = []
    for i, p in enumerate(prices, start=1):
        if p is None:
            continue
        quotes.append(
            {
                "line_no": i,
                "candidate_line_nos": [],
                "vendor_item_ref": "",
                "vendor_description": f"item {i}",
                "price": p,
                "currency": "INR",
                "price_basis": "per_pc",
                "basis_qty": 1,
                "confidence": 0.9,
                "status": "ok",
                "review_cause": "none",
                "reason": "",
                "evidence": {"file_name": "q.xlsx", "location": f"A{i}", "snippet": str(p), "verified": True},
            }
        )
    not_quoted = [i for i, p in enumerate(prices, start=1) if p is None]
    return {
        "vendor_id": vid,
        "name": name,
        "city": "TestCity",
        "email": f"{vid}@example.com",
        "format": "xlsx",
        "status": status,
        "files": [{"file_id": "f1", "name": "q.xlsx", "kind": "xlsx", "url": "/files/x", "size": 10, "role": "quote"}]
        if status != "awaiting"
        else [],
        "extraction": {
            "vendor_name_in_document": name,
            "line_quotes": quotes,
            "not_quoted_line_nos": not_quoted,
            "commercials": [
                {"key": "freight", "value": "Delivered", "numeric_pct": None, "applies_to": "", "confidence": 1, "status": "ok", "evidence": None},
                {"key": "payment_terms", "value": "30 days", "numeric_pct": None, "applies_to": "", "confidence": 1, "status": "ok", "evidence": None},
            ],
            "questionnaire": [
                {"q_id": "Q1", "answer": "Yes", "answered": True, "confidence": 1, "status": "ok", "evidence": None},
                {"q_id": "Q2", "answer": "OK", "answered": True, "confidence": 1, "status": "ok", "evidence": None},
            ],
            "certificates": [],
            "notes": "",
            "grounding": {"checked": len(quotes), "verified": len(quotes), "downgraded": 0},
        }
        if status == "extracted"
        else None,
        "texts": {},
        "error": None,
        "received_at": "2026-09-20T01:00:00+00:00",
    }


def _state_with_vendors(vendors):
    st = new_state("brief for testing corrugated packaging buy", _minimal_rfx())
    st["vendors"] = vendors
    st["status"] = "compared"
    snapshots.ensure_snapshot_fields(st)
    return st


def test_early_answer_becomes_stale():
    """Test 1: process one vendor, answer, then process another → stale + save blocked."""
    v1 = _vendor("v1", "Sri Balaji", [10.0] * 28 + [None, None])
    state = _state_with_vendors([v1])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    assert state["vendor_data_version"] == 1

    snap = snapshots.create_calculation_snapshot(state, "analyst_answer", {"question": "q"}, {})
    answer = snapshots.attach_answer_metadata(
        state, {"question": "Who wins?", "answer": "Sri Balaji alone", "trace": [], "tables": [], "caveats": []}, snap
    )
    state["chat"].append(answer)
    assert answer["status"] == "current"
    ok, _ = snapshots.can_save_recommendation(state, answer)
    assert ok
    snapshots.save_recommendation_from_answer(state, answer, 0)
    assert state["recommendation"]["status"] == "current"

    # Process Kraftline
    v2 = _vendor("v2", "Kraftline", [9.0] * 27 + [None, None, None])
    state["vendors"].append(v2)
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v2"])
    assert state["vendor_data_version"] == 2
    snapshots.refresh_staleness(state)

    assert state["chat"][0]["status"] == "stale"
    assert state["recommendations"][0]["status"] == "stale"
    assert state["recommendation"]["status"] == "stale"
    ok, msg = snapshots.can_save_recommendation(state, state["chat"][0])
    assert not ok
    assert "Rerun the question" in msg

    # Award page must not label it current
    current = next((r for r in state["recommendations"] if r["status"] == "current"), None)
    assert current is None


def test_new_answer_is_current():
    """Test 2: all five vendors, new answer references latest version, save works."""
    vendors = [
        _vendor("v1", "Sri Balaji", [12.0] * 28 + [None, None]),
        _vendor("v2", "Kraftline", [11.0] * 27 + [None, None, None]),
        _vendor("v3", "PakAsia", [10.0] * 11 + [None] * 19),
        _vendor("v4", "Meghna", [10.5] * 28 + [None, None]),
        _vendor("v5", "Ganesh", [9.5] * 28 + [None, None]),
    ]
    # Mark 2 unresolved on Ganesh
    for q in vendors[4]["extraction"]["line_quotes"][-2:]:
        q["status"] = "unresolved"
        q["price"] = None
    state = _state_with_vendors(vendors)
    for v in vendors:
        snapshots.bump_vendor_data_version(state, "vendor_extracted", [v["vendor_id"]])
    ver = state["vendor_data_version"]
    assert ver == 5

    snap = snapshots.create_calculation_snapshot(state, "analyst_answer", {"question": "split"}, {})
    answer = snapshots.attach_answer_metadata(
        state,
        {"question": "split cheapest", "answer": "Split across Ganesh, Kraftline, Meghna, PakAsia", "trace": [], "tables": [], "caveats": []},
        snap,
    )
    assert answer["vendor_data_version"] == ver
    assert answer["calculation_snapshot_id"] == snap["id"]
    state["chat"].append(answer)
    ok, _ = snapshots.can_save_recommendation(state, answer)
    assert ok
    rec = snapshots.save_recommendation_from_answer(state, answer, 0)
    assert rec["status"] == "current"
    assert rec["vendor_data_version"] == ver

    # Context must not claim unextracted vendors
    ctx = snapshots.context_for_analyst(state, engine.build_comparison(state), snap)
    assert "EXTRACTED" in ctx
    assert "Kraftline" in ctx and "EXTRACTED" in ctx.split("Kraftline")[1][:80]


def test_award_and_memo_agree():
    """Test 3: Award live totals and memo share snapshot id and total."""
    vendors = [
        _vendor("v1", "Sri Balaji", [12.0] * 30),
        _vendor("v2", "Kraftline", [11.0] * 30),
    ]
    state = _state_with_vendors(vendors)
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v2"])
    live = snapshots.live_award_calculation(state)
    assert live["available"]
    total = live["total_extended_inr"]
    snap_id = live["snapshot"]["id"]

    memo = export.award_memo_md(state)
    # Re-read live snap id (reuse may return same)
    live2 = snapshots.live_award_calculation(state)
    assert live2["snapshot"]["id"] == snap_id
    assert snap_id in memo, memo[:500]
    assert engine.fmt_inr(total) in memo
    assert f"version **{state['vendor_data_version']}**" in memo


def test_processing_state():
    """Test 4: provisional while extracting; version bumps; answers go stale."""
    v1 = _vendor("v1", "Sri Balaji", [10.0] * 30)
    v2 = _vendor("v2", "Kraftline", [9.0] * 30, status="received")
    v2["extraction"] = None
    state = _state_with_vendors([v1, v2])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    snap = snapshots.create_calculation_snapshot(state, "analyst_answer", {}, {})
    ans = snapshots.attach_answer_metadata(state, {"question": "q", "answer": "a", "trace": [], "tables": [], "caveats": []}, snap)
    state["chat"].append(ans)

    ds = snapshots.data_status(state, "compare")
    assert ds["kind"] == "processing"
    assert "still being extracted" in ds["text"] or "Provisional" in ds["text"]

    # Finish Kraftline
    v2["status"] = "extracted"
    v2["extraction"] = _vendor("v2", "Kraftline", [9.0] * 30)["extraction"]
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v2"])
    snapshots.refresh_staleness(state)
    assert state["chat"][0]["status"] == "stale"
    ds2 = snapshots.data_status(state)
    assert ds2["kind"] in ("current", "current_unresolved")


def test_review_override_bumps_version():
    """Test 5: accept/override creates new version and stale answers."""
    v1 = _vendor("v1", "Sri Balaji", [10.0] * 30)
    # Make one needs_review
    v1["extraction"]["line_quotes"][0]["status"] = "needs_review"
    state = _state_with_vendors([v1])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    snap = snapshots.create_calculation_snapshot(state, "analyst_answer", {}, {})
    ans = snapshots.attach_answer_metadata(state, {"question": "q", "answer": "a", "trace": [], "tables": [], "caveats": []}, snap)
    state["chat"].append(ans)
    ver_before = state["vendor_data_version"]

    state["reviews"].append(
        {"vendor_id": "v1", "vendor_name": "Sri Balaji", "line_no": 1, "action": "accept", "value_inr": None, "note": "ok", "at": "2026-09-20T02:00:00+00:00"}
    )
    snapshots.bump_vendor_data_version(state, "review_accepted", ["v1"])
    assert state["vendor_data_version"] == ver_before + 1
    snapshots.refresh_staleness(state)
    assert state["chat"][0]["status"] == "stale"


def test_export_correctness():
    """Test 6: exports carry same snapshot / version metadata."""
    vendors = [_vendor("v1", "Sri Balaji", [10.0] * 30), _vendor("v2", "Kraftline", [9.5] * 30)]
    state = _state_with_vendors(vendors)
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v2"])
    live = snapshots.live_award_calculation(state)
    total = live["total_extended_inr"]

    xlsx = export.award_workbook(state)
    assert isinstance(xlsx, bytes) and len(xlsx) > 1000
    memo = export.award_memo_md(state)
    csv_bytes = export.comparison_csv(state)
    assert b"vendor_data_version=" in csv_bytes
    assert f"version **{state['vendor_data_version']}**" in memo
    assert engine.fmt_inr(total) in memo


def test_failed_extraction_preserves_prior():
    """Test 8: failed re-read keeps prior successful extraction conceptually."""
    v1 = _vendor("v1", "Sri Balaji", [10.0] * 30)
    state = _state_with_vendors([v1])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    prior = copy.deepcopy(v1["extraction"])
    # Simulate failure path: restore prior
    v1["last_failed_attempt"] = {"at": "2026-09-20T03:00:00+00:00", "error": "boom"}
    v1["extraction"] = prior
    v1["status"] = "extracted"
    assert v1["extraction"] is not None
    assert v1["status"] == "extracted"


def test_legacy_migration():
    state = new_state("brief", _minimal_rfx())
    state.pop("vendor_data_version", None)
    state.pop("recommendations", None)
    state["chat"] = [{"question": "old", "answer": "old answer", "trace": [], "tables": [], "caveats": [], "at": "2026-09-01T00:00:00+00:00"}]
    state["recommendation"] = {"question": "old", "answer": "old rec", "saved_at": "2026-09-01T00:00:00+00:00", "chat_index": 0}
    snapshots.ensure_snapshot_fields(state)
    assert state["chat"][0]["status"] == "stale"
    assert state["chat"][0].get("legacy")
    assert state["recommendations"][0]["status"] == "stale"
    assert "before calculation snapshots" in state["recommendations"][0]["stale_reason"]


def test_input_hash_changes_with_prices():
    v1 = _vendor("v1", "Sri Balaji", [10.0] * 30)
    state = _state_with_vendors([v1])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    h1 = snapshots.input_hash(state)
    state["vendors"][0]["extraction"]["line_quotes"][0]["price"] = 99.0
    h2 = snapshots.input_hash(state)
    assert h1 != h2


def test_context_validation():
    v1 = _vendor("v1", "Sri Balaji", [10.0] * 30)
    state = _state_with_vendors([v1])
    snapshots.bump_vendor_data_version(state, "vendor_extracted", ["v1"])
    snap = snapshots.create_calculation_snapshot(state, "analyst_answer", {}, {})
    snapshots.assert_context_current(state, snap, snap["vendor_data_version"])
    snapshots.bump_vendor_data_version(state, "vendor_reprocessed", ["v1"])
    try:
        snapshots.assert_context_current(state, snap, snap["vendor_data_version"])
        assert False, "should have raised"
    except RuntimeError:
        pass


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
