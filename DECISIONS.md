# Kill the Quote Spreadsheet — one-page decisions

**Live demo:** https://kill-the-quote-spreadsheet-lac.vercel.app  
**Persona:** category buyer · corrugated packaging · ~30 lines · 5 messy vendor replies  
**Rule we followed:** stub SMTP; do **not** fake extraction, reasoning, or Ask answers.

---

## What we decided (and why)

| Decision | Why it matters for the brief |
|---|---|
| **Messy replies in, normalized matrix out** | The week to delete is retyping Excel/PDF/Word/photo/email into one sheet. We simulate all five shapes and extract live into INR/pc. |
| **Brief → exact line count via Claude** | Draft RFx line items respect the brief’s SKU/count so the matrix matches what the buyer described — not a fixed demo stub. |
| **Maths in code; prose from the model** | A ₹4cr buyer won’t trust recalled numbers. Comparison totals, cheapest-per-line, and Ask tables come from deterministic engines; Claude explains them. |
| **Quality gates at draft → questionnaire → eligibility** | Matches the VP question: cheapest per line **only among who cleared quality**. Gates chosen on draft drive the questionnaire and Pass/Partial/Fail on Compare/Award. |
| **Compare = full matrix; Anomalies = exceptions** | Buyers need every vendor’s price in one view *and* a place to act on flags (Override / Send for approval / Deny) without cluttering the matrix. |
| **Award = Ask → top 2 → assign → acknowledgements → Send** | Close path is sendable drafts + regrets + manager notify — not a freeze/lock bureaucracy. Line-split default answers the brief’s split question. |
| **Send → confirmation dialog + distinct Outbox** | After Send: modal lists winners, regrets, and manager email (View Outbox / Close). Outbox gets **one row per** award notice, regret, and manager notice. |
| **Ask: premade cache + live free-ask; “Send award to vendor”** | Demo can move fast on canned questions; typed questions hit Claude live. Analyst suggestion preloads Award with vendor + reason so the buyer can approve and send. |
| **Compare export ≠ Award export** | Compare workbook = line × vendor **price matrix**. Award workbook = who wins which lines + non-awarded. |

---

## What we deliberately left out

Real SMTP / vendor portals · inventing “same as last year” prices · auto-applying footnote discounts or missing freight · multi-user auth / ERP · winner-takes-all as default · freeze/lock as the primary Award UX · P0/P1 seed hardening / partial-award demo theatre · guaranteeing sub-4s for five live extracts · burying Ask only as a popup.

---

## Where the interesting problem actually is

Extraction is getting commodity. The hard product is **awarding under partial, ambiguous data** and making the next action obvious (evidence → anomaly action → Ask → assign → Send). Next I’d build a vendor “confirm these mappings” loop — not a fancier parser.

---

## Map to the live product

1. **Draft** — brief (+ SKU count via Claude) + quality checks → live RFx + auto questionnaire  
2. **Email** — Simulate 5 replies → Read all (live extract) → Outbox (award / regret / manager each as its own row)  
3. **Compare** — multi-vendor matrix, charts, Ask drawer, **Export workbook** (matrix only)  
4. **Anomalies** — flagged cells / gaps · Override / Approve / Deny  
5. **Award** — Ask · Suggest top 2 · Assign by line · Acknowledgements · **Send** (confirmation dialog) · Export Excel (awards)  
