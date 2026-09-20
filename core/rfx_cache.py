"""Canned complete RFx for the seeded Chakan demo brief.

Used only when the submitted brief normalizes equal to vendor_sim.EXAMPLE_BRIEF.
Custom / edited briefs always call the live draft model — never faked.
"""
from __future__ import annotations

import re

# Whitespace-collapsed form of EXAMPLE_BRIEF (kept in sync via tests).
_WS = re.compile(r"\s+")


def normalize_brief(brief: str) -> str:
    return _WS.sub(" ", (brief or "").strip())


def is_example_brief(brief: str) -> bool:
    from .vendor_sim import EXAMPLE_BRIEF

    return normalize_brief(brief) == normalize_brief(EXAMPLE_BRIEF)


def _line(n: int, board: str, L: int, W: int, H: int, gsm: int, qty: int, print_: str, desc: str, flute: str | None = None) -> dict:
    return {
        "line_no": n,
        "sku": f"CB-CHK-{n:03d}",
        "description": desc,
        "board": board,
        "flute": flute or ("BC" if board.startswith(("5", "7")) else "B"),
        "length_mm": L,
        "width_mm": W,
        "height_mm": H,
        "gsm": gsm,
        "print": print_,
        "annual_qty": qty,
        "notes": "",
    }


def cached_chakan_rfx() -> dict:
    """Complete 30-line RFx matching the Chakan snacks brief (no LLM)."""
    specs = [
        _line(1, "3-ply", 380, 260, 180, 420, 180000, "1 colour flexo", "RSC shipper chips 200g family pack"),
        _line(2, "3-ply", 360, 250, 170, 400, 160000, "1 colour flexo", "RSC shipper namkeen 150g"),
        _line(3, "3-ply", 340, 240, 160, 400, 140000, "2 colour flexo", "RSC shipper extruded snacks 100g"),
        _line(4, "3-ply", 400, 280, 200, 450, 120000, "1 colour flexo", "RSC shipper chips multipack"),
        _line(5, "3-ply", 320, 220, 150, 380, 200000, "Unprinted", "Plain RSC inner for pouch packs"),
        _line(6, "3-ply", 300, 200, 140, 380, 150000, "1 colour flexo", "RSC shipper mini snacks 50g"),
        _line(7, "3-ply", 420, 300, 220, 450, 90000, "2 colour flexo", "RSC shipper festive gift assortment"),
        _line(8, "3-ply", 280, 180, 120, 350, 220000, "Unprinted", "Die-cut fitment sleeve for pouches"),
        _line(9, "3-ply", 450, 320, 250, 480, 75000, "1 colour flexo", "RSC shipper club pack chips"),
        _line(10, "3-ply", 350, 250, 190, 420, 110000, "1 colour flexo", "RSC shipper bhujia 400g"),
        _line(11, "3-ply", 330, 230, 155, 400, 130000, "1 colour flexo", "RSC shipper mixture 250g"),
        _line(12, "3-ply", 390, 270, 185, 430, 100000, "2 colour flexo", "RSC shipper branded chips export sample"),
        _line(13, "3-ply", 310, 210, 145, 380, 170000, "Unprinted", "RSC transit carton light snacks"),
        _line(14, "3-ply", 370, 255, 175, 410, 95000, "1 colour flexo", "RSC shipper nachos 180g"),
        _line(15, "3-ply", 405, 285, 205, 450, 85000, "1 colour flexo", "RSC shipper family namkeen"),
        _line(16, "5-ply", 480, 340, 280, 700, 60000, "1 colour flexo", "5-ply RSC export shipper chips"),
        _line(17, "5-ply", 500, 360, 300, 750, 45000, "2 colour flexo", "5-ply RSC heavy namkeen export"),
        _line(18, "5-ply", 520, 380, 320, 780, 40000, "1 colour flexo", "5-ply RSC multipack export"),
        _line(19, "5-ply", 460, 330, 270, 720, 55000, "Unprinted", "5-ply RSC bulk transit Chakan"),
        _line(20, "5-ply", 540, 400, 340, 800, 35000, "1 colour flexo", "5-ply RSC pallet-friendly export"),
        _line(21, "5-ply", 440, 320, 260, 700, 50000, "2 colour flexo", "5-ply RSC branded export tray pack"),
        _line(22, "5-ply", 560, 420, 360, 820, 30000, "1 colour flexo", "5-ply RSC heavy duty export"),
        _line(23, "5-ply", 490, 350, 290, 740, 42000, "Unprinted", "5-ply RSC moisture-prone lanes"),
        _line(24, "3-ply", 600, 400, 80, 450, 80000, "2 colour flexo", "Die-cut display tray chips (shelf ready)", flute="E"),
        _line(25, "3-ply", 550, 380, 70, 430, 70000, "2 colour flexo", "Die-cut display tray namkeen", flute="E"),
        _line(26, "3-ply", 500, 350, 65, 420, 65000, "1 colour flexo", "Die-cut counter display snacks", flute="E"),
        _line(27, "5-ply", 450, 300, 200, 750, 25000, "1 colour flexo", "5-ply RSC institutional catering pack"),
        _line(28, "7-ply", 600, 500, 400, 1100, 12000, "Unprinted", "7-ply master carton palletised export"),
        _line(29, "7-ply", 650, 520, 420, 1200, 10000, "1 colour flexo", "7-ply master carton heavy export"),
        _line(30, "7-ply", 580, 480, 380, 1050, 14000, "Unprinted", "7-ply master carton sea freight"),
    ]
    return {
        "title": "Corrugated packaging FY — Chakan snacks plant (30 SKUs)",
        "scope": (
            "Annual corrugated packaging requirement for a packaged-snacks manufacturer at Chakan (Pune). "
            "Scope covers ~30 SKUs: 3-ply outer shippers for chips and namkeen, 5-ply shippers for export and "
            "heavier loads, a few die-cut display trays, and 7-ply master cartons for palletised export.\n\n"
            "Last-year spend was approximately Rs 3.8 crore. Deliveries are weekly to the Chakan plant. "
            "Prices must be delivered, exclusive of GST, with 60-day validity and 45-day payment terms.\n\n"
            "Vendors must hold ISO 9001 and be able to provide BCT test reports; FSC or recycled-content "
            "declaration is preferred. Print is mostly 1–2 colour flexo with buyer brand marks.\n\n"
            "Submit quotes in any format. Complete the quality questionnaire and attach certificates where requested."
        ),
        "line_items": specs,
        "terms": {
            "currency": "INR",
            "price_basis": "INR per piece, delivered to Chakan plant, exclusive of GST",
            "payment_terms": "45 days from invoice",
            "delivery_location": "Chakan, Pune",
            "delivery_schedule": "Weekly deliveries to plant",
            "freight_basis": "delivered",
            "validity_days": 60,
            "gst_treatment": "exclusive of GST (GST extra as applicable)",
            "other": "Annual volumes indicative; monthly call-offs against PO.",
        },
        "questionnaire": [
            {"q_id": "Q1", "text": "Is your manufacturing site ISO 9001 certified? Attach certificate.", "answer_type": "document", "knockout": True},
            {"q_id": "Q2", "text": "Can you provide BCT test reports for quoted 5-ply and 7-ply grades?", "answer_type": "document", "knockout": True},
            {"q_id": "Q3", "text": "Do you have in-house ECT / bursting strength testing capability?", "answer_type": "yes_no", "knockout": True},
            {"q_id": "Q4", "text": "Can you supply an FSC or recycled-content declaration for kraft liner used?", "answer_type": "yes_no", "knockout": False},
            {"q_id": "Q5", "text": "What is your monthly corrugation capacity (MT) and current utilisation?", "answer_type": "text", "knockout": False},
            {"q_id": "Q6", "text": "Typical lead time from approved artwork to first delivery at Chakan?", "answer_type": "text", "knockout": False},
            {"q_id": "Q7", "text": "Do you support 1–2 colour flexo with spectrophotometer colour checks?", "answer_type": "yes_no", "knockout": False},
            {"q_id": "Q8", "text": "Provide two customer references in FMCG / snacks packaging (name + phone).", "answer_type": "text", "knockout": False},
            {"q_id": "Q9", "text": "Is lot/batch traceability printed on every carton?", "answer_type": "yes_no", "knockout": False},
            {"q_id": "Q10", "text": "Describe your CAPA process for quality complaints.", "answer_type": "text", "knockout": False},
        ],
    }


def enrich_cached_rfx(raw: dict) -> dict:
    """Apply the same deterministic enrichment as live draft_rfx (weights, ids)."""
    from datetime import datetime, timezone

    data = {
        "title": raw["title"],
        "scope": raw["scope"],
        "line_items": [dict(li) for li in raw["line_items"]],
        "terms": dict(raw["terms"]),
        "questionnaire": [dict(q) for q in raw["questionnaire"]],
    }
    for i, li in enumerate(data["line_items"], start=1):
        li["line_no"] = i
        li["uom"] = "pcs"
        from .rfx_drafter import nominal_weight_g
        li["nominal_weight_g"] = nominal_weight_g(
            li["length_mm"], li["width_mm"], li["height_mm"], li["gsm"]
        )
        li["dimensions"] = f'{li["length_mm"]}x{li["width_mm"]}x{li["height_mm"]} mm'
    for i, q in enumerate(data["questionnaire"], start=1):
        q["q_id"] = f"Q{i}"
    data["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data
