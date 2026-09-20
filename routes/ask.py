"""Natural-language analyst chat with snapshot-consistent answers."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from core import analyst, demo_ops, llm, snapshots, storage
from core.web import error_fragment, hx_redirect, load_or_404, now_iso, render

router = APIRouter()

SUGGESTED = [
    "What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?",
    "Who is cheapest overall on a like-for-like basis, and what is excluded from that comparison?",
    "Which vendor quoted in USD and what exchange rate did you use? How much does a 3% weaker rupee change the ranking?",
    "Which lines have no usable quote from anyone, and what would I need to clarify to fix that?",
    "If we want at most two suppliers, which two and what does it cost versus the full split?",
    "Give me an award recommendation I can defend to the VP, with the conditions under which it changes.",
]


@router.get("/rfx/{rfx_id}/ask", response_class=HTMLResponse)
def ask_page(request: Request, rfx_id: str):
    state = load_or_404(rfx_id)
    ready = any(v.get("extraction") for v in state["vendors"])
    prompts = demo_ops.DEMO_PROMPTS if demo_ops.is_demo_mode(state) else SUGGESTED
    return render(request, "ask.html", state=state, active="ask", suggested=prompts, ready=ready, demo_script_mode=demo_ops.is_demo_mode(state))


@router.post("/rfx/{rfx_id}/ask", response_class=HTMLResponse)
def ask(request: Request, rfx_id: str, question: str = Form(...)):
    state = load_or_404(rfx_id)
    if not any(v.get("extraction") for v in state["vendors"]):
        return error_fragment("Extract at least one vendor response before asking questions.", 400)
    q = question.strip()
    if not q:
        return error_fragment("Type a question.", 400)
    try:
        result = analyst.ask(state, q, state.get("chat", []), log=state.setdefault("ai_log", []))
    except llm.AINotConfigured as e:
        return error_fragment(str(e), 400)
    except RuntimeError as e:
        # Snapshot race — surface clearly
        return error_fragment(f"Data changed while answering; please ask again. ({e})", 409)
    except Exception as e:
        return error_fragment(f"The analyst failed: {e}")
    result["at"] = result.get("at") or now_iso()
    state.setdefault("chat", []).append(result)
    storage.save_state(rfx_id, state)
    return render(request, "partials/chat_message.html", state=state, m=result, idx=len(state["chat"]) - 1)


@router.post("/rfx/{rfx_id}/ask/rerun/{idx}", response_class=HTMLResponse)
def rerun_ask(request: Request, rfx_id: str, idx: int):
    """Rerun the same question with the latest vendor-data version; keep the old answer as historical."""
    state = load_or_404(rfx_id)
    chat = state.get("chat", [])
    if idx < 0 or idx >= len(chat):
        raise HTTPException(404)
    old = chat[idx]
    q = (old.get("question") or "").strip()
    if not q:
        return error_fragment("Cannot rerun: original question is empty.", 400)
    if not any(v.get("extraction") for v in state["vendors"]):
        return error_fragment("Extract at least one vendor response before asking questions.", 400)
    try:
        result = analyst.ask(state, q, state.get("chat", []), log=state.setdefault("ai_log", []))
    except llm.AINotConfigured as e:
        return error_fragment(str(e), 400)
    except RuntimeError as e:
        return error_fragment(f"Data changed while answering; please ask again. ({e})", 409)
    except Exception as e:
        return error_fragment(f"The analyst failed: {e}")
    result["at"] = result.get("at") or now_iso()
    result["rerun_of"] = old.get("id")
    state.setdefault("chat", []).append(result)
    storage.save_state(rfx_id, state)
    return render(request, "partials/chat_message.html", state=state, m=result, idx=len(state["chat"]) - 1)


@router.post("/rfx/{rfx_id}/recommendation/{idx}", response_class=HTMLResponse)
def save_recommendation(request: Request, rfx_id: str, idx: int):
    state = load_or_404(rfx_id)
    chat = state.get("chat", [])
    if idx < 0 or idx >= len(chat):
        raise HTTPException(404)
    m = chat[idx]
    ok, msg = snapshots.can_save_recommendation(state, m)
    if not ok:
        return error_fragment(msg, 400)
    try:
        snapshots.save_recommendation_from_answer(state, m, idx)
    except ValueError as e:
        return error_fragment(str(e), 400)
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/award")


@router.post("/rfx/{rfx_id}/chat/clear", response_class=HTMLResponse)
def clear_chat(request: Request, rfx_id: str):
    state = load_or_404(rfx_id)
    state["chat"] = []
    storage.save_state(rfx_id, state)
    return hx_redirect(f"/rfx/{rfx_id}/ask")
