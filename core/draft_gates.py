"""Draft-time quality checks → auto-generated questionnaire.

Buyers pick short-label quality checks on the home page (On + optional Knockout).
They do NOT author question wording or answer types. On draft, selected checks
become questionnaire items: known ids use solid templates (lightly tailored from
the brief); custom short labels get a sensible yes_no/document question.
"""
from __future__ import annotations

import re
from copy import deepcopy

# Sensible defaults for corrugated India / snacks-plant procurement.
# `question` / `answer_type` here are SYSTEM templates — not buyer-authored fields.
DEFAULT_GATES: list[dict] = [
    {
        "id": "iso_9001",
        "label": "ISO 9001 certification",
        "knockout": True,
        "enabled": True,
        "answer_type": "document",
        "question": "Is your manufacturing site ISO 9001 certified? Attach certificate.",
    },
    {
        "id": "bct_reports",
        "label": "BCT test reports",
        "knockout": True,
        "enabled": True,
        "answer_type": "document",
        "question": "Can you provide BCT test reports for quoted 5-ply and 7-ply grades?",
    },
    {
        "id": "ect_burst",
        "label": "In-house ECT / bursting testing",
        "knockout": True,
        "enabled": True,
        "answer_type": "yes_no",
        "question": "Do you have in-house ECT / bursting strength testing capability?",
    },
    {
        "id": "fsc_recycled",
        "label": "FSC / recycled content preferred",
        "knockout": False,
        "enabled": True,
        "answer_type": "yes_no",
        "question": "Can you supply an FSC or recycled-content declaration for kraft liner used?",
    },
    {
        "id": "food_contact",
        "label": "Food-contact / hygiene (snacks)",
        "knockout": False,
        "enabled": True,
        "answer_type": "yes_no",
        "question": "Do cartons intended for snack foods meet food-contact / hygiene requirements where applicable?",
    },
    {
        "id": "capacity",
        "label": "Capacity & utilisation",
        "knockout": False,
        "enabled": True,
        "answer_type": "text",
        "question": "What is your monthly corrugation capacity (MT) and current utilisation?",
    },
    {
        "id": "lead_time",
        "label": "Lead time to first delivery",
        "knockout": False,
        "enabled": True,
        "answer_type": "text",
        "question": "Typical lead time from approved artwork to first delivery at plant?",
    },
    {
        "id": "delivery_plant",
        "label": "Weekly delivery to plant",
        "knockout": True,
        "enabled": True,
        "answer_type": "yes_no",
        "question": "Can you support weekly delivered shipments to the buyer's plant on the stated schedule?",
    },
]

_DEFAULT_BY_ID = {g["id"]: g for g in DEFAULT_GATES}

# Keywords that suggest a document upload rather than yes/no.
_DOC_HINTS = re.compile(
    r"\b(cert|certificate|certification|report|reports|declaration|attach|attachment|"
    r"document|docs|iso|fssai|audit|coa|msds|sds)\b",
    re.I,
)
_TEXT_HINTS = re.compile(
    r"\b(capacity|utilisation|utilization|lead\s*time|volume|mt\b|tonnage|headcount|"
    r"describe|explain|how\s+many|what\s+is)\b",
    re.I,
)


def default_gates() -> list[dict]:
    return deepcopy(DEFAULT_GATES)


def gates_match_defaults(gates: list[dict] | None) -> bool:
    """True when the enabled/knockout set matches DEFAULT_GATES (cache-friendly)."""
    if gates is None:
        return True
    defaults = {g["id"]: g for g in DEFAULT_GATES}
    chosen = {g["id"]: g for g in gates if g.get("enabled", True)}
    if set(chosen) != {gid for gid, g in defaults.items() if g.get("enabled", True)}:
        return False
    for gid, g in chosen.items():
        d = defaults.get(gid)
        if not d:
            return False
        if bool(g.get("knockout")) != bool(d.get("knockout")):
            return False
        if bool(g.get("enabled", True)) != bool(d.get("enabled", True)):
            return False
    for g in gates:
        if g.get("id") not in defaults and g.get("enabled", True):
            return False
    return True


def brief_context(brief: str | None) -> dict:
    """Light heuristics: plant/location and category cues from the buyer brief."""
    text = (brief or "").strip()
    loc = None
    # Common India plant / city patterns in this demo domain
    m = re.search(
        r"\b(?:plant\s+in|deliver(?:y|ies)?\s+(?:to|at)|located\s+in|site\s+at)\s+"
        r"([A-Z][A-Za-z]+(?:\s*\([^)]+\))?)",
        text,
    )
    if m:
        loc = m.group(1).strip()
    if not loc:
        for place in ("Chakan", "Pune", "Nashik", "Bhiwandi", "Manesar", "Sri City", "Hosur"):
            if re.search(rf"\b{re.escape(place)}\b", text, re.I):
                loc = place
                break
    category = None
    if re.search(r"\b(snack|namkeen|chips|fmcg\s+food|food[\s-]?contact)\b", text, re.I):
        category = "snacks / food packaging"
    elif re.search(r"\bcorrugat", text, re.I):
        category = "corrugated packaging"
    return {"location": loc, "category": category, "brief": text}


