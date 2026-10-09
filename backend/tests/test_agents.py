"""
Agents, knowledge bases, and which one answers a given call.

The resolution chain is the part worth testing hardest: it decides what the
agent is allowed to say, and every step of it has a fallback, so a mistake
shows up not as an error but as the wrong answer in a real conversation.
"""
from __future__ import annotations

import pytest



async def _tenant(phone_number_id: str, **extra):
    """A company, created inside the running loop.

    `tenant_factory` calls `asyncio.run` internally, which cannot be used from
    an async test — pytest-asyncio already has a loop going.
    """
    from app.models.tenant import Tenant
    from app.repositories import tenants as tenant_repo

    tenant = Tenant(phoneNumberId=phone_number_id, name="Acme", **extra)
    await tenant_repo.save(tenant)
    tenant_repo.invalidate()
    return tenant


def _kb(client, auth, key, name="Prices", default=False):
    return client.post(
        "/api/knowledge-bases",
        json={"name": name, "purpose": "", "isDefault": default},
        headers=auth(key),
    ).json()


def _agent(client, auth, key, name="Support", kb_id=""):
    return client.post(
        "/api/agents",
        json={"name": name, "greeting": "Hello", "knowledgeBaseId": kb_id},
        headers=auth(key),
    ).json()


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------

def test_a_company_can_keep_several_knowledge_bases(client, tenant_factory, auth):
    _, key = tenant_factory("300")
    _kb(client, auth, key, "Prices")
    _kb(client, auth, key, "Support handbook")
    body = client.get("/api/knowledge-bases", headers=auth(key)).json()
    assert [k["name"] for k in body["knowledgeBases"]] == [
        "Support handbook", "Prices",
    ] or sorted(k["name"] for k in body["knowledgeBases"]) == ["Prices", "Support handbook"]


def test_only_one_knowledge_base_is_the_default(client, tenant_factory, auth):
    """Two defaults would make the fallback depend on row order."""
    _, key = tenant_factory("301")
    first = _kb(client, auth, key, "Prices", default=True)
    second = _kb(client, auth, key, "Handbook", default=True)
    body = client.get("/api/knowledge-bases", headers=auth(key)).json()
    defaults = [k["id"] for k in body["knowledgeBases"] if k["isDefault"]]
    assert defaults == [second["id"]], "the first base was left marked default too"
    assert first["id"] not in defaults


def test_a_knowledge_base_in_use_is_not_deleted_silently(client, tenant_factory, auth):
    _, key = tenant_factory("302")
    kb = _kb(client, auth, key)
    agent = _agent(client, auth, key, kb_id=kb["id"])
    client.post("/api/call-setups", json={
        "name": "Main line", "channel": "PHONE", "direction": "INBOUND",
        "agentId": agent["id"], "knowledgeBaseId": kb["id"],
    }, headers=auth(key))

    resp = client.delete(f"/api/knowledge-bases/{kb['id']}", headers=auth(key))
    assert resp.status_code == 409, "a base a live number answers from was deleted"


def test_documents_can_be_filed_under_a_knowledge_base(client, tenant_factory, auth):
    _, key = tenant_factory("303")
    kb = _kb(client, auth, key)
    client.post(
        f"/api/knowledge?name=prices.txt&knowledgeBaseId={kb['id']}",
        content=b"A consultation is 2000 rupees.",
        headers={**auth(key), "Content-Type": "text/plain"},
    )
    client.post(
        "/api/knowledge?name=general.txt",
        content=b"We are open until six.",
        headers={**auth(key), "Content-Type": "text/plain"},
    )

    scoped = client.get(
        f"/api/knowledge?knowledgeBaseId={kb['id']}", headers=auth(key)
    ).json()
    assert [d["name"] for d in scoped["documents"]] == ["prices.txt"]
    everything = client.get("/api/knowledge", headers=auth(key)).json()
    assert len(everything["documents"]) == 2


# ---------------------------------------------------------------------------
# Call setups
# ---------------------------------------------------------------------------

def test_one_agent_answers_an_incoming_number(client, tenant_factory, auth):
    """A second enabled inbound setup on a channel is refused."""
    _, key = tenant_factory("310")
    a = _agent(client, auth, key, "Support")
    b = _agent(client, auth, key, "Sales")
    first = client.post("/api/call-setups", json={
        "name": "Support line", "channel": "PHONE", "direction": "INBOUND",
        "agentId": a["id"],
    }, headers=auth(key))
    assert first.status_code == 201

    second = client.post("/api/call-setups", json={
        "name": "Sales line", "channel": "PHONE", "direction": "INBOUND",
        "agentId": b["id"],
    }, headers=auth(key))
    assert second.status_code == 409, "two agents were left answering the same number"


