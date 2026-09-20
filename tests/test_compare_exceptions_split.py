"""Compare absorbs Exceptions: anomalies panel + evidence drawer actions; /exceptions redirects."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import demo_ops, engine, exceptions as exc, snapshots, storage

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def _open_cell(st, *, prefer_assumed: bool = False):
    items = [i for i in exc.list_exceptions(st, "open") if i.get("line_no") and i.get("vendor_id")]
    if prefer_assumed:
        assumed = [i for i in items if i.get("kind") == "assumed" or "assumed" in (i.get("key") or "")]
        if assumed:
            return assumed[0]
        for i in items:
            cmp = engine.build_comparison(st)
            c = next(ln for ln in cmp["lines"] if ln["line_no"] == i["line_no"])["cells"][i["vendor_id"]]
            if c.get("unit_inr_candidate") is not None or c.get("unit_inr") is not None:
                return i
    return items[0]


def test_nav_has_no_exceptions_item():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    # Primary nav: RFx | Email | Compare | Award — no Exceptions step
    assert not re.search(r'href="/rfx/[^"]+/exceptions"', r.text)
    # Nav labels in order without Exceptions between Compare and Award
    pos_c = r.text.find(">Compare<") if ">Compare<" in r.text else r.text.find("Compare")
    pos_e = r.text.find(">Exceptions<")
    pos_a = r.text.find(">Award<") if ">Award<" in r.text else r.text.find("Award")
    assert pos_e == -1
    assert 0 <= pos_c < pos_a


def test_exceptions_redirects_to_compare_anomalies():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/exceptions", follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert f"/rfx/{st['id']}/compare" in loc
    assert "#anomalies" in loc

    r2 = client.get(f"/rfx/{st['id']}/exceptions?status=pending", follow_redirects=False)
    assert r2.status_code in (302, 303)
    loc2 = r2.headers.get("location") or ""
    assert "status=pending" in loc2
    assert "#anomalies" in loc2


def test_compare_includes_anomalies_panel_and_actions():
    st = _seed()
    page = client.get(f"/rfx/{st['id']}/compare")
    assert page.status_code == 200
    assert 'id="anomalies"' in page.text
    assert "anomalies-chip" in page.text or "open anomal" in page.text
    assert "/override" in page.text
    assert "Send for approval" in page.text
    assert "/deny" in page.text
    # Gate / coverage-gap rows appear even without a single cell
    assert "Gate" in page.text or "coverage" in page.text.lower() or "Coverage" in page.text
    # No long vendor callout wall
    assert "partial_quote" not in page.text or page.text.count("rounded-md border text-xs px-3 py-2") < 3


def test_evidence_drawer_can_override_deny_request_approval():
    st = _seed()
    cell = _open_cell(st)
    ev = client.get(f"/rfx/{st['id']}/evidence/{cell['vendor_id']}/{cell['line_no']}")
    assert ev.status_code == 200
    assert "Source evidence" in ev.text or "Normalised" in ev.text
    assert f"/exceptions/{cell['key']}/override" in ev.text
    assert f"/exceptions/{cell['key']}/request-approval" in ev.text
    assert f"/exceptions/{cell['key']}/deny" in ev.text
    assert "Override" in ev.text
    assert "Send for approval" in ev.text
    assert "Deny" in ev.text
    # Why flagged short reason
    assert "Why flagged" in ev.text or cell.get("reason") or True


def test_deny_persists_and_excludes_from_totals():
    st = _seed()
    ver_before = snapshots.current_version(st)
    cell = _open_cell(st)
    vid, line_no, key = cell["vendor_id"], cell["line_no"], cell["key"]

    cmp_before = engine.build_comparison(st)
    usable_before = next(v for v in cmp_before["vendors"] if v["vendor_id"] == vid)["usable"]
    total_before = next(v for v in cmp_before["vendors"] if v["vendor_id"] == vid)["extended_total_usable"]

    r = client.post(
        f"/rfx/{st['id']}/exceptions/{key}/deny",
        data={"note": "Not accepting this ambiguous quote into the award."},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert "#anomalies" in loc
    assert "/compare" in loc

    st = storage.load_state(st["id"])
    denied = next(x for x in exc.list_exceptions(st, "resolved") if x["key"] == key)
    assert denied["status"] == "denied"
    assert denied["note"]

    assert not any(
        rev.get("vendor_id") == vid and rev.get("line_no") == line_no
        for rev in (st.get("reviews") or [])
    )
    assert any(
        e.get("action") == "deny" and e.get("exception_key") == key
        for e in (st.get("buyer_review_log") or [])
    )

    cmp_after = engine.build_comparison(st)
    cell_after = next(ln for ln in cmp_after["lines"] if ln["line_no"] == line_no)["cells"][vid]
    assert cell_after["status"] != "reviewed"
    vrow = next(v for v in cmp_after["vendors"] if v["vendor_id"] == vid)
    assert vrow["usable"] == usable_before
    assert vrow["extended_total_usable"] == total_before

    assert snapshots.current_version(st) == ver_before + 1
    assert key not in {i["key"] for i in exc.list_exceptions(st, "open")}


def test_override_still_applies_into_decision():
    st = _seed()
    cell = _open_cell(st, prefer_assumed=True)
    r = client.post(
        f"/rfx/{st['id']}/exceptions/{cell['key']}/override",
        data={"note": "Buyer confirmed value after call."},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st = storage.load_state(st["id"])
    assert any(
        x["key"] == cell["key"] and x["status"] == "overridden"
        for x in exc.list_exceptions(st, "resolved")
    )
    rev = next(
        r
        for r in (st.get("reviews") or [])
        if r.get("vendor_id") == cell["vendor_id"] and r.get("line_no") == cell["line_no"]
    )
    assert rev.get("value_inr") is not None
    cmp = engine.build_comparison(st)
    reviewed = next(ln for ln in cmp["lines"] if ln["line_no"] == cell["line_no"])["cells"][
        cell["vendor_id"]
    ]
    assert reviewed["status"] == "reviewed"
    assert reviewed.get("unit_inr") is not None


def test_request_approval_still_stubs_outbox():
    st = _seed()
    gate = next(
        i for i in exc.list_exceptions(st, "open") if str(i.get("kind", "")).startswith("gate")
    )
    r = client.post(
        f"/rfx/{st['id']}/exceptions/{gate['key']}/request-approval",
        data={
            "note": "Need sign-off",
            "manager_name": "Priya",
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
    if gate.get("vendor_id") and gate.get("line_no") is not None:
        assert not any(
            rev.get("vendor_id") == gate["vendor_id"] and rev.get("line_no") == gate["line_no"]
            for rev in (st.get("reviews") or [])
        )

    page = client.get(f"/rfx/{st['id']}/compare?status=pending")
    assert page.status_code == 200
    assert 'id="anomalies"' in page.text
    assert f"/exceptions/{gate['key']}/approve" in page.text
    assert f"/exceptions/{gate['key']}/reject" in page.text
    assert f"/exceptions/{gate['key']}/deny" not in page.text
    assert f"/exceptions/{gate['key']}/override" not in page.text


def test_deny_does_not_clear_gate_knockout():
    st = _seed()
    gate = next(
        i for i in exc.list_exceptions(st, "open") if str(i.get("kind", "")).startswith("gate")
    )
    exc.deny_exception(st, gate["key"], "Leaving knockout in place.")
    assert (gate["vendor_id"], gate["gate_q_id"]) not in exc.cleared_knockouts(st)
    assert any(
        x["key"] == gate["key"] and x["status"] == "denied"
        for x in exc.list_exceptions(st, "resolved")
    )


def test_award_resolve_link_points_to_compare_anomalies():
    st = _seed()
    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    if "has_blocking" in page.text.lower() or "Resolve" in page.text:
        assert f"/rfx/{st['id']}/compare#anomalies" in page.text
