"""
What an agent is allowed to be used for, and who answers a WhatsApp message.

"Inbound / outbound / both" was the whole question while a call was the only
thing an agent did. Replying to WhatsApp messages is a third job and not a
direction, so it does not fit on that axis — these pin the two staying in
step, and that an agent cannot be dropped into a slot it was never set up for.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.agent import (
    Agent, AgentChannel, AgentMode, channels_from_mode, mode_from_channels,
)

ADMIN = {"X-Admin-Key": "test-admin-key"}


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def test_an_agent_stored_before_channels_existed_keeps_what_it_could_do():
    """Nothing in the database has `channels`. `mode` is the only record of
    what those agents were for, and it has to keep working."""
    assert channels_from_mode(AgentMode.INBOUND) == [AgentChannel.INBOUND_CALL]
    assert channels_from_mode(AgentMode.OUTBOUND) == [AgentChannel.OUTBOUND_CALL]
    assert channels_from_mode(AgentMode.BOTH) == [
        AgentChannel.INBOUND_CALL, AgentChannel.OUTBOUND_CALL,
    ]

    old = Agent(name="Legacy", mode=AgentMode.OUTBOUND)
    assert old.channels == [AgentChannel.OUTBOUND_CALL]
    assert old.places_calls and not old.answers_calls


def test_an_upgrade_does_not_switch_whatsapp_on_for_everyone():
    """Nobody chose it for these agents. Turning it on during an upgrade would
    be this release answering a company's messages uninvited."""
    for mode in AgentMode:
        assert AgentChannel.WHATSAPP_MESSAGE not in channels_from_mode(mode)


def test_channels_decide_the_mode_rather_than_the_other_way_round():
    agent = Agent(name="All", channels=[
        AgentChannel.WHATSAPP_MESSAGE, AgentChannel.OUTBOUND_CALL, AgentChannel.INBOUND_CALL,
    ])
    assert agent.mode is AgentMode.BOTH
    # Stable order, whatever order they arrived in.
    assert agent.channels == [
        AgentChannel.INBOUND_CALL, AgentChannel.OUTBOUND_CALL, AgentChannel.WHATSAPP_MESSAGE,
    ]


def test_a_messages_only_agent_takes_no_calls():
    agent = Agent(name="Writer", channels=[AgentChannel.WHATSAPP_MESSAGE])
    assert agent.handles_messages
    assert not agent.answers_calls and not agent.places_calls
    # It still reports a legal mode, because the column has to hold something.
    assert agent.mode is AgentMode.INBOUND


def test_a_duplicate_channel_is_not_stored_twice():
    agent = Agent(name="D", channels=[AgentChannel.INBOUND_CALL, AgentChannel.INBOUND_CALL])
    assert agent.channels == [AgentChannel.INBOUND_CALL]


def test_mode_from_channels_covers_every_combination():
    assert mode_from_channels([]) is AgentMode.INBOUND
    assert mode_from_channels([AgentChannel.OUTBOUND_CALL]) is AgentMode.OUTBOUND
    assert mode_from_channels(
        [AgentChannel.INBOUND_CALL, AgentChannel.OUTBOUND_CALL]) is AgentMode.BOTH


def test_the_public_shape_answers_the_question_each_screen_asks():
    agent = Agent(name="A", channels=[AgentChannel.WHATSAPP_MESSAGE])
    body = agent.public()
    assert body["handlesMessages"] is True
    assert body["answersCalls"] is False
    assert body["placesCalls"] is False
    assert body["channels"] == ["whatsapp_message"]


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