def test_several_outbound_setups_are_allowed(client, tenant_factory, auth):
    """Outbound is chosen deliberately per call, so several can coexist."""
    _, key = tenant_factory("311")
    a = _agent(client, auth, key)
    for name in ("Reminders", "Follow-ups", "Renewals"):
        resp = client.post("/api/call-setups", json={
            "name": name, "channel": "PHONE", "direction": "OUTBOUND",
            "agentId": a["id"],
        }, headers=auth(key))
        assert resp.status_code == 201, f"{name} was refused"


def test_a_setup_cannot_point_at_an_agent_that_does_not_exist(client, tenant_factory, auth):
    _, key = tenant_factory("312")
    resp = client.post("/api/call-setups", json={
        "name": "Ghost", "channel": "PHONE", "direction": "INBOUND",
        "agentId": "nope",
    }, headers=auth(key))
    assert resp.status_code == 422


def test_inbound_setups_on_different_channels_do_not_clash(client, tenant_factory, auth):
    """A phone line and a WhatsApp number are different numbers."""
    _, key = tenant_factory("313")
    a = _agent(client, auth, key)
    for channel in ("PHONE", "WHATSAPP_CALL"):
        resp = client.post("/api/call-setups", json={
            "name": f"{channel} line", "channel": channel, "direction": "INBOUND",
            "agentId": a["id"],
        }, headers=auth(key))
        assert resp.status_code == 201, f"{channel} was refused"


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_one_company_cannot_see_anothers_agents(client, tenant_factory, auth):
    _, key_a = tenant_factory("320")
    _, key_b = tenant_factory("321")
    _agent(client, auth, key_a, "Theirs")

    mine = client.get("/api/agents", headers=auth(key_b)).json()
    assert mine["agents"] == [], "another company's agent was visible"


def test_one_company_cannot_fetch_anothers_agent_by_id(client, tenant_factory, auth):
    _, key_a = tenant_factory("322")
    _, key_b = tenant_factory("323")
    theirs = _agent(client, auth, key_a, "Theirs")

    resp = client.get(f"/api/agents/{theirs['id']}", headers=auth(key_b))
    assert resp.status_code == 404, "an agent was readable across companies by id"


# ---------------------------------------------------------------------------
# The resolution chain
# ---------------------------------------------------------------------------

async def test_resolution_falls_back_to_the_whole_company(fake_db):
    """A company with no agents and no bases behaves exactly as before."""
    from app.models.agent import Direction
    from app.models.call import Channel
    from app.services import routing

    tenant = await _tenant("330")
    resolved = await routing.resolve(
        tenant, channel=Channel.PHONE, direction=Direction.INBOUND
    )
    assert resolved.agent is None
    assert resolved.knowledge_base_id == ""
    assert resolved.resolved_by == "company"


async def test_the_setup_decides_what_an_incoming_call_knows(fake_db):
    from app.models.agent import Agent, CallSetup, Direction, KnowledgeBase
    from app.models.call import Channel
    from app.repositories import agents as repo
    from app.services import routing

    tenant = await _tenant("331")
    kb = KnowledgeBase(tenantId="331", name="Support handbook")
    await repo.save_knowledge_base(kb)
    agent = Agent(tenantId="331", name="Support")
    await repo.save_agent(agent)
    await repo.save_setup(CallSetup(
        tenantId="331", name="Main line", channel=Channel.PHONE,
        direction=Direction.INBOUND, agentId=agent.id, knowledgeBaseId=kb.id,
    ))

    resolved = await routing.resolve(
        tenant, channel=Channel.PHONE, direction=Direction.INBOUND
    )
    assert resolved.agent_id == agent.id
    assert resolved.knowledge_base_id == kb.id
    assert resolved.resolved_by == "setup"
    assert resolved.knowledge_base_name == "Support handbook"


