"""Draft-time quality gates → questionnaire mapping.

Buyers pick gates on the home page before drafting. Each enabled gate becomes
a questionnaire item (knockout gates → knockout questions). Cached EXAMPLE_BRIEF
drafts keep canned lines/terms and rebuild the questionnaire from the chosen
gates so customization stays instant.
"""
from __future__ import annotations

from copy import deepcopy

# Sensible defaults for corrugated India / snacks-plant procurement.
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
    # Custom-added gates (ids not in defaults) break the match
    for g in gates:
        if g.get("id") not in defaults and g.get("enabled", True):
            return False
    return True


def parse_gates_from_form(form) -> list[dict]:
    """Parse multi-value gate_* fields from a Starlette/FastAPI Form/UploadFile form.

    Expected fields (repeated rows):
      gate_id, gate_label, gate_question, gate_answer_type, gate_knockout (on/off), gate_enabled (on/off)
    Or JSON blob gate_json. Falls back to defaults when empty.
    """
    # Prefer structured JSON if present
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

    labels = form.getlist("gate_label")
    questions = form.getlist("gate_question")
    answer_types = form.getlist("gate_answer_type")
    knockouts = set(form.getlist("gate_knockout"))
    enableds = set(form.getlist("gate_enabled"))

    out = []
    for i, gid in enumerate(ids):
        gid = (gid or "").strip() or f"custom_{i+1}"
        label = (labels[i] if i < len(labels) else gid).strip() or gid
        question = (questions[i] if i < len(questions) else label).strip() or label
        at = (answer_types[i] if i < len(answer_types) else "yes_no").strip() or "yes_no"
        # Checkbox convention: value is the gate id when checked
        knockout = gid in knockouts or str(i) in knockouts or (knockouts == {"on"} and False)
        enabled = gid in enableds or str(i) in enableds
        # If enabled list empty but we got ids, treat missing enabled as off when checkboxes used
        if not enableds:
            # hidden field gate_enabled_flag or assume all listed are enabled
            enabled = True
        out.append(
            {
                "id": gid,
                "label": label,
                "question": question,
                "answer_type": at if at in ("yes_no", "text", "document") else "yes_no",
                "knockout": bool(knockout),
                "enabled": bool(enabled),
            }
        )
    return out or default_gates()


def parse_gates_from_lists(
    ids: list[str],
    labels: list[str] | None = None,
    questions: list[str] | None = None,
    answer_types: list[str] | None = None,
    knockout_ids: list[str] | None = None,
    enabled_ids: list[str] | None = None,
) -> list[dict]:
    """Parse parallel form lists (FastAPI Form lists)."""
    labels = labels or []
    questions = questions or []
    answer_types = answer_types or []
    knockout_ids = set(knockout_ids or [])
    enabled_ids = set(enabled_ids or [])
    if not ids:
        return default_gates()
    out = []
    for i, gid in enumerate(ids):
        gid = (gid or "").strip() or f"custom_{i+1}"
        label = (labels[i] if i < len(labels) else "").strip() or gid
        question = (questions[i] if i < len(questions) else "").strip() or label
        at = (answer_types[i] if i < len(answer_types) else "yes_no").strip() or "yes_no"
        # When enabled_ids provided, only those are on; when empty, all submitted rows are enabled
        enabled = (gid in enabled_ids) if enabled_ids else True
        out.append(
            {
                "id": gid,
                "label": label,
                "question": question,
                "answer_type": at if at in ("yes_no", "text", "document") else "yes_no",
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
        question = str(g.get("question") or label)
        at = str(g.get("answer_type") or "yes_no")
        out.append(
            {
                "id": gid,
                "label": label,
                "question": question,
                "answer_type": at if at in ("yes_no", "text", "document") else "yes_no",
                "knockout": bool(g.get("knockout")),
                "enabled": bool(g.get("enabled", True)),
            }
        )
    return out or default_gates()


def questionnaire_from_gates(gates: list[dict] | None) -> list[dict]:
    """Deterministic gate → questionnaire mapping (enabled gates only)."""
    gates = gates if gates is not None else default_gates()
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


def apply_gates_to_rfx(rfx: dict, gates: list[dict] | None) -> dict:
    """Replace questionnaire from gates; store quality_gates on the RFx."""
    gates = gates if gates is not None else default_gates()
    rfx = dict(rfx)
    rfx["questionnaire"] = questionnaire_from_gates(gates)
    rfx["quality_gates"] = deepcopy(gates)
    return rfx


def gates_prompt_block(gates: list[dict] | None) -> str:
    """Text block for the drafter SYSTEM/user content so the model respects gates."""
    gates = [g for g in (gates or default_gates()) if g.get("enabled", True)]
    if not gates:
        return "Questionnaire: invent 8-12 sensible quality questions; mark 3-4 as knockout."
    lines = [
        "Build the questionnaire STRICTLY from these buyer-selected quality gates "
        "(do not ignore or drop them; you may add at most 2 extra non-knockout clarifying questions):"
    ]
    for g in gates:
        ko = "KNOCKOUT" if g.get("knockout") else "preferred/non-knockout"
        lines.append(f"- [{ko}] {g.get('label')}: {g.get('question')} (answer_type={g.get('answer_type', 'yes_no')})")
    return "\n".join(lines)
