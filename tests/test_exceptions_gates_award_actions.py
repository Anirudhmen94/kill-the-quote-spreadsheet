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


def test_nav_has_anomalies_between_compare_and_award():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    assert not re.search(r'href="/rfx/[^"]+/exceptions"', r.text)
    assert re.search(r'href="/rfx/[^"]+/anomalies"', r.text)
    def _nav_pos(label: str) -> int:
        m = re.search(rf">\s*{re.escape(label)}\s*<", r.text)
        return m.start() if m else -1
    pos_c, pos_an, pos_a = _nav_pos("Compare"), _nav_pos("Anomalies"), _nav_pos("Award")
    assert 0 <= pos_c < pos_an < pos_a
    # Ask is not a primary nav step (drawer may still mention it)
    assert not re.search(r'href="/rfx/[^"]+/ask"', r.text)



def test_home_shows_gate_builder():
    r = client.get("/")
    assert r.status_code == 200
    assert "gates-builder" in r.text
    assert "ISO 9001" in r.text
    assert 'name="gate_knockout"' in r.text
    assert 'name="gate_enabled"' in r.text
    assert 'name="gate_label"' in r.text
    # Buyer does not author question wording or answer type on home
    assert 'name="gate_question"' not in r.text
    assert 'name="gate_answer_type"' not in r.text
    assert "Add check" in r.text


def test_draft_default_and_custom_gates():
    gates = draft_gates.default_gates()
    rfx = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF, gates=gates)
    assert len(rfx["line_items"]) == 30
    assert len(rfx["questionnaire"]) == sum(1 for g in gates if g.get("enabled", True))
    assert rfx["questionnaire"][0]["knockout"] is True
    assert rfx.get("quality_gates")

    # Custom short label only — system generates question (not empty)
    gates = [
        {"id": "custom_pe", "label": "PE coating", "knockout": True, "enabled": True},
    ]
    rfx2 = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF, gates=gates)
    assert len(rfx2["questionnaire"]) == 1
    pe = rfx2["questionnaire"][0]
    assert pe["knockout"] is True
    assert pe.get("gate_id") == "custom_pe"
    assert pe["text"] and "PE coating" in pe["text"]


def test_select_three_checks_generates_exactly_those_questions():
    gates = [
        {"id": "iso_9001", "label": "ISO 9001 certification", "knockout": True, "enabled": True},
        {"id": "bct_reports", "label": "BCT test reports", "knockout": True, "enabled": True},
        {"id": "fsc_recycled", "label": "FSC / recycled content preferred", "knockout": False, "enabled": True},
    ]
    rfx = rfx_drafter.draft_rfx(vendor_sim.EXAMPLE_BRIEF, gates=gates)
    qs = rfx["questionnaire"]
    assert len(qs) == 3
    assert [q["gate_id"] for q in qs] == ["iso_9001", "bct_reports", "fsc_recycled"]
    assert [q["knockout"] for q in qs] == [True, True, False]
    assert all(q["text"] for q in qs)
    assert "ISO 9001" in qs[0]["text"]
    assert "BCT" in qs[1]["text"]


def test_custom_short_label_generates_nonempty_question():
    q_text, at = draft_gates.generate_question_for_check(
        {"id": "custom_fire", "label": "Fire safety cert"},
        brief=vendor_sim.EXAMPLE_BRIEF,
    )
    assert q_text.strip()
    assert "Fire safety" in q_text or "fire safety" in q_text.lower()
    assert at == "document"  # "cert" hint


def test_form_ignores_buyer_authored_question_fields():
    """Home no longer posts gate_question / gate_answer_type; if present, ignore them."""
    class FakeForm(dict):
        def getlist(self, key):
            return self.get(key, [])

    form = FakeForm(
        {
            "gate_id": ["iso_9001"],
            "gate_label": ["ISO 9001 certification"],
            "gate_enabled": ["iso_9001"],
            "gate_knockout": ["iso_9001"],
            "gate_question": ["BUYER WROTE THIS SHOULD BE IGNORED"],
            "gate_answer_type": ["text"],
        }
    )
    gates = draft_gates.parse_gates_from_form(form)
    assert len(gates) == 1
    assert "question" not in gates[0] or gates[0].get("question") != "BUYER WROTE THIS SHOULD BE IGNORED"
    enriched = draft_gates.enrich_gates(gates, vendor_sim.EXAMPLE_BRIEF)
    assert "BUYER WROTE THIS" not in enriched[0]["question"]
    assert enriched[0]["answer_type"] == "document"


def test_draft_endpoint_custom_gate_shows_on_rfx_page():
    # Home posts short labels only — no gate_question / gate_answer_type.
    data = {
        "brief": vendor_sim.EXAMPLE_BRIEF,
        "gate_id": ["iso_9001", "custom_pe"],
        "gate_label": ["ISO 9001 certification", "PE coating"],
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
    assert "PE coating" in page.text
    assert "Quality checks that drove" in page.text or "Quality gates that drove" in page.text


def test_exceptions_override_approval_reject_flow():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/anomalies")
    assert r.status_code == 200
    assert 'id="anomalies"' in r.text
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
        freeze.freeze_award(
            st,
            confirm_assumed=True,
            require_quality_gate=True,
            mode="partial",
            acknowledgements=["coverage_gaps", "selected_blockers"],
            partial_reason="Test partial freeze — demo coverage gap on line 30.",
        )
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
    vendor_mail = [o for o in new_mail if o.get("vendor_id")]
    award_count = sum(1 for o in vendor_mail if o.get("kind") == "award_notice")
    regret_count = sum(1 for o in vendor_mail if o.get("kind") in ("regret", "regret_notice"))
    flash = st.get("flash") or {}
    assert flash.get("level") == "success"
    assert flash.get("message") == (
        f"Successfully stub-sent {award_count} award notice(s) and {regret_count} regret notice(s) "
        "to Outbox (no real SMTP). View Outbox."
    )
    assert flash.get("cta_href") == f"/rfx/{st['id']}/email#outbox"

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


def test_award_send_notices_failure_redirects_with_error_flash():
    st = _seed()
    rid = st["id"]

    r = client.post(f"/rfx/{rid}/award/send-notices", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == f"/rfx/{rid}/award#award-step-3"

    st2 = storage.load_state(rid)
    flash = st2.get("flash") or {}
    assert flash.get("level") == "error"
    assert "Freeze an award first" in (flash.get("message") or "")

    page = client.get(f"/rfx/{rid}/award")
    assert page.status_code == 200
    assert "Error" in page.text
    assert "Freeze an award first" in page.text


def test_app_import_smoke():
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200  # import + home smoke