async def test_an_outbound_call_can_name_its_own_knowledge(fake_db):
    """What a campaign needs: this list, answered from the price list."""
    from app.models.agent import Direction, KnowledgeBase
    from app.models.call import Channel
    from app.repositories import agents as repo
    from app.services import routing

    tenant = await _tenant("332")
    prices = KnowledgeBase(tenantId="332", name="Prices")
    await repo.save_knowledge_base(prices)

    resolved = await routing.resolve(
        tenant, channel=Channel.PHONE, direction=Direction.OUTBOUND,
        knowledge_base_id=prices.id,
    )
    assert resolved.knowledge_base_id == prices.id
    assert resolved.resolved_by == "explicit"


async def test_an_agents_own_base_is_used_when_nothing_else_says(fake_db):
    from app.models.agent import Agent, Direction, KnowledgeBase
    from app.models.call import Channel
    from app.repositories import agents as repo
    from app.services import routing

    tenant = await _tenant("333")
    kb = KnowledgeBase(tenantId="333", name="Sales")
    await repo.save_knowledge_base(kb)
    agent = Agent(tenantId="333", name="Seller", knowledgeBaseId=kb.id)
    await repo.save_agent(agent)

    resolved = await routing.resolve(
        tenant, channel=Channel.PHONE, direction=Direction.OUTBOUND, agent_id=agent.id
    )
    assert resolved.knowledge_base_id == kb.id
    assert resolved.resolved_by == "agent"


async def test_a_deleted_base_falls_back_rather_than_answering_from_nothing(fake_db):
    from app.models.agent import Direction
    from app.models.call import Channel
    from app.services import routing

    tenant = await _tenant("334")
    resolved = await routing.resolve(
        tenant, channel=Channel.PHONE, direction=Direction.OUTBOUND,
        knowledge_base_id="deleted-long-ago",
    )
    assert resolved.knowledge_base_id == ""
    assert resolved.resolved_by == "company"


async def test_an_empty_base_still_answers_from_the_company(fake_db):
    """Making an empty base must not make the agent forget everything."""
    from app.repositories import knowledge as knowledge_repo

    await _tenant("335")
    await knowledge_repo.save("335", "hours.txt", "We are open until six.")
    context = await knowledge_repo.context_for("335", knowledge_base_id="empty-base")
    assert "open until six" in context


async def test_a_populated_base_excludes_the_other_documents(fake_db):
    from app.repositories import knowledge as knowledge_repo

    await _tenant("336")
    await knowledge_repo.save("336", "prices.txt", "A consultation is 2000 rupees.",
                              knowledge_base_id="kb-prices")
    await knowledge_repo.save("336", "hours.txt", "We are open until six.")

    context = await knowledge_repo.context_for("336", knowledge_base_id="kb-prices")
    assert "2000 rupees" in context
    assert "open until six" not in context, "another base's document leaked in"


async def test_persona_falls_back_to_the_company_when_there_is_no_agent(fake_db):
    from app.models.agent import Direction
    from app.models.call import Channel
    from app.services import routing

    tenant = await _tenant("337", agentGreeting="Hello from the company")
    resolved = await routing.resolve(
        tenant, channel=Channel.PHONE, direction=Direction.INBOUND
    )
    assert routing.persona_for(tenant, resolved)["greeting"] == "Hello from the company"


# ---------------------------------------------------------------------------
# The runtime actually using the resolution
# ---------------------------------------------------------------------------

async def test_an_answered_call_uses_the_setups_knowledge_and_records_why(fake_db):
    """The whole point: the agent on this number answers from this base.

    Exercises `live.persona_for` with a real Call, which is the path every
    answered call takes — a phone call, a WhatsApp call and a browser test
    all arrive here.
    """
    from app.models.agent import Agent, CallSetup, Direction as SetupDirection, KnowledgeBase
    from app.models.call import Call, Channel, Direction
    from app.repositories import agents as repo
    from app.repositories import knowledge as knowledge_repo
    from app.services.agent import live

    tenant = await _tenant("340")
    kb = KnowledgeBase(tenantId="340", name="Support handbook")
    await repo.save_knowledge_base(kb)
    await knowledge_repo.save("340", "support.txt", "Refunds take five days.",
                              knowledge_base_id=kb.id)
    await knowledge_repo.save("340", "prices.txt", "A consultation is 2000 rupees.")
    agent = Agent(tenantId="340", name="Support", greeting="Support here")
    await repo.save_agent(agent)
    await repo.save_setup(CallSetup(
        tenantId="340", name="Main line", channel=Channel.PHONE,
        direction=SetupDirection.INBOUND, agentId=agent.id, knowledgeBaseId=kb.id,
    ))

    call = Call(tenantId="340", channel=Channel.PHONE, direction=Direction.INBOUND)
    persona = await live.persona_for(tenant, call)

    assert "Refunds take five days" in persona.instructions
    assert "2000 rupees" not in persona.instructions, "the other base leaked into the prompt"
    assert persona.greeting == "Support here"
    # And the call records what answered it.
    assert call.agent_id == agent.id
    assert call.knowledge_base_id == kb.id
    assert call.resolved_by == "setup"


