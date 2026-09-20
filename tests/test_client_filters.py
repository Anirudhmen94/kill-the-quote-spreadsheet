"""Client-side search/filter bars on Email and Compare — markup smoke tests."""
from __future__ import annotations

import re
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


def test_email_incoming_filter_bar_and_vendor_data_attrs():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/email")
    assert r.status_code == 200
    assert 'id="inbox-filters"' in r.text
    assert 'data-filter-bar="inbox-filters"' in r.text
    assert "data-filter-control=\"search\"" in r.text
    assert "data-filter-clear" in r.text
    assert "data-filter-count" in r.text
    assert "No matches — clear filters" in r.text
    # Vendor cards expose filter keys
    assert re.search(r'data-name="[^"]+"', r.text)
    assert re.search(r'data-status="extracted"', r.text)
    assert 'data-file-kinds="excel"' in r.text
    assert 'data-file-kinds="pdf"' in r.text
    assert 'data-file-kinds="word"' in r.text
    assert 'data-file-kinds="photo"' in r.text
    assert 'data-file-kinds="email"' in r.text
    assert "data-filenames=" in r.text
    # Status + file type controls
    assert 'data-filter-control="status"' in r.text
    assert 'data-filter-control="file-kind"' in r.text
    assert 'value="awaiting"' in r.text
    assert 'value="extracting"' in r.text
    assert 'value="photo"' in r.text


def test_email_outbox_filter_bar_and_row_data_attrs():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/email")
    assert r.status_code == 200
    assert 'id="outbox-filters"' in r.text
    assert 'data-filter-bar="outbox-filters"' in r.text
    assert 'id="outbox-list"' in r.text
    assert 'data-kind="rfx_invite"' in r.text
    assert "data-subject=" in r.text
    assert "data-vendor-name=" in r.text
    assert "data-body=" in r.text
    assert 'data-filter-control="kind"' in r.text
    assert 'value="clarification"' in r.text
    assert 'value="award_notice"' in r.text
    assert 'value="stakeholder_alert"' in r.text
    # Separate URL key prefixes for inbox vs outbox
    assert "iq" in r.text and "oq" in r.text


def test_compare_matrix_filter_bar_and_data_attrs():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    assert 'id="compare-matrix-filters"' in r.text
    assert 'id="compare-matrix"' in r.text
    assert "data-filter-row" in r.text
    assert "data-description=" in r.text
    assert "data-sku=" in r.text
    assert "data-col-vendor" in r.text
    assert "data-cell-vendor" in r.text
    assert "data-cell-status=" in r.text
    assert "data-gate=" in r.text
    assert 'data-filter-control="cell-status"' in r.text
    assert 'value="ok"' in r.text
    assert 'value="converted"' in r.text
    assert 'value="reviewed"' in r.text
    assert 'value="needs_review"' in r.text
    assert 'value="flagged"' in r.text
    assert 'data-filter-control="gate"' in r.text
    assert 'value="Pass"' in r.text
    assert 'data-filter-multi="vendors"' in r.text
    assert "KqFilterBar" in r.text or "data-filter-bar" in r.text


def test_anomalies_keeps_status_chips_and_adds_client_filters():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/anomalies?status=open")
    assert r.status_code == 200
    assert 'id="anomalies"' in r.text
    assert 'id="anomalies-filters"' in r.text
    assert f'/rfx/{st["id"]}/anomalies?status=open' in r.text
    assert f'/rfx/{st["id"]}/anomalies?status=pending' in r.text
    assert f'/rfx/{st["id"]}/anomalies?status=resolved' in r.text
    assert f'/rfx/{st["id"]}/anomalies?status=all' in r.text
    assert 'data-filter-control="kind"' in r.text
    assert 'value="coverage_gap"' in r.text
    assert 'value="gate_fail"' in r.text
    assert 'value="assumed"' in r.text
    assert 'id="anomalies-list"' in r.text
    # Anomaly cards keep actions
    assert "/override" in r.text
    assert "Send for approval" in r.text
    assert "/deny" in r.text
    assert re.search(r'data-kind="[^"]+"', r.text)
    assert "data-reason=" in r.text

    r2 = client.get(f"/rfx/{st['id']}/anomalies?status=all")
    assert r2.status_code == 200
    assert "bg-slate-900 text-white" in r2.text  # active chip styling present

def test_compare_has_no_anomalies_filters():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    assert 'id="anomalies"' not in r.text
    assert 'id="anomalies-filters"' not in r.text


def test_award_page_has_no_filter_bar():
    """Slim Award redesign removed Award-specific filters; Email/Compare keep theirs."""
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    assert 'id="award-filters"' not in r.text
    assert "data-award-filter-item" not in r.text
    assert 'data-filter-multi="awarded-vendor"' not in r.text
    assert 'data-filter-control="blocker-kind"' not in r.text
    # Freeze / send / download still reachable
    assert "Lock award" in r.text
    assert "Send award" in r.text or "freeze.zip" in r.text or "Award workbook" in r.text or "Lock award" in r.text


def test_filter_macros_and_script_partial_exist():
    macros = (ROOT / "templates" / "macros.html").read_text()
    assert "filter_bar_shell" in macros
    assert "filter_search" in macros
    assert "filter_select" in macros
    script = (ROOT / "templates" / "partials" / "filter_bar_script.html").read_text()
    assert "KqFilterBar" in script
    assert "No matches" not in script  # empty copy lives in markup
    assert "showing " in script