def test_an_agent_can_be_created_for_messages_only(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    made = client.post("/api/agents", headers=auth(key), json={
        "name": "Writer", "channels": ["whatsapp_message"],
    })
    assert made.status_code in (200, 201), made.text
    assert made.json()["handlesMessages"] is True
    assert made.json()["answersCalls"] is False


def test_an_older_client_that_only_sends_mode_still_works(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    made = client.post("/api/agents", headers=auth(key), json={
        "name": "Caller", "mode": "outbound",
    })
    assert made.status_code in (200, 201), made.text
    assert made.json()["channels"] == ["outbound_call"]


def test_channels_can_be_changed_afterwards(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    agent_id = client.post("/api/agents", headers=auth(key),
                           json={"name": "A", "channels": ["inbound_call"]}).json()["id"]
    changed = client.patch(f"/api/agents/{agent_id}", headers=auth(key), json={
        "channels": ["inbound_call", "whatsapp_message"],
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["handlesMessages"] is True
    assert changed.json()["mode"] == "inbound"


# ---------------------------------------------------------------------------
# Slots on a number
# ---------------------------------------------------------------------------


@pytest.fixture
def whatsapp_number(client, auth):
    """A verified WhatsApp number, with verification stubbed out."""
    def make(key: str, tenant_id: str = "111"):
        import asyncio as _asyncio

        from app.models.number import NumberKind, NumberStatus, PhoneNumber
        from app.repositories import numbers as number_repo

        number = PhoneNumber(
            tenantId=tenant_id, kind=NumberKind.WHATSAPP, label="WA",
            phoneNumber="+923001112222", status=NumberStatus.VERIFIED,
            metaPhoneNumberId="meta-1", wabaId="waba-1",
            accessToken="t", appSecret="s",
        )
        _asyncio.run(number_repo.save(number))
        return number
    return make


def test_a_messages_agent_cannot_be_put_in_the_inbound_call_slot(
    client, tenant_factory, auth, whatsapp_number,
):
    _, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    agent_id = client.post("/api/agents", headers=auth(key),
                           json={"name": "Writer", "channels": ["whatsapp_message"]}).json()["id"]

    refused = client.patch(f"/api/numbers/{number.id}", headers=auth(key),
                           json={"inboundAgentId": agent_id})
    assert refused.status_code == 422
    assert "not set up to answer calls" in refused.json()["error"]["message"]


def test_a_call_agent_cannot_be_put_in_the_message_slot(
    client, tenant_factory, auth, whatsapp_number,
):
    _, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    agent_id = client.post("/api/agents", headers=auth(key),
                           json={"name": "Caller", "channels": ["inbound_call"]}).json()["id"]

    refused = client.patch(f"/api/numbers/{number.id}", headers=auth(key),
                           json={"messageAgentId": agent_id})
    assert refused.status_code == 422
    assert "reply to WhatsApp messages" in refused.json()["error"]["message"]


def test_the_message_slot_accepts_an_agent_that_handles_messages(
    client, tenant_factory, auth, whatsapp_number,
):
    _, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    agent_id = client.post("/api/agents", headers=auth(key), json={
        "name": "Writer", "channels": ["inbound_call", "whatsapp_message"],
    }).json()["id"]

    saved = client.patch(f"/api/numbers/{number.id}", headers=auth(key),
                         json={"messageAgentId": agent_id, "autoReply": True})
    assert saved.status_code == 200, saved.text
    assert saved.json()["messageAgentId"] == agent_id
    assert saved.json()["autoReply"] is True


def test_a_phone_line_cannot_be_given_a_message_agent(client, tenant_factory, auth):
    """There are no WhatsApp messages on a SIM line, so the slot is refused
    rather than silently kept and never used."""
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.repositories import numbers as number_repo

    _, key = tenant_factory("111", "Acme")
    line = PhoneNumber(
        tenantId="111", kind=NumberKind.SIM, label="Line", phoneNumber="+923009998888",
        status=NumberStatus.VERIFIED, infobipApiKey="k", infobipBaseUrl="x.api.infobip.com",
    )
    asyncio.run(number_repo.save(line))
    agent_id = client.post("/api/agents", headers=auth(key),
                           json={"name": "W", "channels": ["whatsapp_message"]}).json()["id"]

    refused = client.patch(f"/api/numbers/{line.id}", headers=auth(key),
                           json={"messageAgentId": agent_id})
    assert refused.status_code == 422
    assert "Only a WhatsApp number" in refused.json()["error"]["message"]


def test_deleting_an_agent_empties_the_message_slot_too(
    client, tenant_factory, auth, whatsapp_number,
):
    """Otherwise the number points at an agent that is gone, and every reply
    falls back to the company's whole pile without saying why."""
    _, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    agent_id = client.post("/api/agents", headers=auth(key), json={
        "name": "Writer", "channels": ["whatsapp_message"],
    }).json()["id"]
    client.patch(f"/api/numbers/{number.id}", headers=auth(key),
                 json={"messageAgentId": agent_id})

    assert client.delete(f"/api/agents/{agent_id}", headers=auth(key)).status_code in (200, 204)
    after = client.get(f"/api/numbers/{number.id}", headers=auth(key)).json()
    assert after["messageAgentId"] is None


# ---------------------------------------------------------------------------
# Which agent actually answers a message
# ---------------------------------------------------------------------------


def test_a_message_is_answered_by_the_message_agent(
    client, tenant_factory, auth, whatsapp_number, fake_db,
):
    from app.models.agent import Direction
    from app.models.call import Channel
    from app.repositories import numbers as number_repo
    from app.repositories import tenants as tenant_repo
    from app.services import routing

    tenant, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    phone = client.post("/api/agents", headers=auth(key),
                        json={"name": "Phone", "channels": ["inbound_call"]}).json()["id"]
    writer = client.post("/api/agents", headers=auth(key),
                         json={"name": "Writer", "channels": ["whatsapp_message"]}).json()["id"]
    client.patch(f"/api/numbers/{number.id}", headers=auth(key),
                 json={"inboundAgentId": phone, "messageAgentId": writer})

    stored = asyncio.run(number_repo.get("111", number.id))
    speaking_as = number_repo.as_tenant(asyncio.run(tenant_repo.require("111")), stored)
    resolved = asyncio.run(routing.resolve(
        speaking_as, channel=Channel.WHATSAPP_MESSAGE, direction=Direction.INBOUND))
    assert resolved.agent is not None and resolved.agent.name == "Writer"


def test_without_one_the_phone_agent_answers_the_message(
    client, tenant_factory, auth, whatsapp_number, fake_db,
):
    """What every number did before the slot existed, and a sensible default:
    the one that answers the phone is a fair guess for the one that writes."""
    from app.models.agent import Direction
    from app.models.call import Channel
    from app.repositories import numbers as number_repo
    from app.repositories import tenants as tenant_repo
    from app.services import routing

    _, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    phone = client.post("/api/agents", headers=auth(key), json={
        "name": "Phone", "channels": ["inbound_call"],
    }).json()["id"]
    client.patch(f"/api/numbers/{number.id}", headers=auth(key),
                 json={"inboundAgentId": phone})

    stored = asyncio.run(number_repo.get("111", number.id))
    speaking_as = number_repo.as_tenant(asyncio.run(tenant_repo.require("111")), stored)
    resolved = asyncio.run(routing.resolve(
        speaking_as, channel=Channel.WHATSAPP_MESSAGE, direction=Direction.INBOUND))
    assert resolved.agent is not None and resolved.agent.name == "Phone"


def test_the_numbers_own_knowledge_base_wins_for_messages(
    client, tenant_factory, auth, whatsapp_number, fake_db,
):
    from app.models.agent import Direction
    from app.models.call import Channel
    from app.repositories import numbers as number_repo
    from app.repositories import tenants as tenant_repo
    from app.services import routing

    _, key = tenant_factory("111", "Acme")
    number = whatsapp_number(key)
    base = client.post("/api/knowledge-bases", headers=auth(key),
                       json={"name": "Written answers"}).json()["id"]
    writer = client.post("/api/agents", headers=auth(key), json={
        "name": "Writer", "channels": ["whatsapp_message"],
    }).json()["id"]
    client.patch(f"/api/numbers/{number.id}", headers=auth(key), json={
        "messageAgentId": writer, "messageKnowledgeBaseId": base,
    })

    stored = asyncio.run(number_repo.get("111", number.id))
    speaking_as = number_repo.as_tenant(asyncio.run(tenant_repo.require("111")), stored)
    resolved = asyncio.run(routing.resolve(
        speaking_as, channel=Channel.WHATSAPP_MESSAGE, direction=Direction.INBOUND))
    assert resolved.knowledge_base_id == base
    assert resolved.resolved_by == "number"