async def test_an_outbound_call_answers_from_the_base_it_names(fake_db):
    """A campaign dialling from the price list, not the support handbook."""
    from app.models.agent import KnowledgeBase
    from app.models.call import Call, Channel, Direction
    from app.repositories import agents as repo
    from app.repositories import knowledge as knowledge_repo
    from app.services.agent import live

    tenant = await _tenant("341")
    prices = KnowledgeBase(tenantId="341", name="Prices")
    await repo.save_knowledge_base(prices)
    await knowledge_repo.save("341", "prices.txt", "A consultation is 2000 rupees.",
                              knowledge_base_id=prices.id)
    await knowledge_repo.save("341", "support.txt", "Refunds take five days.",
                              knowledge_base_id="other-base")

    call = Call(tenantId="341", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                knowledgeBaseId=prices.id)
    persona = await live.persona_for(tenant, call)

    assert "2000 rupees" in persona.instructions
    assert "Refunds take five days" not in persona.instructions
    assert call.resolved_by == "explicit"


async def test_a_company_with_no_agents_answers_exactly_as_before(fake_db):
    """The fallback that makes this safe to add to a running system."""
    from app.models.call import Call, Channel, Direction
    from app.repositories import knowledge as knowledge_repo
    from app.services.agent import live

    tenant = await _tenant("342", agentGreeting="Hello from Acme")
    await knowledge_repo.save("342", "hours.txt", "We are open until six.")

    call = Call(tenantId="342", channel=Channel.PHONE, direction=Direction.INBOUND)
    persona = await live.persona_for(tenant, call)

    assert "open until six" in persona.instructions
    assert persona.greeting == "Hello from Acme"
    assert call.resolved_by == "company"


async def test_a_whatsapp_message_answers_from_that_numbers_knowledge(fake_db):
    """A message to the support number answers from the support handbook."""
    from app.models.agent import Agent, CallSetup, Direction as SD, KnowledgeBase
    from app.models.call import Channel
    from app.repositories import agents as repo
    from app.repositories import knowledge as knowledge_repo
    from app.services import routing

    tenant = await _tenant("350")
    kb = KnowledgeBase(tenantId="350", name="Support handbook")
    await repo.save_knowledge_base(kb)
    await knowledge_repo.save("350", "support.txt", "Refunds take five days.",
                              knowledge_base_id=kb.id)
    await knowledge_repo.save("350", "prices.txt", "A consultation is 2000 rupees.",
                              knowledge_base_id="a-different-base")
    agent = Agent(tenantId="350", name="Support")
    await repo.save_agent(agent)
    await repo.save_setup(CallSetup(
        tenantId="350", name="WhatsApp support", channel=Channel.WHATSAPP_MESSAGE,
        direction=SD.INBOUND, agentId=agent.id, knowledgeBaseId=kb.id,
    ))

    resolved = await routing.resolve(
        tenant, channel=Channel.WHATSAPP_MESSAGE, direction=SD.INBOUND
    )
    context = await routing.context_for(tenant, resolved)
    assert "Refunds take five days" in context
    assert "2000 rupees" not in context, "the wrong base answered a WhatsApp message"
    assert resolved.resolved_by == "setup"


async def test_a_campaign_carries_its_agent_onto_every_call(fake_db):
    """What makes one list answer from the price list and another not."""
    from app.models.campaign import Campaign, CampaignStatus, Contact

    campaign = Campaign(
        tenantId="351", name="Renewals", status=CampaignStatus.RUNNING,
        agentId="agent-sales", knowledgeBaseId="kb-prices",
        contacts=[Contact(number="+923001112222")],
    )
    assert campaign.public()["agentId"] == "agent-sales"
    assert campaign.public()["knowledgeBaseId"] == "kb-prices"
