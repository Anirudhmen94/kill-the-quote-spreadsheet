"""Demo safety + Interview reset → golden MESSY seed.

Golden seed is fixture data that showcases the brief's ugly edges:
  - Kraftline: only 27/30 lines, freight EXTRA, footnote discount not applied
  - PakAsia: USD per 1,000 → FX conversion chip
  - Meghna: photo rate card per box-of-20, low confidence, empty questionnaire
  - Ganesh: ₹/kg email + unresolved "rest same as last year"
  - Sri Balaji: per-100 Excel with one needs-review alternate

Not a hardcoded Ask answer: the seeded recommendation is computed by the
engine and stamped onto a real calculation snapshot. Live Ask still calls the model.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from . import awardability, engine, freeze, gates, rfx_drafter, snapshots, vendor_extraction

DEMO_PROMPTS = [
    "What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?",
    "Which vendor quoted in USD, what FX rate did you use, and how does a 3% weaker rupee change the ranking?",
    "Which lines have no usable quote from anyone — or only one — and what do I need to clarify?",
    "Why is Meghna's photo quote converted per box of 20, and how confident are we in those cells?",
    "Ganesh said 'rest same as last year' and quoted per kg — what did you convert, and what is still unresolved?",
    "If we include needs-review / assumed cells, what changes versus the quality-gated award?",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:10]


def _line(n: int, board: str, L: int, W: int, H: int, gsm: int, qty: int, print_: str = "1 colour") -> dict:
    return {
        "line_no": n,
        "sku": f"CB-{n:03d}",
        "description": f"{board} RSC {L}x{W}x{H} mm, {print_}",
        "board": board,
        "flute": "BC" if board.startswith(("5", "7")) else "B",
        "length_mm": L,
        "width_mm": W,
        "height_mm": H,
        "gsm": gsm,
        "print": print_,
        "annual_qty": qty,
        "notes": "",
        "uom": "pcs",
        "nominal_weight_g": rfx_drafter.nominal_weight_g(L, W, H, gsm),
        "dimensions": f"{L}x{W}x{H} mm",
    }


def _rfx() -> dict:
    specs = [
        ("3-ply", 300, 200, 150, 400, 12000),
        ("3-ply", 350, 250, 180, 420, 10000),
        ("3-ply", 400, 300, 200, 450, 8000),
        ("5-ply", 400, 300, 250, 700, 15000),
        ("5-ply", 450, 350, 300, 750, 12000),
        ("5-ply", 500, 400, 350, 800, 9000),
        ("5-ply", 600, 400, 400, 850, 7000),
        ("7-ply", 600, 500, 400, 1100, 4000),
        ("3-ply", 280, 180, 120, 380, 20000),
        ("5-ply", 550, 400, 350, 780, 6000),
    ]
    lines = []
    for i in range(30):
        board, L, W, H, gsm, qty = specs[i % len(specs)]
        lines.append(_line(i + 1, board, L + (i % 5) * 10, W, H, gsm, qty, "plain" if i % 3 == 0 else "1 colour"))
    return {
        "title": "Corrugated packaging — Chakan FMCG plant (demo seed)",
        "scope": (
            "Annual corrugated shipper requirement for a snacks plant at Chakan. "
            "This demo seed deliberately includes messy vendor replies."
        ),
        "created_at": _now(),
        "line_items": lines,
        "terms": {
            "currency": "INR",
            "price_basis": "INR per piece, delivered Chakan",
            "payment_terms": "30 days from GRN",
            "delivery_location": "Chakan, Pune",
            "delivery_schedule": "monthly call-offs",
            "freight_basis": "delivered",
            "validity_days": 30,
            "gst_treatment": "extra",
            "other": "",
        },
        "questionnaire": [
            {"q_id": "Q1", "text": "Is the manufacturing site ISO 9001 certified?", "answer_type": "yes_no", "knockout": True},
            {"q_id": "Q2", "text": "Can you provide a food-contact / hygiene declaration for shippers?", "answer_type": "yes_no", "knockout": True},
            {"q_id": "Q3", "text": "Attach bursting / ECT test reports for quoted 5-ply grades.", "answer_type": "document", "knockout": True},
            {"q_id": "Q4", "text": "What is your monthly capacity for mixed-SKU call-offs?", "answer_type": "text", "knockout": False},
            {"q_id": "Q5", "text": "Typical recycled content in quoted grades (%)?", "answer_type": "text", "knockout": False},
        ],
    }


def _ev(name: str, loc: str, snippet: str, verified: bool = True) -> dict:
    return {"file_name": name, "location": loc, "snippet": snippet, "verified": verified}


def _q(line_no, price, currency, basis, basis_qty, status="ok", reason="", conf=0.9, evidence=None, candidates=None):
    return {
        "line_no": line_no,
        "candidate_line_nos": candidates or [],
        "vendor_item_ref": "",
        "vendor_description": f"item {line_no}",
        "price": price,
        "currency": currency,
        "price_basis": basis,
        "basis_qty": basis_qty,
        "confidence": conf,
        "status": status,
        "review_cause": "none",
        "reason": reason,
        "evidence": evidence or _ev("quote", f"L{line_no}", str(price), status in ("ok", "converted")),
    }


def _qa(q_id, answer, answered=True, status="ok", conf=1.0):
    return {"q_id": q_id, "answer": answer, "answered": answered, "confidence": conf, "status": status, "evidence": None}


def _term(key, value, pct=None, applies="", status="ok"):
    return {"key": key, "value": value, "numeric_pct": pct, "applies_to": applies, "confidence": 1, "status": status, "evidence": None}


def _vendor(vid, name, city, fmt, email) -> dict:
    return {
        "vendor_id": vid,
        "name": name,
        "city": city,
        "email": email,
        "format": fmt,
        "status": "extracted",
        "files": [
            {
                "file_id": f"{vid}f1",
                "name": f"{vid}-quote.{ 'jpg' if fmt=='image' else ('eml' if fmt=='email' else fmt)}",
                "kind": fmt,
                "url": f"/files/demo/{vid}",
                "size": 100,
                "role": "quote",
            }
        ],
        "extraction": None,
        "texts": {},
        "error": None,
        "received_at": _now(),
    }


def _ext(quotes, not_quoted, questionnaire, commercials, notes="") -> dict:
    return {
        "vendor_name_in_document": "",
        "line_quotes": quotes,
        "not_quoted_line_nos": not_quoted,
        "commercials": commercials,
        "questionnaire": questionnaire,
        "certificates": [],
        "notes": notes,
        "grounding": {
            "checked": len(quotes),
            "verified": sum(1 for q in quotes if (q.get("evidence") or {}).get("verified")),
            "downgraded": 0,
        },
    }


def build_golden_seed(existing_id: str | None = None) -> dict:
    """Deterministic messy event used by Interview reset."""
    rfx = _rfx()
    state = {
        "id": existing_id or _id(),
        "created_at": _now(),
        "brief": "Demo seed — Chakan FMCG corrugated (messy replies on purpose).",
        "status": "compared",
        "rfx": rfx,
        "outbox": [
            {
                "kind": "rfx",
                "to": "vendors@example.com",
                "vendor_id": None,
                "vendor_name": "all",
                "subject": f"RFQ: {rfx['title']}",
                "body": "(stubbed send)",
                "sent_at": _now(),
                "delivery": "stubbed (no SMTP)",
            }
        ],
        "vendors": [],
        "fx": {
            "as_of": "2026-09-15",
            "source": "Buyer treasury reference rate (fixed for this event)",
            "rates_to_inr": {"INR": 1.0, "USD": 83.50, "EUR": 91.20, "GBP": 106.40},
        },
        "reviews": [],
        "exceptions": [],
        "chat": [],
        "recommendation": None,
        "recommendations": [],
        "vendor_data_version": 0,
        "vendor_data_versions": [],
        "calculation_snapshots": [],
        "version_events": [],
        "ai_log": [],
        "demo_mode": True,
        "freeze_packs": [],
        "freeze": None,
        "is_golden_seed": True,
    }

    # v1 Sri Balaji — per 100, cleared, one alternate needs_review
    v1 = _vendor("v1", "Sri Balaji Packaging", "Chakan", "xlsx", "sales@sribalaji.example")
    # Realistic per-100 list (~₹9.5–12/pc after ÷100) so Partial vendors can beat Pass under unrestricted.
    q1 = [
        _q(i, round(950.0 + i * 8.0, 2), "INR", "per_100", 100, evidence=_ev("SBP.xlsx", f"B{i+2}", f"{950.0+i*8.0:.2f}"))
        for i in range(1, 31)
    ]
    q1[4]["status"] = "needs_review"
    q1[4]["reason"] = "Alternate flute offered (B instead of BC); price is for the alternate spec."
    q1[4]["candidate_line_nos"] = [5]
    # Soft assumption — ambiguous line map with a confirmable candidate (distinct from needs-review)
    q1[7]["line_no"] = None
    q1[7]["candidate_line_nos"] = [8]
    q1[7]["status"] = "needs_review"
    q1[7]["reason"] = "Ambiguous row mapping; candidate price shown — confirm before award."
    # Phase D: ensure ≥1 line with no usable quote from anyone (line 30).
    q1[29]["status"] = "missing"
    q1[29]["price"] = None
    q1[29]["reason"] = "Not quoted on this SKU."

    v1["extraction"] = _ext(
        q1,
        [],
        [_qa("Q1", "Yes"), _qa("Q2", "Yes — declaration attached"), _qa("Q3", "Attached"), _qa("Q4", "450 t/month"), _qa("Q5", "35%")],
        [
            _term("freight", "Delivered Chakan"),
            _term("discount", "3% if annual invoice exceeds ₹25 lakh", 3.0, "order value > 25L"),
            _term("payment_terms", "45 days"),
        ],
        notes="Own SKU codes; priced per 100. Conditional discount in notes — not auto-applied.",
    )

    # v2 Kraftline — 27/30, freight extra, footnote discount, cleared
    v2 = _vendor("v2", "Kraftline Industries", "Nashik", "pdf", "quotes@kraftline.example")
    q2 = [
        _q(i, round(9.0 + i * 0.11, 2), "INR", "per_pc", 1, evidence=_ev("Kraftline.pdf", f"p.1 r{i}", f"{9.0+i*0.11:.2f}"))
        for i in range(1, 28)
    ]
    v2["extraction"] = _ext(
        q2,
        [28, 29, 30],
        [_qa("Q1", "Yes"), _qa("Q2", "Yes"), _qa("Q3", "Enclosed test reports"), _qa("Q4", "300 t/month"), _qa("Q5", "40%")],
        [
            _term("freight", "Freight extra at actuals"),
            _term("discount", "5% on orders above ₹20 lakh", 5.0, "footnote page 2"),
        ],
        notes="Quoted 27 of 30. Discount buried in footnote — not auto-applied. Freight EXTRA.",
    )

    # v3 PakAsia — USD per 1000, CIF, Q3 unanswered → Partial gate
    v3 = _vendor("v3", "PakAsia Global", "Singapore", "docx", "tenders@pakasia.example")
    # Lines 3–15 share one blended USD/1000 rate across dissimilar specs (Phase D detector).
    # Lines outside that band are priced aggressively so unrestricted split differs from gated.
    q3 = []
    for i in range(1, 31):
        if 3 <= i <= 15:
            price, note = 100.0, "blended rate USD 100 / 1,000 for lines 3–15"
        elif i == 30:
            continue  # coverage gap — not quoted
        else:
            # Cheap unique rates so Partial PakAsia wins under unrestricted
            price = round(60.0 + i * 0.7, 1)
            note = f"USD {price} / 1,000"
        q3.append(
            _q(i, price, "USD", "per_1000", 1000, conf=0.88, evidence=_ev("PakAsia.docx", f"L{i}", note))
        )
    q3_not_quoted = [30]
    v3["extraction"] = _ext(
        q3,
        q3_not_quoted,
        [
            _qa("Q1", "Yes — ISO 9001:2015"),
            _qa("Q2", "Yes"),
            _qa("Q3", "", answered=False, status="missing", conf=0),
            _qa("Q4", "Export capacity 600 t/month"),
            _qa("Q5", "20%"),
        ],
        [
            _term("freight", "CIF Nhava Sheva", applies="all lines"),
            _term("payment_terms", "LC at sight"),
        ],
        notes="USD per 1,000. Lines 3–15 offered as a single blended rate despite mixed flute/gsm. CIF Nhava Sheva — inland to Chakan NOT included. Knockout Q3 unanswered.",
    )
    # Drop line 30 so the coverage gap is real (Kraftline/Meghna already omit it).
    v3["extraction"]["line_quotes"] = [q for q in v3["extraction"]["line_quotes"] if q.get("line_no") != 30]
    v3["extraction"].setdefault("not_quoted_line_nos", [])
    if 30 not in v3["extraction"]["not_quoted_line_nos"]:
        v3["extraction"]["not_quoted_line_nos"].append(30)

    # v4 Meghna — photo per box/20, empty questionnaire, low confidence, 28/30
    v4 = _vendor("v4", "Meghna Corrugators", "Vatva", "image", "info@meghna.example")
    q4 = [
        _q(i, 180 + i * 7, "INR", "per_box", 20, conf=0.58, evidence=_ev("IMG_ratecard.jpg", f"row~{i}", f"Rate (Rs./Box) {180+i*7}"))
        for i in range(1, 29)
    ]
    v4["extraction"] = _ext(
        q4,
        [29, 30],
        [
            _qa("Q1", "No", conf=0.9),
            _qa("Q2", "", answered=False, status="missing", conf=0),
            _qa("Q3", "", answered=False, status="missing", conf=0),
            _qa("Q4", "", answered=False, status="missing", conf=0),
            _qa("Q5", "", answered=False, status="missing", conf=0),
        ],
        [_term("freight", "Ex-factory Vatva")],
        notes="Angled phone photo. 'Per box of 20 nos' only in small print. Knockout Q1 answered No → gate Fail.",
    )

    # v5 Ganesh — per kg for 3/5-ply, unresolved for 7-ply ("same as last year")
    v5 = _vendor("v5", "Ganesh Board Mills", "Pune", "email", "ganeshboards@example.com")
    q5 = []
    for li in rfx["line_items"]:
        i = li["line_no"]
        if li["board"].startswith("7"):
            q5.append(
                _q(
                    i,
                    None,
                    "INR",
                    "per_pc",
                    1,
                    status="unresolved",
                    reason="Vendor wrote 'rest same as last year' — we do not have last year's prices.",
                    conf=0.4,
                    evidence=_ev("email.eml", "body", "rest same as last year, freight extra"),
                )
            )
        else:
            rate = 51.0 if li["board"].startswith("3") else 54.0
            q5.append(
                _q(
                    i,
                    rate,
                    "INR",
                    "per_kg",
                    1,
                    conf=0.75,
                    evidence=_ev("email.eml", "body", f"₹{rate:g}/kg for the {li['board']}"),
                )
            )
    v5["extraction"] = _ext(
        q5,
        [],
        [
            _qa("Q1", "Yes", conf=0.8),
            _qa("Q2", "Yes", conf=0.8),
            _qa("Q3", "Yes — reports with invoice", conf=0.7),
            _qa("Q4", "Can discuss", conf=0.5),
            _qa("Q5", "", answered=False, status="missing", conf=0),
        ],
        [_term("freight", "Freight extra")],
        notes="One-line email: ₹54/kg 5-ply, ₹51/kg 3-ply, rest same as last year, freight extra.",
    )
    # Force line 30 unresolved so the no-usable-quote gap sticks even if board isn't 7-ply.
    for _qrow in v5["extraction"]["line_quotes"]:
        if _qrow.get("line_no") == 30:
            _qrow["status"] = "unresolved"
            _qrow["price"] = None
            _qrow["reason"] = "Vendor wrote 'rest same as last year' — we do not have last year's prices."


    state["vendors"] = [v1, v2, v3, v4, v5]
    snapshots.ensure_snapshot_fields(state)
    # Explicit extraction_status so demo never shows lingering failed_no_previous
    for v in state["vendors"]:
        vendor_extraction.set_extracted(v, version=0)
        # Ensure no residual failure flags
        blob = v.get("extraction_status") or {}
        blob["technical_error"] = None
        blob["buyer_message"] = None
        v["error"] = None
        v["status"] = "extracted"
    snapshots.bump_vendor_data_version(
        state,
        "vendor_extracted",
        affected_vendor_ids=["v1", "v2", "v3", "v4", "v5"],
        notice="Interview reset · golden messy seed loaded (5 vendor replies)",
    )
    # Stamp last_success_version after bump
    ver = snapshots.current_version(state)
    for v in state["vendors"]:
        vendor_extraction.set_extracted(v, version=ver)

    # Engine-computed recommendation bound to a real snapshot (not a fake LLM answer)
    cmp = awardability.enrich_state_comparison(state)
    eligible = gates.gate_filter_vendors(cmp, cmp["gates"], True)
    split = engine.cheapest_per_line(cmp, eligible, False, False)
    question = DEMO_PROMPTS[0]
    snap = snapshots.create_calculation_snapshot(
        state,
        "analyst_answer",
        parameters={"question": question, "strategy": "quality_gated_cheapest"},
        result={
            "total_extended_inr": split["total_extended_inr"],
            "covered_line_count": cmp["line_count"] - len(split["uncovered_lines"]),
            "uncovered_lines": split["uncovered_lines"],
            "share_by_vendor": split["share_by_vendor"],
        },
    )
    eligible_names = split.get("eligible_vendors") or []
    answer_md = (
        f"**Quality-gated cheapest-per-line** (snapshot `{snap['id']}`, "
        f"vendor data v{snap['vendor_data_version']}).\n\n"
        f"Eligible (Pass gate only): {', '.join(eligible_names) or 'none'}.\n\n"
        f"- Total on covered lines: **{engine.fmt_inr(split['total_extended_inr'])}** / yr\n"
        f"- Lines covered: {cmp['line_count'] - len(split['uncovered_lines'])} / {cmp['line_count']}\n"
        f"- Uncovered: {split['uncovered_lines'] or 'none'}\n\n"
        f"**Excluded on purpose:** {cmp['exclusion_summary']['headline']}\n\n"
        "PakAsia is Partial (knockout document unanswered) — out of the gated split. "
        "Meghna answered no questionnaire — out. "
        "Ganesh 7-ply lines stay unresolved ('same as last year'). "
        "Kraftline freight is EXTRA; its footnote discount is not applied.\n\n"
        "Bound to the calculation snapshot above. If vendor data changes, this goes stale."
    )
    raw = {
        "question": question,
        "answer": answer_md,
        "trace": [{"tool": "cheapest_per_line", "input": {"require_cleared_questionnaire": True}, "ok": True, "output_preview": "quality-gated split"}],
        "tables": [
            {
                "title": "Quality-gated cheapest per line (first 10)",
                "columns": ["line_no", "winner", "unit_inr", "extended_inr"],
                "rows": [
                    {
                        "line_no": r["line_no"],
                        "winner": r.get("winner"),
                        "unit_inr": r.get("unit_inr"),
                        "extended_inr": r.get("extended_inr"),
                    }
                    for r in split["rows"][:10]
                ],
            }
        ],
        "caveats": list(split.get("caveats") or []) + [cmp["exclusion_summary"]["headline"]],
    }
    answer = snapshots.attach_answer_metadata(state, raw, snap)
    state["chat"] = [answer]
    snapshots.save_recommendation_from_answer(state, answer, 0)
    state["status"] = "compared"
    # Demo terminal: 5/5 successfully processed (never 4 processed + 1 failed)
    for v in state["vendors"]:
        st = vendor_extraction.get_status(v)
        if st in (
            vendor_extraction.STATUS_FAILED_NO_PREVIOUS,
            vendor_extraction.STATUS_FAILED_USING_PREVIOUS,
            vendor_extraction.STATUS_AWAITING,
            vendor_extraction.STATUS_EXTRACTING,
        ):
            # Prefer successful extracted fixture data; only exclude if extraction missing
            if v.get("extraction"):
                vendor_extraction.set_extracted(v, version=snapshots.current_version(state))
            else:
                vendor_extraction.set_excluded(
                    v,
                    reason="Interview reset: vendor reply could not be deterministically extracted; excluded for demo terminal state.",
                    actor="interview_reset",
                )
                state.setdefault("buyer_review_log", []).append(
                    {
                        "action": "exclude_vendor",
                        "vendor_id": v.get("vendor_id"),
                        "vendor": v.get("name"),
                        "reason": "Interview reset demo terminal — excluded after non-deterministic extract.",
                        "at": _now(),
                        "actor": "interview_reset",
                    }
                )
    return state


def is_demo_mode(state: dict) -> bool:
    # Only on when explicitly set (Interview reset / golden seed). Missing key ⇒ off
    # so a normal draft can Simulate vendor replies without fighting Demo mode.
    return bool(state.get("demo_mode", False))


def set_demo_mode(state: dict, enabled: bool) -> None:
    state["demo_mode"] = bool(enabled)


def guard_destructive(state: dict, action: str) -> tuple[bool, str]:
    if not is_demo_mode(state):
        return True, ""
    # First Simulate (no files yet) must work even in demo mode — buyers need replies.
    # Only block overwriting an existing messy seed / full wipe.
    if action == "regenerate_replies":
        has_files = any((v.get("files") or []) for v in (state.get("vendors") or []))
        if not has_files:
            return True, ""
        return (
            False,
            "Demo mode is on. Use Interview reset to restore the golden messy seed, "
            "or turn Demo mode off before regenerating vendor replies.",
        )
    if action in ("full_wipe", "delete_rfx", "simulate_overwrite"):
        return (
            False,
            "Demo mode is on. Use Interview reset to restore the golden messy seed, "
            "or turn Demo mode off before a full wipe / regenerate.",
        )
    return True, ""


def interview_reset(existing_id: str | None = None) -> dict:
    return build_golden_seed(existing_id)


def lifecycle_stage(state: dict) -> dict:
    """Compact lifecycle derived from real data."""
    snapshots.ensure_snapshot_fields(state)
    vendors = state.get("vendors") or []
    with_files = [v for v in vendors if v.get("files")]
    extracted = [v for v in with_files if v.get("status") == "extracted" and v.get("extraction")]
    freeze.refresh_freeze_staleness(state)
    from . import event_status

    fr = event_status.active_valid_freeze(state)
    has_rec = any(r.get("status") == "current" for r in (state.get("recommendations") or []))

    stages = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("responses", "Responses"),
        ("extracted", "Extracted"),
        ("compared", "Compared"),
        ("recommended", "Recommended"),
        ("frozen", "Frozen"),
    ]
    if fr:
        current = "frozen"
    elif has_rec:
        current = "recommended"
    elif extracted and len(extracted) == len(with_files) and with_files:
        current = "compared"
    elif extracted:
        current = "extracted"
    elif with_files:
        current = "responses"
    elif state.get("outbox") or state.get("status") == "sent":
        current = "sent"
    else:
        current = "draft"

    idx = [s[0] for s in stages].index(current)
    return {
        "current": current,
        "label": stages[idx][1],
        "steps": [
            {
                "key": k,
                "label": lab,
                "state": "done" if i < idx else ("current" if i == idx else "todo"),
            }
            for i, (k, lab) in enumerate(stages)
        ],
    }
