"""
Meta delivering the same message twice, and the agent answering it twice.

This is taken from production, not imagined. The same provider message id was
stored twice in the same second and answered twice, and the person on WhatsApp
got two slightly different replies to one question:

    15:26:23  INBOUND  button  pid=…FCQzkzQzQ0ODMA  'Reply For More'
    15:26:23  INBOUND  button  pid=…FCQzkzQzQ0ODMA  'Reply For More'
    15:27:13  OUTBOUND text    'How can I help you further? …'
    15:27:13  OUTBOUND text    'How can I help you? …'

Answering in the background made us quick to acknowledge, which lowers the
chance of a retry but cannot rule one out. These pin that a repeat delivery is
now ignored however it arrives — concurrently, or minutes later.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.api.routes import webhooks


def _payload(message_id: str, *, body: str = "Reply For More", kind: str = "button",
             phone_number_id: str = "meta-1") -> dict:
    inner: dict = {"id": message_id, "from": "923191611020", "type": kind,
                   "timestamp": "1760000000"}
    if kind == "text":
        inner["text"] = {"body": body}
    else:
        inner["button"] = {"text": body}
    value = {
        "metadata": {"phone_number_id": phone_number_id},
        "messages": [inner],
    }
    change = {"field": "messages", "value": value}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [change]}],
    }


@pytest.fixture(autouse=True)
def _forget_what_was_seen():
    """The claim table is module-level, so one test's ids would otherwise
    make the next test's identical id look like a duplicate."""
    webhooks._seen_inbound.clear()
    yield
    webhooks._seen_inbound.clear()


@pytest.fixture
def whatsapp(client, tenant_factory, auth, monkeypatch, fake_db):
    """A company with a WhatsApp number that auto-replies, and a record of
    every reply the agent tried to send."""
    import asyncio as _asyncio

    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.repositories import numbers as number_repo

    tenant, key = tenant_factory("111", "Acme")
    number = PhoneNumber(
        tenantId="111", kind=NumberKind.WHATSAPP, label="WA",
        phoneNumber="+923010808222", status=NumberStatus.VERIFIED,
        metaPhoneNumberId="meta-1", wabaId="waba-1",
        accessToken="t", appSecret="s", autoReply=True,
    )
    _asyncio.run(number_repo.save(number))

    replies: list[str] = []

    async def fake_compose(tenant, body, context, history):
        replies.append(body)
        return f"answer to {body}"

    async def fake_send_text(self, to, text):
        from app.models.call import Message, MessageDirection
        return Message(tenantId="111", direction=MessageDirection.OUTBOUND,
                       counterparty=to, body=text, providerMessageId="out-1")

    from app.services import whatsapp as whatsapp_service
    from app.services.agent import reply as reply_service

    monkeypatch.setattr(reply_service, "compose", fake_compose)
    monkeypatch.setattr(whatsapp_service.WhatsApp, "send_text", fake_send_text)
    # The signature check is exercised in its own tests; here it is in the way.
    monkeypatch.setattr(webhooks, "signature_ok", lambda *a, **k: True)

    return {"key": key, "auth": auth(key), "replies": replies}


def _deliver(client, payload: dict):
    return client.post("/api/webhooks/whatsapp", json=payload,
                       headers={"X-Hub-Signature-256": "sha256=ignored"})


def _messages(client, headers) -> list[dict]:
    return client.get("/api/messages", headers=headers).json()["messages"]


# ---------------------------------------------------------------------------


def test_the_same_message_delivered_twice_is_stored_once(client, whatsapp):
    payload = _payload("wamid.DUPLICATE")
    assert _deliver(client, payload).status_code == 200
    assert _deliver(client, payload).status_code == 200

    inbound = [m for m in _messages(client, whatsapp["auth"])
               if m["direction"] == "INBOUND"]
    assert len(inbound) == 1, inbound


def test_the_same_message_delivered_twice_is_answered_once(client, whatsapp):
    """The bug as the person on WhatsApp experienced it."""
    payload = _payload("wamid.DUPLICATE")
    _deliver(client, payload)
    _deliver(client, payload)

    assert whatsapp["replies"] == ["Reply For More"]


def test_two_deliveries_arriving_together_still_answer_once(client, whatsapp):
    """Both copies landed in the same second in production, so they raced.
    A database check alone would have both miss, each reading before the
    other wrote — which is why the claim is made in memory first."""
    payload = _payload("wamid.RACE")

    async def both():
        await asyncio.gather(
            asyncio.to_thread(_deliver, client, payload),
            asyncio.to_thread(_deliver, client, payload),
        )

    asyncio.run(both())
    assert len(whatsapp["replies"]) == 1, whatsapp["replies"]


def test_a_redelivery_after_a_restart_is_still_ignored(client, whatsapp):
    """This process has no memory of the first delivery, so the stored
    message is what catches it."""
    payload = _payload("wamid.AFTER-RESTART")
    _deliver(client, payload)
    webhooks._seen_inbound.clear()  # as if the service had restarted
    _deliver(client, payload)

    assert len(whatsapp["replies"]) == 1
    inbound = [m for m in _messages(client, whatsapp["auth"])
               if m["direction"] == "INBOUND"]
    assert len(inbound) == 1


def test_two_genuinely_different_messages_are_both_answered(client, whatsapp):
    """The fix must not swallow a second question. Somebody asking two
    things in a row deserves two answers."""
    _deliver(client, _payload("wamid.ONE", body="What are the fees?", kind="text"))
    _deliver(client, _payload("wamid.TWO", body="And the deadline?", kind="text"))

    assert whatsapp["replies"] == ["What are the fees?", "And the deadline?"]


def test_the_same_words_with_a_different_id_are_answered_twice(client, whatsapp):
    """Deduplication is on the provider's id, not on the text. Asking the
    same question twice is a thing people do, and it deserves an answer."""
    _deliver(client, _payload("wamid.FIRST", body="hi", kind="text"))
    _deliver(client, _payload("wamid.SECOND", body="hi", kind="text"))

    assert whatsapp["replies"] == ["hi", "hi"]


def test_a_message_with_no_id_is_not_dropped(client, whatsapp):
    """A payload shape we have not seen must not be silently discarded: an
    empty id would otherwise claim the slot for every other empty id."""
    payload = _payload("", body="no id here", kind="text")
    _deliver(client, payload)
    assert whatsapp["replies"] == ["no id here"]


def test_the_claim_table_does_not_grow_without_limit(client, whatsapp):
    """It is a module-level dict on a long-running process."""
    for index in range(2100):
        webhooks._claim_inbound(f"id-{index}")
    assert len(webhooks._seen_inbound) <= 2200


def test_a_claim_is_only_made_once():
    assert webhooks._claim_inbound("only-once") is True
    assert webhooks._claim_inbound("only-once") is False