def _infer_answer_type(label: str) -> str:
    if _DOC_HINTS.search(label or ""):
        return "document"
    if _TEXT_HINTS.search(label or ""):
        return "text"
    return "yes_no"


def _question_from_custom_label(label: str, ctx: dict) -> tuple[str, str]:
    """Build a sensible question + answer_type from a short buyer label."""
    label = (label or "").strip() or "this requirement"
    at = _infer_answer_type(label)
    loc = ctx.get("location")
    plant_bit = f" for deliveries to {loc}" if loc else ""

    if at == "document":
        # Certs / reports → ask to confirm + attach
        if re.search(r"\b(cert|certificate|certification|iso)\b", label, re.I):
            q = f"Do you hold a current {label}? Attach the certificate{plant_bit}."
        elif re.search(r"\b(report|reports|declaration|coa|msds|sds)\b", label, re.I):
            q = f"Can you provide {label} for the grades you quote{plant_bit}?"
        else:
            q = f"Can you provide documentation for {label}? Attach supporting evidence."
        return q, "document"

    if at == "text":
        if re.search(r"lead\s*time", label, re.I):
            where = f" to {loc}" if loc else " to the buyer's plant"
            q = f"What is your typical {label.lower()}{where}?"
        elif re.search(r"capacity|utilisation|utilization", label, re.I):
            q = f"Please state your {label.lower()} (figures and units)."
        else:
            q = f"Please describe your {label}."
        return q, "text"

    # yes_no default
    if re.search(r"\b(can|do|have|support|able|meet)\b", label, re.I):
        q = f"{label.rstrip('?')}?"
    else:
        q = f"Can you confirm {label}{plant_bit}?"
    if not q.endswith("?"):
        q = q.rstrip(".") + "?"
    return q, "yes_no"


def _tailor_template(template: str, gate_id: str, ctx: dict) -> str:
    """Lightly tailor a known template using brief location/category."""
    q = template
    loc = ctx.get("location")
    if gate_id == "delivery_plant" and loc:
        q = (
            f"Can you support weekly delivered shipments to {loc} "
            f"on the stated schedule?"
        )
    elif gate_id == "lead_time" and loc:
        q = f"Typical lead time from approved artwork to first delivery at {loc}?"
    elif gate_id == "food_contact" and ctx.get("category"):
        q = (
            "Do cartons intended for snack foods meet food-contact / hygiene "
            "requirements where applicable?"
        )
    elif gate_id == "iso_9001" and loc:
        q = (
            f"Is your manufacturing site (supplying {loc}) ISO 9001 certified? "
            f"Attach certificate."
        )
    return q


def generate_question_for_check(gate: dict, brief: str | None = None) -> tuple[str, str]:
    """Return (question_text, answer_type) for a check — never buyer-authored.

    Known default ids → solid templates, lightly tailored from the brief.
    Custom short labels → sensible yes_no / document / text from the label.
    """
    ctx = brief_context(brief)
    gid = str(gate.get("id") or "")
    label = str(gate.get("label") or gid).strip() or gid

    known = _DEFAULT_BY_ID.get(gid)
    if known:
        at = known.get("answer_type") or "yes_no"
        q = _tailor_template(known.get("question") or known.get("label") or label, gid, ctx)
        return q, at if at in ("yes_no", "text", "document") else "yes_no"

    return _question_from_custom_label(label, ctx)


def enrich_gates(gates: list[dict] | None, brief: str | None = None) -> list[dict]:
    """Fill question + answer_type on every gate from system generation.

    Ignores any buyer-posted question / answer_type so the home form cannot
    author questionnaire wording.
    """
    gates = deepcopy(gates) if gates is not None else default_gates()
    out = []
    for g in gates:
        g = dict(g)
        q, at = generate_question_for_check(g, brief)
        g["question"] = q
        g["answer_type"] = at
        g["label"] = str(g.get("label") or g.get("id") or "").strip() or str(g.get("id"))
        g["knockout"] = bool(g.get("knockout"))
        g["enabled"] = bool(g.get("enabled", True))
        out.append(g)
    return out


