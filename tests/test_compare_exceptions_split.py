"""Compare shows full multi-vendor matrix; Anomalies is a separate tab with actions + redirects."""
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


def test_nav_has_anomalies_between_compare_and_award():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    assert re.search(r'href="/rfx/[^"]+/anomalies"', r.text)
    assert not re.search(r'href="/rfx/[^"]+/exceptions"', r.text)
    def _nav_pos(label: str) -> int:
        m = re.search(rf">\s*{re.escape(label)}\s*<", r.text)
        return m.start() if m else -1
    pos_c, pos_an, pos_a = _nav_pos("Compare"), _nav_pos("Anomalies"), _nav_pos("Award")
    assert 0 <= pos_c < pos_an < pos_a


def test_exceptions_redirects_to_anomalies():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/exceptions", follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert f"/rfx/{st['id']}/anomalies" in loc
    assert "#anomalies" not in loc

    r2 = client.get(f"/rfx/{st['id']}/exceptions?status=pending", follow_redirects=False)
    assert r2.status_code in (302, 303)
    loc2 = r2.headers.get("location") or ""
    assert "status=pending" in loc2
    assert "/anomalies" in loc2


def test_compare_has_full_matrix_without_anomaly_actions():
    st = _seed()
    page = client.get(f"/rfx/{st['id']}/compare")
    assert page.status_code == 200
    # Anomalies panel / actions stay off Compare
    assert 'id="anomalies"' not in page.text
    assert "anomalies-chip" not in page.text
    assert "/override" not in page.text
    assert "Send for approval" not in page.text
    assert "/deny" not in page.text
    # Full multi-vendor matrix (Pass + Fail/Partial)
    assert "Sri Balaji" in page.text or "Balaji" in page.text
    assert "Meghna" in page.text  # Fail vendor still a column
    assert "needs review" in page.text.lower()
    assert "unresolved" in page.text.lower()
    assert "Excluded from every total" in page.text
    assert "Quality gates" in page.text
    assert "Pass" in page.text
    assert "/rfx/" in page.text and "/anomalies" in page.text


def test_anomalies_page_lists_items_and_actions():
    st = _seed()
    page = client.get(f"/rfx/{st['id']}/anomalies")
    assert page.status_code == 200
    assert 'id="anomalies"' in page.text
    assert "/override" in page.text
    assert "Send for approval" in page.text
    assert "/deny" in page.text
    assert "Gate" in page.text or "coverage" in page.text.lower() or "Coverage" in page.text
    assert ">Anomalies<" in page.text


def test_evidence_drawer_links_to_anomalies_without_actions():
    st = _seed()
    cell = _open_cell(st)
    ev = client.get(f"/rfx/{st['id']}/evidence/{cell['vendor_id']}/{cell['line_no']}")
    assert ev.status_code == 200
    assert "Source evidence" in ev.text or "Normalised" in ev.text or "evidence" in ev.text.lower()
    assert f"/exceptions/{cell['key']}/override" not in ev.text
    assert f"/exceptions/{cell['key']}/deny" not in ev.text
    assert f"/rfx/{st['id']}/anomalies" in ev.text


def test_deny_persists_and_redirects_to_anomalies():
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
    assert "/anomalies" in loc
    assert "/compare" not in loc or "/anomalies" in loc

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
    assert "/anomalies" in (r.headers.get("location") or "")
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


def test_request_approval_stubs_outbox_and_shows_on_anomalies():
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
    assert "/anomalies" in (r.headers.get("location") or "")
    st = storage.load_state(st["id"])
    assert any(
        x["key"] == gate["key"] and x["status"] == "pending_approval"
        for x in exc.list_exceptions(st, "pending")
    )
    assert any("priya@buyer.example" in (o.get("to") or "") for o in st.get("outbox", []))

    page = client.get(f"/rfx/{st['id']}/anomalies?status=pending")
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


def test_award_resolve_link_points_to_anomalies():
    st = _seed()
    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    if "has_blocking" in page.text.lower() or "Resolve" in page.text:
        assert f"/rfx/{st['id']}/anomalies" in page.text
        assert f"/rfx/{st['id']}/compare#anomalies" not in page.text
