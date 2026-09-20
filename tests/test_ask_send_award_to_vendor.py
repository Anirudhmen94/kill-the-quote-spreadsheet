"""Ask → Send award to vendor preloads Award; send notifies manager."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import app
from core import award_draft, demo_ops, storage

client = TestClient(app)


def _seed():
    st = demo_ops.build_golden_seed()
    storage.save_state(st["id"], st)
    return st


def _tick_all(st: dict) -> dict:
    award_draft.ensure_award_draft(st)
    award_draft.update_acknowledgements(st, {c["id"]: True for c in award_draft.ACKNOWLEDGEMENT_ITEMS})
    storage.save_state(st["id"], st)
    return storage.load_state(st["id"])


def test_no_save_as_award_recommendation_copy_on_compare_ask():
    st = _seed()
    r = client.post(f"/rfx/{st['id']}/ask/premade", data={"prompt_id": "like_for_like"})
    assert r.status_code == 200
    assert "Save as award recommendation" not in r.text
    assert "Use this recommendation" not in r.text
    assert "Send award to" in r.text
    assert 'data-testid="compare-ask-send-award"' in r.text


def test_compare_ask_send_to_vendor_preloads_award():
    st = _seed()
    r0 = client.post(f"/rfx/{st['id']}/ask/premade", data={"prompt_id": "like_for_like"})
    assert r0.status_code == 200
    st1 = storage.load_state(st["id"])
    idx = len(st1["chat"]) - 1
    msg = st1["chat"][idx]
    actions = msg.get("apply_actions") or []
    assert actions, "like_for_like should expose Pass vendor send actions"
    action = actions[0]

    r = client.post(
        f"/rfx/{st['id']}/award/send-to-vendor",
        data={
            "vendor_id": action["vendor_id"],
            "line_nos": ",".join(str(n) for n in (action.get("line_nos") or [])),
            "chat_idx": str(idx),
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert "/award" in loc
    assert "send-award" in loc

    st2 = storage.load_state(st["id"])
    banner = st2.get("award_from_ask") or {}
    assert banner.get("vendor_id") == action["vendor_id"]
    assert banner.get("reason")
    alloc = (st2.get("award_draft") or {}).get("allocation") or {}
    assert any(vid == action["vendor_id"] for vid in alloc.values())

    page = client.get(f"/rfx/{st['id']}/award")
    assert page.status_code == 200
    assert 'data-testid="award-from-ask-banner"' in page.text
    assert action["vendor_name"].split()[0] in page.text
    assert 'data-testid="award-from-ask-reason"' in page.text
    assert "Save as award recommendation" not in page.text


def test_award_ask_send_to_vendor_applies_and_focuses():
    st = _seed()
    r0 = client.post(f"/rfx/{st['id']}/award/ask-premade", data={"prompt_id": "who_to_drop"})
    assert r0.status_code == 200
    assert "Send award to" in r0.text
    assert 'data-testid="award-ask-send-award"' in r0.text
    assert 'data-testid="award-ask-apply-vendor"' in r0.text  # lighter action kept
    assert "Save as award recommendation" not in r0.text

    st1 = storage.load_state(st["id"])
    idx = len(st1["chat"]) - 1
    actions = st1["chat"][idx].get("apply_actions") or []
    assert actions
    # Prefer Balaji if present
    action = next((a for a in actions if "Balaji" in (a.get("vendor_name") or "")), actions[0])

    r = client.post(
        f"/rfx/{st['id']}/award/send-to-vendor",
        data={
            "vendor_id": action["vendor_id"],
            "line_nos": ",".join(str(n) for n in (action.get("line_nos") or [])),
            "chat_idx": str(idx),
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert "#send-award" in loc

    st2 = storage.load_state(st["id"])
    banner = st2.get("award_from_ask") or {}
    assert banner.get("vendor_id") == action["vendor_id"]
    assert "Balaji" in (banner.get("vendor_name") or action["vendor_name"]) or banner.get("reason")

    page = client.get(f"/rfx/{st['id']}/award#send-award")
    assert 'data-testid="award-from-ask-banner"' in page.text
    assert "(analyst pick)" in page.text or banner["vendor_name"] in page.text


def test_award_ask_send_split_preloads():
    st = _seed()
    r0 = client.post(f"/rfx/{st['id']}/award/ask-premade", data={"prompt_id": "best_split"})
    assert r0.status_code == 200
    assert 'data-testid="award-ask-send-split"' in r0.text or "Send awards from this split" in r0.text
    st1 = storage.load_state(st["id"])
    idx = len(st1["chat"]) - 1
    r = client.post(
        f"/rfx/{st['id']}/award/send-to-vendor",
        data={"apply_split": "1", "chat_idx": str(idx)},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    st2 = storage.load_state(st["id"])
    assert (st2.get("award_from_ask") or {}).get("split") is True
    assert (st2.get("award_draft") or {}).get("allocation")


def test_send_award_drafts_notifies_manager_and_confirmation():
    st = _seed()
    client.get(f"/rfx/{st['id']}/award")
    st = _tick_all(storage.load_state(st["id"]))
    before = len(st.get("outbox") or [])
    r = client.post(f"/rfx/{st['id']}/award/send-notices", follow_redirects=False)
    assert r.status_code in (302, 303)
    st2 = storage.load_state(st["id"])
    new_mail = (st2.get("outbox") or [])[before:]
    awards = [o for o in new_mail if o.get("kind") == "award_notice"]
    regrets = [o for o in new_mail if o.get("kind") in ("regret", "regret_notice")]
    manager = [o for o in new_mail if o.get("kind") == "manager_notice"]
    assert awards and regrets
    assert len(manager) == 1
    assert manager[0].get("to") == "manager@buyer.example"
    assert "manager" in (manager[0].get("body") or "").lower()
    conf = (st2.get("award_draft") or {}).get("send_confirmation") or {}
    assert conf.get("manager_notified") is True
    assert conf.get("manager_email") == "manager@buyer.example"
    flash = st2.get("flash") or {}
    assert "manager" in (flash.get("message") or "").lower()

    page = client.get(f"/rfx/{st['id']}/award")
    assert 'data-testid="award-send-confirmation"' in page.text
    assert 'data-testid="award-manager-notified"' in page.text
    assert "Manager notified" in page.text
    assert "manager@buyer.example" in page.text
    assert 'data-testid="award-confirm-close"' in page.text


def test_preload_from_analyst_unit():
    st = demo_ops.build_golden_seed()
    out = award_draft.preload_from_analyst(
        st,
        "v1",
        line_nos=[1, 2],
        reason="Balaji best price per piece on these lines.",
        question="Who is best price per piece?",
    )
    assert out["banner"]["vendor_id"] == "v1"
    assert "Balaji" in out["banner"]["reason"] or "best price" in out["banner"]["reason"]
    assert st["award_draft"]["allocation"]["1"] == "v1"
    assert st["award_draft"]["allocation"]["2"] == "v1"
