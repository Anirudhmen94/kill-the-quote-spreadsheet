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
| **Maths in code, prose from the model** | Trust: ₹4cr decisions can’t rest on recalled numbers. | Compare matrix footers · Award shortlist · Ask answers (tables = engine) |
| **Quality gates at draft time** | Questionnaire comes from gates + brief; gates drive eligibility. | Home: pick gates → draft · Compare gate badges · Award “Pass vendors” |
| **Default award = line split** | Assignment VP question: cheapest per line among who cleared quality. | Award **Assign by line** (cheapest eligible Pass / shortlist default) |
| **Compare vs Anomalies split** | Full multi-vendor matrix stays separate from exception workflows. | `/rfx/<id>/compare` (all vendors, all cell statuses) · `/rfx/<id>/anomalies` (flags + Override / Send for approval / Deny) |
| **Award = Ask → top 2 → assign → checks → Send** | One simple buyer close path; Ask stays strong after Compare. | `/rfx/<id>/award`: Ask → Suggest top 2 → Assign by line → Rule checks → **Send award drafts** → confirmation → Export Excel |
| **Premade Ask = fast cache; free-ask = live Claude** | Demo speed ≠ Email Read-all latency; typed questions stay real. | Award Ask: 3 premade buttons (instant) · textarea (live API) |
| **Ask → Send award to vendor preloads Award** | Buyer goes from analyst suggestion straight into Award send path. | Compare/Award Ask: **Send award to vendor** → Award with vendor + reason banner · **Apply this vendor** remains lighter |
| **Award draft + notices, not freeze/lock UX** | Freeze/lock lifecycle was over-engineered for the buyer. Core freeze code may remain for tests/back-compat; Award page does not expose it. | Award Send → Outbox award + regret stubs · **manager/stakeholder notified** · status **Award drafts sent** |
| **Email channel, stub SMTP** | Brief allows fake mail; Outbox is the proof. | `/rfx/<id>/email` Incoming + Outbox · award/regret stubs after Send |
| **Search/filter on Email & Compare** | Scale the matrix without cluttering Award. | Email status/file filters · Compare cell-status / gate / vendor filters · Anomalies kind filters (Award: none) |

---

## What we added (product surface)

1. **Draft** — brief + quality-check prefs → live RFx + auto questionnaire.  
2. **Email** — simulate messy replies, live parallel extract, clarifications, Outbox.  
3. **Compare** — full multi-vendor INR/pc matrix (all statuses + gate badges), evidence drawer (read/link to Anomalies), Ask drawer, filters.  
3b. **Anomalies** — flagged cells, gate Fail/Partial, coverage gaps, format/pricing callouts; Override / Send for approval / Deny.  
4. **Award** — Ask the analyst (premade + live) with **Send award to vendor** (preload + reason) and lighter **Apply this vendor**; Suggest top 2 Pass vendors; Assign by line; rule checkmarks; **Send award drafts** (+ regrets + manager notify); confirmation dialog; Export Excel.  
5. **Trust chrome** — cell states, vendor data version / snapshots, audit strip.  
6. **Charts / exports / audit** — Compare allocation & coverage bars; award workbook with line→vendor sheet + non-awarded summary.

## What we deliberately left out

Real SMTP / vendor portals · inventing “same as last year” prices · auto-zeroing missing freight or footnote discounts · multi-buyer auth / ERP · private blob ACLs · guaranteeing sub-4s five-file live extract · winner-takes-all as default · freeze/lock as the Award buyer path · interview lifecycle/demo strips in the main buyer UI · burying Ask only as a Compare popup.

## Where the interesting problem is

Extraction is getting commodity. The hard product is **awarding under partial, ambiguous data** and making the next action obvious (evidence, anomaly action, Ask, assign, Send). Next I’d build a vendor “confirm these mappings” loop — not a fancier parser.
