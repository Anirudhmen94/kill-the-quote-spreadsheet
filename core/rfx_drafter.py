"""Turn a plain-language brief into a complete RFx (AI), then enrich it with
deterministic engineering data (nominal carton weight) that later powers
per-kg to per-piece conversion."""
from __future__ import annotations

import copy
import re
import time
import uuid
from datetime import datetime, timezone

from . import draft_gates, llm, rfx_cache
from .models import RFxDraftAI

# Patterns for stated SKU / line-item counts in buyer briefs.
_COUNT_PATTERNS = [
    re.compile(
        r"(?i)\b(?:approximately|approx\.?|about|around|~|roughly)?\s*"
        r"(\d{1,3})\s*(?:SKU|SKUs|line\s*items?|lines?|items?|cartons?|skus)\b"
    ),
    re.compile(
        r"(?i)\b(\d{1,3})\s*(?:SKU|SKUs|line\s*items?|lines?)\b"
    ),
    re.compile(
        r"(?i)\b(?:SKU|SKUs|line\s*items?|lines?)\s*[:=]?\s*(\d{1,3})\b"
    ),
]

DEFAULT_LINE_COUNT = 30  # only when brief does not state a count


def parse_target_line_count(brief: str, default: int | None = DEFAULT_LINE_COUNT) -> int | None:
    """Extract the stated SKU/line-item count from a buyer brief.

    Returns ``default`` when no count is found (``None`` if default is None).
    Prefers the first plausible match in 1..200.
    """
    text = brief or ""
    for pat in _COUNT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= 200:
            return n
    return default


def _system_prompt(n: int) -> str:
    return f"""You are a senior category manager for packaging procurement in India, drafting an RFx
(request for quotation) for corrugated packaging on behalf of a buyer.

Produce a realistic, procurement-grade RFx that a supplier would recognise:
- EXACTLY {n} line items — match the brief's stated SKU / line-item count. Do not invent extra
  lines to pad a fixed catalog. Mix ply types (3-ply / 5-ply / 7-ply / display) only as the brief describes.
  Use realistic RSC / die-cut carton dimensions in mm, typical Indian board grammages
  (3-ply ~ 350-500 gsm, 5-ply ~ 600-900 gsm, 7-ply ~ 1000-1300 gsm), flute profiles (B, C, BC, E),
  and print specs. Quantities should be annual volumes appropriate to the brief. SKU codes sequential.
- Descriptions must be specific enough that a vendor could quote them without calling back.
- Commercial terms: INR per piece, delivered, exclusive of GST is the default basis unless the brief says otherwise.
- Questionnaire: emit a short stub only (the system replaces it from the buyer's selected
  quality checks). Prefer matching the check labels if listed in the user message; do not invent
  a long unrelated questionnaire.
- Follow the brief's numbers if it gives any (volumes, sizes, plant location, timelines, SKU count). Never contradict it.
- Do not invent supplier names or prices. This is the buyer's document.
- Keep each line description under ~80 characters so the full {n}-item payload fits reliably.
"""


def nominal_weight_g(length_mm: int, width_mm: int, height_mm: int, gsm: int) -> float:
    """Approximate blank weight of a regular slotted carton (RSC).

    Blank length = 2*(L+W) + 40 mm glue flap; blank height = H + W (two flaps of W/2).
    weight = area(m2) * gsm. This is a nominal figure used only to convert per-kg quotes
    to per-piece for comparison, and is always disclosed alongside the conversion.
    """
    blank_len = 2 * (length_mm + width_mm) + 40
    blank_h = height_mm + width_mm
    area_m2 = (blank_len * blank_h) / 1_000_000
    return round(area_m2 * gsm, 1)


