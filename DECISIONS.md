# Kill the Quote Spreadsheet — decisions (one page)

**Live prototype:** [kill-the-quote-spreadsheet-lac.vercel.app](https://kill-the-quote-spreadsheet-lac.vercel.app) · **This note in-app:** [/demo/decisions](https://kill-the-quote-spreadsheet-lac.vercel.app/demo/decisions)  
**Persona:** category buyer, corrugated packaging. **Rule followed:** stub plumbing (SMTP); don’t fake extraction, reasoning, or Ask answers.

Open any event from the home list, then use the paths below (`/rfx/<id>/…`).

---

## Decisions → where to see them

| Decision | Why | See it here |
|---|---|---|
| **Messy edges over happy path** | Assignment cares about uncertainty, not a clean Excel. | [Email](https://kill-the-quote-spreadsheet-lac.vercel.app/) → Simulate 5 replies (Excel / PDF / Word / photo / email) → Read all |
| **Live AI for draft, extract, free-ask** | Brief: don’t hardcode demo answers. | Home draft · Email **Read all** · Award **Ask** free-form textarea |
| **Maths in code, prose from the model** | Trust: ₹4cr decisions can’t rest on recalled numbers. | Compare matrix footers · Award packs · Ask answers (tables = engine) |
| **Quality gates at draft time** | Questionnaire comes from gates + brief; gates drive eligibility. | Home: pick gates → draft · Compare gate badges · Award “Pass vendors” |
| **Default award = line split** | Assignment VP question: cheapest per line among who cleared quality. | Award **Who wins** packs (not winner-takes-all) |
| **Compare vs Anomalies split** | Clean verified Pass matrix stays separate from messy exceptions. | `/rfx/<id>/compare` (verified Pass only) · `/rfx/<id>/anomalies` (flags + Override / Send for approval / Deny) |
| **Award = Ask → Lock → Send** | Short close path; Ask stays strong after Compare. | `/rfx/<id>/award`: Ask card → packs → **Lock award** → **Send notices** |
| **Premade Ask = fast cache; free-ask = live Claude** | Demo speed ≠ Email Read-all latency; typed questions stay real. | Award Ask: 3 premade buttons (instant) · textarea (live API) |
| **Freeze binds a snapshot** | Defensible packet; later re-reads don’t silently rewrite a frozen memo. | Award **Lock** · then Outbox notices / freeze.zip |
| **Email channel, stub SMTP** | Brief allows fake mail; Outbox is the proof. | `/rfx/<id>/email` Incoming + Outbox · award/regret stubs after Send |
| **Search/filter on Email & Compare** | Scale the matrix without cluttering Award. | Email status/file filters · Compare verified-cell / vendor filters · Anomalies kind filters (Award: none) |

---

## What we added (product surface)

1. **Draft** — brief + quality-check prefs → live RFx + auto questionnaire.  
2. **Email** — simulate messy replies, live parallel extract, clarifications, Outbox.  
3. **Compare** — verified Pass-only INR/pc matrix, evidence drawer (read/link), Ask drawer, filters.  
3b. **Anomalies** — flagged cells, gate Fail/Partial, coverage gaps, format/pricing callouts; Override / Send for approval / Deny.  
4. **Award** — Ask-before-you-lock (premade + live), per-vendor why packs, one-click Lock (complete or auto-partial), Send stub notices, success banners.  
5. **Trust chrome** — cell states, vendor data version / snapshots, recommendation before lock, historical freeze when data moves.
6. **Charts / exports / audit** — Compare allocation & coverage bars (engine data); Award compact share strip; workbook sheet names + freeze metadata; trust strip (freeze / notices / overrides).

## What we deliberately left out

Real SMTP / vendor portals · inventing “same as last year” prices · auto-zeroing missing freight or footnote discounts · multi-buyer auth / ERP · private blob ACLs · guaranteeing sub-4s five-file live extract · winner-takes-all as default · interview lifecycle/demo strips in the main buyer UI · burying Ask only as a Compare popup.

## Where the interesting problem is

Extraction is getting commodity. The hard product is **awarding under partial, ambiguous data** and making the next action obvious (evidence, anomaly action, Ask, Lock, Send). Next I’d build a vendor “confirm these mappings” loop — not a fancier parser.

