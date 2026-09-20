"""Exceptions workflow, draft-time quality gates, award send/export notices."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import (
    award_actions,
    demo_ops,
    draft_gates,
    exceptions as exc,
    freeze,
    rfx_drafter,
    storage,
    vendor_sim,
)

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def test_nav_includes_exceptions_between_compare_and_award():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    assert re.search(r'href="/rfx/[^"]+/exceptions"', r.text)
    # Primary step labels appear in order Compare → Exceptions → Award
    pos_c = r.text.find("Compare")
    pos_e = r.text.find("Exceptions")
    pos_a = r.text.find(">Award<") if ">Award<" in r.text else r.text.find("Award")
    assert 0 <= pos_c < pos_e < pos_a
    # Ask is not a primary nav step (drawer may still mention it)
    assert not re.search(r'href="/rfx/[^"]+/ask"', r.text)



def test_home_shows_gate_builder():
    r = client.get("/")
    assert r.status_code == 200
    assert "gates-builder" in r.text
    assert "ISO 9001" in r.text
    assert 'name="gate_knockout"' in r.text


def test_draft_default_and_custom_gates():
    gates = draft_gates.default_gates()
    rfx = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF, gates=gates)
    assert len(rfx["line_items"]) == 30
    assert len(rfx["questionnaire"]) == sum(1 for g in gates if g.get("enabled", True))
    assert rfx["questionnaire"][0]["knockout"] is True
    assert rfx.get("quality_gates")

    gates = draft_gates.default_gates()
    gates.append(
        {
            "id": "custom_pe",
            "label": "PE coating",
            "knockout": True,
            "enabled": True,
            "answer_type": "yes_no",
            "question": "Can you supply PE-coated board for moisture-sensitive snacks?",
        }
    )
    rfx2 = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF, gates=gates)
    pe = next(q for q in rfx2["questionnaire"] if "PE-coated" in q["text"])
    assert pe["knockout"] is True
    assert pe.get("gate_id") == "custom_pe"


def test_draft_endpoint_custom_gate_shows_on_rfx_page():
    # Prefer multipart list form fields matching the home page builder.
    data = {
        "brief": vendor_sim.EXAMPLE_BRIEF,
        "gate_id": ["iso_9001", "custom_pe"],
        "gate_label": ["ISO 9001 certification", "PE coating"],
        "gate_question": [
            "Is your manufacturing site ISO 9001 certified? Attach certificate.",
            "Can you supply PE-coated board for moisture-sensitive snacks?",
        ],
        "gate_answer_type": ["document", "yes_no"],
        "gate_enabled": ["iso_9001", "custom_pe"],
        "gate_knockout": ["iso_9001", "custom_pe"],
    }
    r = client.post("/draft", data=data)
    assert r.status_code == 200, r.text[:500]
    loc = r.headers.get("HX-Redirect") or r.headers.get("location") or ""
    assert "/rfx/" in loc, (loc, r.headers, r.text[:300])
    rfx_id = loc.split("/rfx/")[1].split("/")[0]
    page = client.get(f"/rfx/{rfx_id}/rfx")
    assert page.status_code == 200
    assert "PE-coated" in page.text or "PE coating" in page.text
    assert "Quality gates that drove" in page.text


def test_exceptions_override_approval_reject_flow():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/exceptions")
    assert r.status_code == 200
    assert exc.counts(st)["open"] >= 1

    items = exc.list_exceptions(st, "open")
    cell = next(i for i in items if i.get("line_no") and i.get("vendor_id"))
    gate = next(i for i in items if str(i.get("kind", "")).startswith("gate"))

    r = client.post(
        f"/rfx/{st['id']}/exceptions/{cell['key']}/override",
        data={"note": "Buyer confirmed alternate flute is acceptable."},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st = storage.load_state(st["id"])
    assert any(
        x["key"] == cell["key"] and x["status"] == "overridden"
        for x in exc.list_exceptions(st, "resolved")
    )
    assert st.get("reviews")

    r = client.post(
        f"/rfx/{st['id']}/exceptions/{gate['key']}/request-approval",
        data={
            "note": "Need manager sign-off on incomplete questionnaire.",
            "manager_name": "Priya Manager",
            "manager_email": "priya@buyer.example",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st = storage.load_state(st["id"])
    assert any(
        x["key"] == gate["key"] and x["status"] == "pending_approval"
        for x in exc.list_exceptions(st, "pending")
    )
    assert any("priya@buyer.example" in (o.get("to") or "") for o in st.get("outbox", []))

    r = client.post(
        f"/rfx/{st['id']}/exceptions/{gate['key']}/approve",
        data={"note": "Approved for this event only."},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st = storage.load_state(st["id"])
    assert any(
        x["key"] == gate["key"] and x["status"] == "approved"
        for x in exc.list_exceptions(st, "resolved")
    )
    assert (gate["vendor_id"], gate["gate_q_id"]) in exc.cleared_knockouts(st)

    # Reject path
    st2 = _seed()
    gate2 = next(
        i for i in exc.list_exceptions(st2, "open") if str(i.get("kind", "")).startswith("gate")
    )
    client.post(
        f"/rfx/{st2['id']}/exceptions/{gate2['key']}/request-approval",
        data={"note": "Please review", "manager_name": "M", "manager_email": "m@x.com"},
        follow_redirects=False,
    )
    client.post(
        f"/rfx/{st2['id']}/exceptions/{gate2['key']}/reject",
        data={"note": "Not acceptable"},
        follow_redirects=False,
    )
    st2b = storage.load_state(st2["id"])
    assert gate2["key"] in {i["key"] for i in exc.list_exceptions(st2b, "open")}


def test_award_send_notices_export_and_stakeholder_alerts():
    st = _seed()
    if not freeze.current_freeze(st):
        freeze.freeze_award(st, confirm_assumed=False, require_quality_gate=True)
    storage.save_state(st["id"], st)
    before = len(storage.load_state(st["id"]).get("outbox") or [])

    r = client.post(f"/rfx/{st['id']}/award/send-notices", follow_redirects=False)
    assert r.status_code in (302, 303)
    st = storage.load_state(st["id"])
    outbox = st.get("outbox") or []
    assert len(outbox) > before
    new_mail = outbox[before:]
    assert any(o.get("vendor_id") for o in new_mail)
    assert any(
        o.get("kind") == "stakeholder_alert" or (o.get("to") or "").endswith("@buyer.example")
        for o in new_mail
    )

    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert ("Stub-sent" in page.text or "stakeholder" in page.text.lower() or "Outbox" in page.text or "Done." in page.text)

    before2 = len(storage.load_state(st["id"]).get("outbox") or [])
    r = client.post(f"/rfx/{st['id']}/award/export-notify", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "export.xlsx" in (r.headers.get("location") or "")
    st = storage.load_state(st["id"])
    assert len(st["outbox"]) > before2
    assert any(
        "export" in (o.get("action") or "")
        or "workbook" in (o.get("subject") or "").lower()
        or o.get("kind") == "stakeholder_alert"
        for o in st["outbox"][before2:]
    )

    x = client.get(f"/rfx/{st['id']}/export.xlsx")
    assert x.status_code == 200
    ctype = x.headers.get("content-type", "")
    assert "spreadsheet" in ctype or "officedocument" in ctype or x.content[:2] == b"PK"


def test_app_import_smoke():
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200  # import + home smoke
