"""Clarification draft: editable subject/body, save, stub-send uses edited text."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import demo_ops, storage

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def test_clarification_create_edit_save_send(monkeypatch):
    st = _seed()
    vid = st["vendors"][0]["vendor_id"]

    monkeypatch.setattr(
        "core.clarify.draft_clarification",
        lambda state, vendor, log=None: {
            "subject": "Original subject",
            "body": "Original body\nLine two.",
            "points": ["Line 1 missing price", "Freight unclear"],
        },
    )

    r = client.post(f"/rfx/{st['id']}/vendor/{vid}/clarify")
    assert r.status_code == 200
    assert 'name="subject"' in r.text
    assert 'name="body"' in r.text
    assert "Original subject" in r.text
    assert "Original body" in r.text
    assert "Save" in r.text
    assert "Send (stub)" in r.text

    st = storage.load_state(st["id"])
    item = next(o for o in st["outbox"] if o.get("kind") == "clarification" and o.get("vendor_id") == vid)
    assert item.get("id")
    assert item["delivery"] == "draft"
    assert item.get("sent_at") in (None, "")
    assert item["subject"] == "Original subject"
    oid = item["id"]

    r = client.post(
        f"/rfx/{st['id']}/vendor/{vid}/clarify/save",
        data={"outbox_id": oid, "subject": "Edited subject", "body": "Edited body saved."},
    )
    assert r.status_code == 200
    assert "Saved" in r.text
    assert "Edited subject" in r.text

    st = storage.load_state(st["id"])
    item = next(o for o in st["outbox"] if o.get("id") == oid)
    assert item["subject"] == "Edited subject"
    assert item["body"] == "Edited body saved."
    assert item["delivery"] == "draft"
    assert not item.get("sent_at")

    r = client.post(
        f"/rfx/{st['id']}/vendor/{vid}/clarify/send",
        data={"outbox_id": oid, "subject": "Final subject", "body": "Final body for stub send."},
    )
    assert r.status_code == 200
    assert "Stub-sent" in r.text or "stub" in r.text.lower()

    st = storage.load_state(st["id"])
    item = next(o for o in st["outbox"] if o.get("id") == oid)
    assert item["subject"] == "Final subject"
    assert item["body"] == "Final body for stub send."
    assert "stubbed" in (item.get("delivery") or "")
    assert item.get("sent_at")

    # Outbox page shows edited content
    page = client.get(f"/rfx/{st['id']}/email")
    assert page.status_code == 200
    assert "Final subject" in page.text
    assert "Final body for stub send." in page.text


def test_header_has_no_lifecycle_or_demo_strips():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/email")
    assert r.status_code == 200
    assert "Lifecycle" not in r.text
    assert 'action="/rfx/' not in r.text or "/demo/interview-reset" not in r.text
    assert "Enable demo mode" not in r.text
    assert "Disable demo mode" not in r.text
    assert 'href="/demo/script"' not in r.text
    assert "Compare" in r.text
