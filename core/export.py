"""Exports: award workbook (openpyxl), markdown memo, comparison CSV.

Every export is stamped with RFx id, timestamp, vendor-data version, calculation
snapshot id, and current/historical/provisional status.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import engine, snapshots

HEAD = PatternFill("solid", fgColor="0F172A")
STATUS_FILL = {
    "ok": "DCFCE7",
    "converted": "DBEAFE",
    "reviewed": "E9D5FF",
    "needs_review": "FEF3C7",
    "unresolved": "FECACA",
    "missing": "F1F5F9",
    "not_extracted": "F1F5F9",
}


def _sheet_from_rows(wb: Workbook, title: str, columns: list[str], rows: list[list], widths: dict | None = None):
    ws = wb.create_sheet(title[:31])
    for c, h in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r, row in enumerate(rows, start=2):
        for c, v in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=v)
    for i, col in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(col, max(12, min(60, len(str(col)) + 4)))
    ws.freeze_panes = "A2"
    return ws


def _ensure_export_snapshot(state: dict, provisional: bool) -> tuple:
    """Stamp exports with the same calculation snapshot shown on the Award page.

    Prefer the latest current award_scenario / comparison snapshot so the memo
    and Award page always share an id and totals. Still record an export event
    snapshot for audit.
    """
    snapshots.ensure_snapshot_fields(state)
    # Ensure a live award snapshot exists so memo and Award agree
    live = None
    if any(v.get("extraction") for v in state.get("vendors", [])):
        live = snapshots.live_award_calculation(state)
    calc_snap = None
    if live and live.get("available"):
        calc_snap = live["snapshot"]
    else:
        for s in reversed(state.get("calculation_snapshots", [])):
            if s.get("status") == "current" and s.get("calculation_type") in ("award_scenario", "comparison", "analyst_answer"):
                calc_snap = s
                break
    meta = snapshots.export_meta(state, snapshot=calc_snap, provisional=provisional)
    export_snap = snapshots.create_calculation_snapshot(
        state,
        "export",
        parameters={"provisional": provisional, "based_on": (calc_snap or {}).get("id")},
        result=meta,
        status="current",
    )
    # Primary id exposed to buyers is the calculation snapshot (Award page), not the export event
    if calc_snap:
        meta["calculation_snapshot_id"] = calc_snap["id"]
        meta["export_event_snapshot_id"] = export_snap["id"]
        return meta, calc_snap
    meta["calculation_snapshot_id"] = export_snap["id"]
    return meta, export_snap


def award_workbook(state: dict, provisional: bool = False) -> bytes:
    snapshots.ensure_snapshot_fields(state)
    cmp = engine.build_comparison(state)
    meta, snap = _ensure_export_snapshot(state, provisional)
    # Prefer current saved recommendation; fall back to legacy pointer
    rec = next((r for r in state.get("recommendations", []) if r.get("status") == "current"), None)
    if rec is None:
        rec = state.get("recommendation") or {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"Award recommendation: {cmp['title']}"
    ws["A1"].font = Font(bold=True, size=14)
    from . import freeze as freeze_mod

    pack = freeze_mod.current_freeze(state)
    status_line = (
        f"RFx {cmp['rfx_id']} · exported {meta['export_timestamp']} · "
        f"vendor data version {meta['vendor_data_version']} · snapshot {meta['calculation_snapshot_id']} · "
        f"export status: {meta['export_status']} · "
        f"vendors processed {meta['vendors_processed']} · still processing {meta['vendors_still_processing']} · "
        f"review {meta['needs_review']} · unresolved {meta['unresolved']}"
    )
    if pack:
        status_line += (
            f" · freeze {pack.get('id')} ({pack.get('status')}"
            f"{' · ' + pack['freeze_mode'] if pack.get('freeze_mode') else ''}"
            f") · strategy {pack.get('strategy') or pack.get('strategy_key') or '—'}"
        )
    ws["A2"] = status_line
    if meta.get("provisional"):
        ws["A3"] = "PROVISIONAL EXPORT — vendor responses were still processing at export time."
        ws["A3"].font = Font(bold=True, color="B45309")
    row0 = 5 if meta.get("provisional") else 4
    ws.cell(row=row0, column=1, value="Recommendation (analyst answer saved by buyer)").font = Font(bold=True)
    answer = rec.get("recommendation_markdown") or rec.get("answer") or "No recommendation saved yet. Ask the analyst and click 'Save as award recommendation'."
    if rec.get("status") in ("stale", "superseded") or rec.get("legacy"):
        answer = (
            f"[HISTORICAL / STALE — vendor data version {rec.get('vendor_data_version')}]\n\n" + answer
        )
    cell = ws.cell(row=row0 + 1, column=1, value=answer)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row0 + 1, start_column=1, end_row=row0 + 16, end_column=8)
    ws.cell(row=row0 + 18, column=1, value="Question asked").font = Font(bold=True)
    ws.cell(row=row0 + 19, column=1, value=rec.get("question", ""))
    ws.cell(row=row0 + 21, column=1, value="Caveats").font = Font(bold=True)
    for i, c in enumerate(rec.get("caveats") or engine.caveats_for(cmp, None, None, False), start=row0 + 22):
        ws.cell(row=i, column=1, value=c)
    ws.column_dimensions["A"].width = 110

    from . import award_draft as _ad

    draft = state.get("award_draft") if isinstance(state.get("award_draft"), dict) else None
    draft_rows = None
    if draft and draft.get("allocation"):
        try:
            draft_rows = _ad.line_assignment_rows(state)
            totals = _ad.draft_totals(state)
        except Exception:
            draft_rows = None
            totals = None
    if draft_rows is not None:
        line_by_no = {ln["line_no"]: ln for ln in cmp["lines"]}
        award_rows = []
        for r in draft_rows:
            ln = line_by_no.get(r["line_no"]) or {}
            award_rows.append(
                [
                    r["line_no"],
                    ln.get("sku"),
                    r.get("description"),
                    r.get("annual_qty"),
                    r.get("selected_vendor_name") or "—",
                    r.get("selected_unit_inr"),
                    r.get("extended_inr"),
                    "",
                    "",
                    "",
                ]
            )
        _sheet_from_rows(
            wb,
            "Award by line",
            ["Line", "SKU", "Description", "Annual qty", "Awarded to", "Unit INR", "Extended INR", "Runner-up", "Runner-up unit INR", "Gap %"],
            award_rows,
            {"Description": 50, "Awarded to": 28, "Runner-up": 28},
        )
        ws2 = wb["Award by line"]
        total_ext = (totals or {}).get("total_extended_inr") or 0
        ws2.cell(row=len(award_rows) + 3, column=6, value="Total")
        ws2.cell(row=len(award_rows) + 3, column=7, value=total_ext).font = Font(bold=True)
        ws2.cell(row=len(award_rows) + 5, column=1, value=f"Snapshot {snap['id']} · vendor data version {meta['vendor_data_version']} · {meta['export_status']} · award draft")
        uncovered = (totals or {}).get("uncovered_lines") or []
        if uncovered:
            ws2.cell(row=len(award_rows) + 4, column=1, value=f"Lines without an eligible Pass quote: {uncovered}")
        # Regret / non-awarded summary
        winner_ids = {r.get("selected_vendor_id") for r in draft_rows if r.get("selected_vendor_id")}
        regret_rows = []
        for v in cmp["vendors"]:
            if v["vendor_id"] in winner_ids:
                continue
            regret_rows.append(
                [
                    v["name"],
                    v.get("gate") or "",
                    "regret / not awarded",
                    v.get("usable"),
                ]
            )
        _sheet_from_rows(
            wb,
            "Non-awarded",
            ["Vendor", "Gate", "Status", "Usable lines"],
            regret_rows,
            {"Vendor": 28, "Status": 24},
        )
    else:
        cp = engine.cheapest_per_line(cmp, None, False, False)
        _sheet_from_rows(
            wb,
            "Award by line",
            ["Line", "SKU", "Description", "Annual qty", "Awarded to", "Unit INR", "Extended INR", "Runner-up", "Runner-up unit INR", "Gap %"],
            [[r["line_no"], ln["sku"], r["description"], r["annual_qty"], r["winner"], r["unit_inr"], r["extended_inr"], r["runner_up"], r["runner_up_unit_inr"], r.get("gap_pct")] for r, ln in zip(cp["rows"], cmp["lines"])],
            {"Description": 50, "Awarded to": 28, "Runner-up": 28},
        )
        ws2 = wb["Award by line"]
        ws2.cell(row=len(cp["rows"]) + 3, column=6, value="Total")
        ws2.cell(row=len(cp["rows"]) + 3, column=7, value=cp["total_extended_inr"]).font = Font(bold=True)
        ws2.cell(row=len(cp["rows"]) + 5, column=1, value=f"Snapshot {snap['id']} · vendor data version {meta['vendor_data_version']} · {meta['export_status']}")
        if cp["uncovered_lines"]:
            ws2.cell(row=len(cp["rows"]) + 4, column=1, value=f"Lines without a usable quote: {cp['uncovered_lines']}")

    cols = ["Line", "SKU", "Description", "Board", "Annual qty"] + [v["name"] for v in cmp["vendors"]]
    ws3 = _sheet_from_rows(wb, "Comparison", cols, [], {"Description": 50})
    for r, ln in enumerate(cmp["lines"], start=2):
        base = [ln["line_no"], ln["sku"], ln["description"], ln["board"], ln["annual_qty"]]
        for c, v in enumerate(base, start=1):
            ws3.cell(row=r, column=c, value=v)
        for i, v in enumerate(cmp["vendors"]):
            cell = ln["cells"][v["vendor_id"]]
            xc = ws3.cell(row=r, column=6 + i, value=cell.get("unit_inr") if cell.get("unit_inr") is not None else cell["status"])
            xc.fill = PatternFill("solid", fgColor=STATUS_FILL.get(cell["status"], "FFFFFF"))
            if cell.get("conversion") or cell.get("reason"):
                from openpyxl.comments import Comment

                xc.comment = Comment(((cell.get("conversion") or "") + "\n" + (cell.get("reason") or "")).strip()[:900], "engine")
    legend_row = len(cmp["lines"]) + 3
    ws3.cell(row=legend_row, column=1, value="Legend: green ok · blue converted · purple reviewed · amber needs review · red unresolved · grey missing. Hover a cell for conversion notes.")

    fl = engine.list_flags(cmp)
    _sheet_from_rows(wb, "Flags", ["Line", "Vendor", "Status", "Flags", "Reason", "Conversion", "Raw price", "Currency", "Basis", "Unit INR", "Confidence"], [[i["line_no"], i["vendor"], i["status"], ", ".join(i["flags"]), i["reason"], i["conversion"], i["raw_price"], i["currency"], i["basis"], i["unit_inr"], i["confidence"]] for i in fl["items"]], {"Reason": 60, "Conversion": 60, "Vendor": 28})

    _sheet_from_rows(wb, "Vendors", ["Vendor", "Usable lines", "Needs review", "Unresolved", "Missing", "Questionnaire", "Knockouts open", "Knockouts failed", "Freight", "Payment", "Validity", "Discount", "Avg confidence"], [[v["name"], v["usable"], v["counts"].get("needs_review", 0), v["counts"].get("unresolved", 0), v["counts"].get("missing", 0), v["questionnaire"]["overall"], ", ".join(v["questionnaire"]["knockout_open"]), ", ".join(v["questionnaire"]["knockout_failed"]), v["commercial"].get("freight_text"), v["commercial"].get("payment"), v["commercial"].get("validity"), v["commercial"].get("discount_condition"), v["avg_confidence"]] for v in cmp["vendors"]], {"Vendor": 28, "Freight": 40, "Payment": 30, "Discount": 50})

    ev_rows = []
    for ln in cmp["lines"]:
        for v in cmp["vendors"]:
            c = ln["cells"][v["vendor_id"]]
            ev = c.get("evidence")
            if ev:
                ev_rows.append([ln["line_no"], v["name"], c.get("raw_price"), c.get("currency"), c.get("basis"), ev.get("file_name"), ev.get("location"), ev.get("snippet"), "yes" if ev.get("verified") else "NO"])
    _sheet_from_rows(wb, "Evidence", ["Line", "Vendor", "Raw price", "Currency", "Basis", "File", "Location", "Verbatim snippet", "Verified in source"], ev_rows, {"Vendor": 28, "File": 34, "Location": 20, "Verbatim snippet": 70})

    _sheet_from_rows(wb, "Review log", ["When", "Vendor", "Line", "Action", "Value INR", "Note"], [[r.get("at"), r.get("vendor_name"), r["line_no"], r["action"], r.get("value_inr"), r.get("note")] for r in state.get("reviews", [])], {"Note": 60})

    meta_rows = [
        ["RFx ID", meta["rfx_id"]],
        ["Export timestamp", meta["export_timestamp"]],
        ["Vendor data version", meta["vendor_data_version"]],
        ["Calculation snapshot ID", meta["calculation_snapshot_id"]],
        ["Export status", meta["export_status"]],
        ["Vendors processed", meta["vendors_processed"]],
        ["Still processing", meta["vendors_still_processing"]],
        ["Needs review", meta["needs_review"]],
        ["Unresolved", meta["unresolved"]],
        ["Input hash", snap.get("input_hash")],
    ]
    if pack:
        meta_rows.extend(
            [
                ["Freeze ID", pack.get("id")],
                ["Freeze status", pack.get("status")],
                ["Freeze mode", pack.get("freeze_mode")],
                ["Freeze strategy", pack.get("strategy") or pack.get("strategy_key")],
                ["Freeze snapshot ID", pack.get("calculation_snapshot_id")],
                ["Freeze vendor data version", pack.get("vendor_data_version")],
                ["Freeze total INR", pack.get("total_extended_inr")],
            ]
        )
    _sheet_from_rows(wb, "Snapshot metadata", ["Field", "Value"], meta_rows)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def comparison_csv(state: dict, provisional: bool = False) -> bytes:
    snapshots.ensure_snapshot_fields(state)
    meta, snap = _ensure_export_snapshot(state, provisional)
    df = engine.to_dataframe(engine.build_comparison(state))
    # Prepend a metadata header row as comments via a leading block
    header = (
        f"# RFx {meta['rfx_id']} · vendor_data_version={meta['vendor_data_version']} · "
        f"snapshot={meta['calculation_snapshot_id']} · status={meta['export_status']} · "
        f"exported={meta['export_timestamp']}\n"
    )
    if meta.get("provisional"):
        header += "# PROVISIONAL EXPORT — vendor responses were still processing at export time.\n"
    return (header + df.to_csv(index=False)).encode("utf-8")


def award_memo_md(state: dict, provisional: bool = False) -> str:
    snapshots.ensure_snapshot_fields(state)
    cmp = engine.build_comparison(state)
    meta, snap = _ensure_export_snapshot(state, provisional)
    rec = next((r for r in state.get("recommendations", []) if r.get("status") == "current"), None)
    if rec is None:
        rec = state.get("recommendation") or {}
    # Live calculation must agree with Award page
    cp = engine.cheapest_per_line(cmp, None, False, False)
    out = [f"# Award recommendation: {cmp['title']}", ""]
    if meta.get("provisional"):
        out += ["> **PROVISIONAL EXPORT — vendor responses were still processing at export time.**", ""]
    from . import freeze as freeze_mod

    pack = freeze_mod.current_freeze(state)
    out += [
        f"RFx `{cmp['rfx_id']}` · {meta['export_timestamp']}",
        f"Vendor data version **{meta['vendor_data_version']}** · Calculation snapshot `{meta['calculation_snapshot_id']}` · Export status: **{meta['export_status']}**",
        f"Vendors processed: {meta['vendors_processed']} · Still processing: {meta['vendors_still_processing']} · Review: {meta['needs_review']} · Unresolved: {meta['unresolved']}",
    ]
    if pack:
        out.append(
            f"Freeze `{pack.get('id')}` · status **{pack.get('status')}**"
            + (f" · mode {pack.get('freeze_mode')}" if pack.get('freeze_mode') else "")
            + f" · strategy {pack.get('strategy') or pack.get('strategy_key') or '—'}"
        )
    out.append("")
    status = rec.get("status")
    if status == "current":
        out += ["## Current award recommendation", ""]
    elif rec:
        out += ["## Historical recommendation — not valid for the current vendor dataset", ""]
        out.append(f"_Stale reason:_ {rec.get('stale_reason', 'Vendor data changed after this was saved.')}")
        out.append("")
    else:
        out += ["## Recommendation", ""]
    out += [rec.get("recommendation_markdown") or rec.get("answer") or "_No recommendation saved yet._", ""]
    if rec.get("question"):
        out += [f"_Question asked:_ {rec['question']}", ""]
        if rec.get("calculation_snapshot_id"):
            out += [f"_Recommendation snapshot:_ `{rec.get('calculation_snapshot_id')}` · vendor data version {rec.get('vendor_data_version')}", ""]
    out += ["## Current live calculation (cheapest-per-line, usable quotes only)", "", f"Total extended annual value: **{engine.fmt_inr(cp['total_extended_inr'])}** · Snapshot `{snap['id']}`", ""]
    for name, s in cp["share_by_vendor"].items():
        out.append(f"- {name}: {s['lines']} lines, {engine.fmt_inr(s['extended_inr'])}")
    if cp["uncovered_lines"]:
        out.append(f"- Lines with no usable quote: {cp['uncovered_lines']}")
    out += ["", "## Vendor status", ""]
    for v in cmp["vendors"]:
        out.append(f"- **{v['name']}**: usable {v['usable']}/{cmp['line_count']}, needs review {v['counts'].get('needs_review',0)}, unresolved {v['counts'].get('unresolved',0)}, missing {v['counts'].get('missing',0)}; questionnaire {v['questionnaire']['overall']}; freight {'extra' if v['commercial'].get('freight_extra') else 'as quoted'}")
    out += ["", "## Caveats", ""]
    for c in rec.get("caveats") or engine.caveats_for(cmp, None, None, False):
        out.append(f"- {c}")
    if state.get("reviews"):
        out += ["", "## Buyer review log", ""]
        for r in state["reviews"]:
            out.append(f"- {r.get('at','')[:16]} {r.get('vendor_name')} L{r['line_no']}: {r['action']}{' → ₹' + str(r['value_inr']) if r.get('value_inr') is not None else ''} — {r.get('note','')}")
    return "\n".join(out)



def audit_trail_csv(state: dict) -> bytes:
    """Small audit CSV: buyer reviews + freeze/notices summary (engine/state only)."""
    import csv

    snapshots.ensure_snapshot_fields(state)
    from . import freeze as freeze_mod

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "at", "vendor", "line", "action", "value_inr", "note", "extra"])
    for r in state.get("buyer_review_log") or state.get("reviews") or []:
        w.writerow(
            [
                "review",
                r.get("at"),
                r.get("vendor_name") or r.get("vendor"),
                r.get("line_no"),
                r.get("action"),
                r.get("value_inr"),
                r.get("note"),
                r.get("source") or "",
            ]
        )
    pack = freeze_mod.current_freeze(state)
    if pack:
        w.writerow(
            [
                "freeze",
                pack.get("frozen_at"),
                "",
                "",
                pack.get("status"),
                pack.get("total_extended_inr"),
                pack.get("strategy") or pack.get("strategy_key"),
                pack.get("id"),
            ]
        )
    for m in state.get("outbox") or []:
        if (m.get("kind") or "") in ("award_notice", "regret", "regret_notice", "stakeholder_alert"):
            w.writerow(
                [
                    "outbox",
                    m.get("sent_at"),
                    m.get("vendor_name"),
                    "",
                    m.get("kind"),
                    "",
                    m.get("subject"),
                    m.get("freeze_id") or "",
                ]
            )
    return buf.getvalue().encode("utf-8")