def _enrich(data: dict) -> dict:
    for i, li in enumerate(data["line_items"], start=1):
        li["line_no"] = i  # enforce sequential numbering regardless of model output
        li["uom"] = "pcs"
        li["nominal_weight_g"] = nominal_weight_g(li["length_mm"], li["width_mm"], li["height_mm"], li["gsm"])
        li["dimensions"] = f'{li["length_mm"]}x{li["width_mm"]}x{li["height_mm"]} mm'
    for i, q in enumerate(data["questionnaire"], start=1):
        q["q_id"] = f"Q{i}"
    data["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def _pad_line_from(template: dict, line_no: int) -> dict:
    """Clone a line item for post-process padding when the model under-emits."""
    li = copy.deepcopy(template)
    li["line_no"] = line_no
    # Keep SKU readable/sequential; preserve prefix if present.
    sku = str(li.get("sku") or "CB-RSC")
    base = re.sub(r"\d+$", "", sku).rstrip("-") or "CB-RSC"
    li["sku"] = f"{base}-{line_no:03d}"
    # Slight dimension nudge so padded rows are not identical clones.
    li["length_mm"] = int(li.get("length_mm") or 300) + (line_no % 7) * 5
    li["width_mm"] = int(li.get("width_mm") or 200)
    li["height_mm"] = int(li.get("height_mm") or 150)
    desc = str(li.get("description") or "Corrugated carton")
    if "(variant" not in desc.lower():
        li["description"] = f"{desc} (variant {line_no})"
    return li


def enforce_line_count(data: dict, n: int) -> dict:
    """Hard post-process: trim or pad ``line_items`` to exactly ``n`` rows."""
    items = list(data.get("line_items") or [])
    if n < 1:
        raise ValueError("target line count must be >= 1")
    if len(items) > n:
        items = items[:n]
    elif len(items) < n:
        if not items:
            raise ValueError("cannot pad: model returned zero line items")
        template = items[-1]
        while len(items) < n:
            items.append(_pad_line_from(template, len(items) + 1))
    data["line_items"] = items
    return data


def draft_rfx(brief: str, log: list | None = None, gates: list[dict] | None = None) -> dict:
    """Draft an RFx. Optional `gates` drive the questionnaire (and the prompt).

    Cached EXAMPLE_BRIEF path keeps canned lines/terms and rebuilds the
    questionnaire from `gates` so gate customization stays instant.
    Custom / edited briefs always call the live draft model (Anthropic) and
    enforce the brief's stated SKU / line-item count.
    """
    gates = gates if gates is not None else draft_gates.default_gates()

    # Instant path: seeded Chakan demo brief → canned lines/terms (no LLM),
    # then questionnaire always rebuilt from selected gates.
    if rfx_cache.is_example_brief(brief):
        started = time.time()
        data = rfx_cache.enrich_cached_rfx(rfx_cache.cached_chakan_rfx())
        data = draft_gates.apply_gates_to_rfx(data, gates, brief=brief)
        if log is not None:
            log.append(
                {
                    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "purpose": "draft_rfx_cached",
                    "model": "cache:chakan_example",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "latency_s": round(time.time() - started, 2),
                    "attempt": 1,
                    "stop_reason": "cache_hit",
                    "gates_customized": not draft_gates.gates_match_defaults(gates),
                }
            )
        return data

    target_n = parse_target_line_count(brief) or DEFAULT_LINE_COUNT
    system = _system_prompt(target_n)
    brief_text = brief.strip()
    content = (
        "Buyer brief (plain language):\n\n" + brief_text + "\n\n"
        + draft_gates.gates_prompt_block(gates) + "\n\n"
        + f"Draft the complete RFx now. Remember: exactly {target_n} line items matching ONLY what "
        f"the brief describes (SKU count, ply mix, categories). Prefer concise descriptions; "
        f"one successful emit is better than a retry."
    )
    draft = llm.structured(
        purpose="draft_rfx",
        system=system,
        content=content,
        schema=RFxDraftAI,
        max_tokens=16000,
        log=log,
        model=llm.draft_model_name(),
    )

    if len(draft.line_items) != target_n:
        content2 = (
            content
            + f"\n\nYour previous draft had {len(draft.line_items)} line items. "
            f"Produce exactly {target_n}, keeping the same style and matching the brief."
        )
        draft = llm.structured(
            purpose="draft_rfx_retry",
            system=system,
            content=content2,
            schema=RFxDraftAI,
            max_tokens=16000,
            log=log,
            model=llm.draft_model_name(),
        )

    data = draft.model_dump(mode="json")
    data = enforce_line_count(data, target_n)
    data = _enrich(data)
    return draft_gates.apply_gates_to_rfx(data, gates, brief=brief)


def new_state(brief: str, rfx: dict) -> dict:
    return {
        "id": uuid.uuid4().hex[:10],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brief": brief,
        "status": "draft",
        "rfx": rfx,
        "outbox": [],
        "vendors": [],
        "fx": {"as_of": "2026-09-15", "source": "Buyer treasury reference rate (fixed for this event)", "rates_to_inr": {"INR": 1.0, "USD": 83.50, "EUR": 91.20, "GBP": 106.40, "AED": 22.73, "SGD": 62.10}},
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
        "demo_mode": False,
    }
