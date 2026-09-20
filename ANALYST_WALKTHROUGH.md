# Analyst walkthrough script (record this)

**Length:** ~6–8 minutes of talk, plus model latency on free-ask.  
**Prep:** One event already extracted (fallback). Open Compare with Ask drawer, and keep Award tab ready.  
**Live URL:** https://kill-the-quote-spreadsheet-lac.vercel.app  

Say this almost verbatim; pause where noted.

---

### Open (20 sec)

“This is the part of the week that usually dies in Excel — the VP asks one question and the buyer re-pivots for a day. Here the comparison is already normalized to INR per piece across five messy replies. I’m going to interrogate it in plain language.”

---

### Q1 — The VP question (premade / cached — instant)

**Click the premade:** *“What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?”*

**Say while it answers:**  
“Gates were chosen when we drafted. Only Pass vendors are eligible. The engine does the split math; the model narrates it.”

**Point at:** eligible vendor count, per-line winners, cost vs unrestricted split, any coverage gaps.

**Then click:** **Send award to vendor** (or Apply if you only want to preload without jumping).  
“If I like this recommendation, one click opens Award with that suggestion loaded so I can acknowledge and send drafts — I don’t retype it into a spreadsheet.”

---

### Q2 — Currency / FX edge (live free-ask)

**Type:** *“Who quoted in USD, what FX rate did you use, and how does a 3% weaker rupee change the ranking?”*

**Say while it runs:**  
“PakAsia came in USD. Conversion assumptions are on the Compare cells; Ask should name the rate and show sensitivity — not invent a new table.”

**Point at:** FX disclosure, which lines flip if any, that needs-review cells stay labeled.

---

### Q3 — Coverage / clarify (live or premade)

**Ask:** *“Which lines have no usable quote or only one usable quote, and what should I clarify first?”*

**Then (optional demo beat):** Email → Ganesh → **Draft clarification** → show Outbox.  
“We don’t invent ‘same as last year.’ We surface the gap and draft the email.”

---

### Q4 — Two-supplier constraint (live)

**Ask:** *“If we want at most two suppliers, which two, and what does it cost versus the full quality-gated split?”*

**Point at:** named pair, premium vs full split, that Fail/Partial don’t sneak in as Pass.

---

### Q5 — Defendable close → Award send (1–2 min)

**Ask:** *“Who has the best price per piece among quality-cleared vendors for [pick one line or overall], and why?”*

When it names a vendor (e.g. Sri Balaji):

1. Click **Send award to vendor**  
2. Land on **Award** — show the **reason banner** and that vendor preselected on lines  
3. Tick **Acknowledgements** (confirmation questions)  
4. Click **Send award drafts**  
5. Show confirmation: winners get award drafts, others get regrets, **manager notified**  
6. Optional: **Export Excel** on Award (allocations only) — contrast with Compare’s matrix export

**Close line:**  
“So the analyst isn’t a chatbot bolted on the side — it’s the bridge from messy evidence to a sendable, auditable award draft.”

---

### Extra questions if they have time

- “Apply every conditional discount and re-rank — what conditions apply?”  
- “Include needs-review cells and tell me what changes.”  
- “What did Kraftline not quote, and who is cheapest on those lines among Pass vendors?”  
- “Why is Balaji freight-included and Kraftline freight-extra?” (open evidence)

---

### If they ask “did you hardcode this?”

Draft a **new** corrugated brief on Home (different plant / ply mix) → Simulate → Read all → ask Q1 again. Or upload your own file to a vendor. Free-ask always hits the live model; premades are cached for demo speed only.