def parse_gates_from_form(form) -> list[dict]:
    """Parse multi-value gate_* fields from a Starlette/FastAPI form.

    Expected fields (repeated rows): gate_id, gate_label, gate_knockout, gate_enabled.
    Buyer-authored gate_question / gate_answer_type are ignored if present.
    Or JSON blob gate_json. Falls back to defaults when empty.
    """
    raw_json = None
    try:
        raw_json = form.get("gate_json")
    except Exception:
        raw_json = None
    if raw_json:
        import json

        try:
            data = json.loads(raw_json)
            if isinstance(data, list) and data:
                return _normalize_gate_list(data)
        except Exception:
            pass

    ids = form.getlist("gate_id") if hasattr(form, "getlist") else []
    if not ids:
        return default_gates()

    labels = form.getlist("gate_label") if hasattr(form, "getlist") else []
    knockouts = set(form.getlist("gate_knockout")) if hasattr(form, "getlist") else set()
    enableds = set(form.getlist("gate_enabled")) if hasattr(form, "getlist") else set()

    out = []
    for i, gid in enumerate(ids):
        gid = (gid or "").strip() or f"custom_{i+1}"
        label = (labels[i] if i < len(labels) else gid).strip() or gid
        knockout = gid in knockouts or str(i) in knockouts
        enabled = gid in enableds or str(i) in enableds
        if not enableds:
            enabled = True
        # Deliberately ignore gate_question / gate_answer_type from the form.
        out.append(
            {
                "id": gid,
                "label": label,
                "knockout": bool(knockout),
                "enabled": bool(enabled),
            }
        )
    return out or default_gates()


def parse_gates_from_lists(
    ids: list[str],
    labels: list[str] | None = None,
    questions: list[str] | None = None,  # accepted for back-compat; ignored
    answer_types: list[str] | None = None,  # accepted for back-compat; ignored
    knockout_ids: list[str] | None = None,
    enabled_ids: list[str] | None = None,
) -> list[dict]:
    """Parse parallel form lists (FastAPI Form lists). Questions/types ignored."""
    labels = labels or []
    knockout_ids = set(knockout_ids or [])
    enabled_ids = set(enabled_ids or [])
    if not ids:
        return default_gates()
    out = []
    for i, gid in enumerate(ids):
        gid = (gid or "").strip() or f"custom_{i+1}"
        label = (labels[i] if i < len(labels) else "").strip() or gid
        enabled = (gid in enabled_ids) if enabled_ids else True
        out.append(
            {
                "id": gid,
                "label": label,
                "knockout": gid in knockout_ids,
                "enabled": enabled,
            }
        )
    return out


def _normalize_gate_list(data: list) -> list[dict]:
    out = []
    for i, g in enumerate(data):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or f"custom_{i+1}")
        label = str(g.get("label") or gid)
        # Keep optional pre-set question only when it came from our defaults path;
        # enrich_gates will overwrite from templates / generation anyway.
        out.append(
            {
                "id": gid,
                "label": label,
                "knockout": bool(g.get("knockout")),
                "enabled": bool(g.get("enabled", True)),
            }
        )
    return out or default_gates()


def questionnaire_from_gates(
    gates: list[dict] | None, brief: str | None = None
) -> list[dict]:
    """Deterministic check → questionnaire mapping (enabled checks only).

    Always regenerates wording from check id/label + brief so the model (or
    a stale gate.question) cannot invent a mismatched list.
    """
    gates = enrich_gates(gates, brief)
    qs = []
    n = 0
    for g in gates:
        if not g.get("enabled", True):
            continue
        n += 1
        qs.append(
            {
                "q_id": f"Q{n}",
                "text": g.get("question") or g.get("label") or g.get("id"),
                "answer_type": g.get("answer_type") or "yes_no",
                "knockout": bool(g.get("knockout")),
                "gate_id": g.get("id"),
            }
        )
    return qs


def apply_gates_to_rfx(
    rfx: dict, gates: list[dict] | None, brief: str | None = None
) -> dict:
    """Replace questionnaire from checks; store enriched quality_gates on the RFx."""
    brief = brief if brief is not None else (rfx.get("brief") if isinstance(rfx, dict) else None)
    gates = enrich_gates(gates, brief)
    rfx = dict(rfx)
    rfx["questionnaire"] = questionnaire_from_gates(gates, brief)
    rfx["quality_gates"] = gates
    return rfx


def gates_prompt_block(gates: list[dict] | None) -> str:
    """Text block for the drafter: checks are selected; questionnaire is forced later."""
    gates = [g for g in (gates or default_gates()) if g.get("enabled", True)]
    if not gates:
        return (
            "Quality checks: none selected. Still produce a short placeholder questionnaire "
            "(the system will replace it)."
        )
    lines = [
        "The buyer selected these quality checks. Draft scope, 30 lines, and terms as usual. "
        "Include a minimal questionnaire stub only — the system will REPLACE the questionnaire "
        "with exactly one question per selected check (knockout flags preserved). "
        "Do NOT invent extra or different quality questions:",
    ]
    for g in gates:
        ko = "KNOCKOUT" if g.get("knockout") else "preferred/non-knockout"
        lines.append(f"- [{ko}] id={g.get('id')} label={g.get('label')}")
    return "\n".join(lines)
