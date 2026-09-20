"""Compare export = price matrix; Award export = awards only."""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook
from fastapi.testclient import TestClient

from app import app
from core import demo_ops, export, storage

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def test_compare_page_links_to_comparison_xlsx():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/compare")
    assert r.status_code == 200
    assert f"/rfx/{st['id']}/comparison.xlsx" in r.text
    # Must not point Compare buyers at the award workbook
    assert 'data-testid="compare-export-link"' in r.text


def test_award_page_still_links_to_export_xlsx():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/award")
    assert r.status_code == 200
    assert f"/rfx/{st['id']}/export.xlsx" in r.text
    assert 'data-testid="award-export-link"' in r.text


def test_comparison_xlsx_route_serves_matrix():
    st = _seed()
    r = client.get(f"/rfx/{st['id']}/comparison.xlsx")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    cd = r.headers.get("content-disposition") or ""
    assert "comparison-" in cd
    wb = load_workbook(io.BytesIO(r.content))
    assert "Comparison" in wb.sheetnames
    assert "Award by line" not in wb.sheetnames
    assert "Non-awarded" not in wb.sheetnames


def test_export_xlsx_route_serves_awards_only():
    st = _seed()
    # Ensure award draft exists so Non-awarded sheet appears
    client.get(f"/rfx/{st['id']}/award")
    r = client.get(f"/rfx/{st['id']}/export.xlsx")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition") or ""
    assert "award-" in cd
    wb = load_workbook(io.BytesIO(r.content))
    assert "Award by line" in wb.sheetnames
    assert "Comparison" not in wb.sheetnames
    # Awarded-to column on award sheet
    ws = wb["Award by line"]
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    assert "Awarded to" in headers


def test_comparison_workbook_vendor_columns_match_seed():
    st = _seed()
    cmp_bytes = export.comparison_workbook(st)
    wb = load_workbook(io.BytesIO(cmp_bytes))
    ws = wb["Comparison"]
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    vendor_names = [v["name"] for v in st["vendors"] if v.get("extraction") or v.get("status") == "extracted"]
    # engine.build_comparison includes extracted vendors; at least 2
    assert sum(1 for h in headers[5:] if h) >= 2
    for name in headers[5:]:
        assert name  # no blank vendor columns
