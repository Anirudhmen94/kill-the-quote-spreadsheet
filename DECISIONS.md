# Kill the Quote Spreadsheet — decisions note (one page)

**Prototype:** https://kill-the-quote-spreadsheet-lac.vercel.app  
**Persona:** category buyer for corrugated packaging (Chakan plant).  
**Stack:** FastAPI + HTMX, Claude for draft / extract / analyst, deterministic engine for maths, Vercel + Blob.

## What I decided

**Trust over format theatre.** The brief’s messy edges matter more than a clean happy path. Every price must carry verbatim evidence; the model never does arithmetic; uncertainty is a first-class cell state (`ok` / `converted` / `needs review` / `unresolved` / missing). Totals exclude anything unsure unless the buyer overrides with a note.

**Generate replies from the live RFx, then read them for real.** Vendor files are synthesised in fixed ugly formats (Excel-per-100, PDF footnotes, USD Word, angled photo, ₹/kg email) but extraction is a live model call — not hardcoded demo answers. Ask answers are tool-backed engine tables, not recalled prose.

**Quality gates at draft time.** The buyer picks gates on the home screen; the questionnaire is generated from those gates plus the brief. Gates are the single Pass/Partial/Fail engine for Compare and for the default award: *cheapest per line among vendors who cleared knockouts* — the VP question in the assignment.

**Freeze before you defend.** Award freeze locks strategy, line awards, totals, notices and regrets to a calculation snapshot. Later re-reads or exception overrides make that freeze historical so an old memo cannot sit next to new totals.

**Compare vs Exceptions.** Compare is five-vendor comparison only (matrix, colours, read-only evidence, gates as badges). Exceptions owns flagged anomalies: Override (apply into decision), Send for approval (pending + stub outbox), or Deny (close without putting a value into award totals). Pending is Approve/Reject only; Resolved includes overridden, approved, and denied.

**Email is the channel, stubbed on purpose.** Outbound RFx, clarifications (editable drafts), award/regret notices, and internal stakeholder alerts all land in Outbox with no real SMTP — per the brief’s “stub the plumbing, don’t fake the AI loops.”

## What I deliberately left out

Real SMTP and vendor portals; inventing “same as last year” prices; auto-applying footnote discounts or missing freight as zero; multi-buyer auth / ERP hand-off; private blob ACLs (synthetic demo data); guaranteeing sub-4s live reads of five documents (physics of five parallel API calls). Also removed interview chrome (lifecycle/demo strips) so the buyer UI stays the product, not the rehearsal tooling.

## Where the interesting problem is

Extraction is increasingly commodity. The hard product problem is **row matching under ambiguity and awarding under partial data** — and making the buyer’s next action obvious (evidence click, clarification email, exception override, freeze). The next thing I would build is not a better parser; it is a vendor-side “confirm these mappings” loop that closes the gap without anyone reopening Excel.
