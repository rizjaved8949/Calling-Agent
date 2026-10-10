"""
The instructions an agent is given, and a company's right to change them.

All of this used to be a literal in `live.py`: a company saw a greeting and a
persona on screen while a few hundred words it had never agreed to decided
what the agent may promise and when it hangs up. These pin that every block is
now reachable — and, just as importantly, that an agent which has customised
nothing is given exactly what it was given before.
"""
from __future__ import annotations

import pytest

from app.services.agent import prompt


def _built(**kwargs) -> str:
    base = {"persona": "You are Ayesha, the clinic's assistant.", "context": ""}
    return prompt.build(**{**base, **kwargs})


# ---------------------------------------------------------------------------
# The defaults still apply
# ---------------------------------------------------------------------------


def test_an_agent_that_customises_nothing_gets_the_standard_wording():
    text = _built()
    for block in prompt.BLOCKS:
        if block.needs_messaging:
            continue
        # Compared on the first line, because the blocks are multi-line and
        # this is about the block being present at all.
        assert block.default.splitlines()[0] in text, block.id


def test_the_persona_leads_and_is_restated_at_the_end():
    """Identity placed before a large knowledge base loses to the names used
    inside it — measured at four times out of four until it was repeated."""
    text = _built(context="x" * 2000)
    persona = "You are Ayesha, the clinic's assistant."
    assert text.startswith(persona)
    assert text.count(persona) == 2
    assert text.rindex(persona) > text.index("## The material you answer from")


def test_every_rule_comes_after_the_material():
    """Rules before a long knowledge base were followed two times in five.
    This ordering is load-bearing, not cosmetic."""
    text = _built(context="y" * 5000, can_send_whatsapp=True)
    material = text.index("## The material you answer from")
    for block in prompt.BLOCKS:
        if block.id == "identity":
            continue  # deliberately first, and repeated last
        assert text.index(block.default.splitlines()[0]) > material, block.id


def test_the_writing_rules_appear_only_when_a_message_can_be_sent():
    without = _built(can_send_whatsapp=False)
    withit = _built(can_send_whatsapp=True)
    assert "send_whatsapp_message" not in without
    assert "send_whatsapp_message" in withit


# ---------------------------------------------------------------------------
# A company's own words
# ---------------------------------------------------------------------------


def test_a_rewritten_block_replaces_ours_entirely():
    text = _built(written={"limits": "You may never discuss price."})
    assert "You may never discuss price." in text
    assert "There is no switchboard behind you" not in text


def test_an_empty_block_falls_back_to_the_default():
    """Empty means "use the standard wording", not "say nothing" — otherwise
    clearing a field silently removes a safety rule."""
    text = _built(written={"limits": "   "})
    assert "There is no switchboard behind you" in text


def test_extra_rules_go_last_so_they_win():
    text = _built(extra="Always greet in Urdu first.")
    assert "Always greet in Urdu first." in text
    assert text.index("Always greet in Urdu first.") > text.index(
        prompt.ENDING.default.splitlines()[0])


def test_an_override_replaces_everything_but_keeps_the_material():
    """Somebody writing the whole prompt should not have to paste their own
    documents into it."""
    text = _built(override="Say only: we are closed.", context="OPENING HOURS: 9-5")
    assert text.startswith("Say only: we are closed.")
    assert "OPENING HOURS: 9-5" in text
    assert "There is no switchboard behind you" not in text
    assert prompt.ENDING.default.splitlines()[0] not in text


def test_tone_and_escalation_still_reach_the_agent():
    text = _built(tone="Dry and brief.", escalation="Escalate anything legal.")
    assert "Dry and brief." in text
    assert "Escalate anything legal." in text


def test_a_pace_is_said_as_behaviour_not_a_number():
    text = _built(pace="slow")
    assert prompt.PACE_RULES["slow"] in text
    assert prompt.PACE_RULES["brisk"] not in text


def test_an_unknown_pace_adds_nothing():
    assert all(rule not in _built(pace="supersonic") for rule in prompt.PACE_RULES.values())


def test_with_no_material_it_is_told_not_to_invent_facts():
    assert "do not state specific facts" in _built(context="")


def test_a_company_with_no_persona_still_gets_one():
    assert prompt.FALLBACK_PERSONA in _built(persona="   ")


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


def test_the_blocks_are_offered_with_the_wording_we_would_use(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    body = client.get("/api/agents/prompt-blocks", headers=auth(key)).json()
    ids = {b["id"] for b in body["blocks"]}
    assert ids == {block.id for block in prompt.BLOCKS}
    for block in body["blocks"]:
        assert block["default"].strip()
        assert block["title"] and block["help"]
    assert set(body["paceRules"]) == {"slow", "natural", "brisk"}


def test_an_agents_blocks_can_be_written_and_read_back(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    agent_id = client.post("/api/agents", headers=auth(key),
                           json={"name": "Clinic"}).json()["id"]

    saved = client.patch(f"/api/agents/{agent_id}", headers=auth(key), json={
        "promptBlocks": {"limits": "Never quote a price."},
        "extraRules": "Always confirm the spelling of a name.",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["promptBlocks"]["limits"] == "Never quote a price."
    assert saved.json()["extraRules"] == "Always confirm the spelling of a name."


def test_the_preview_shows_what_the_agent_will_actually_be_told(
    client, tenant_factory, auth,
):
    """Reading it back is the only way to tell whether a rule survived."""
    _, key = tenant_factory("111", "Acme")
    agent_id = client.post("/api/agents", headers=auth(key), json={
        "name": "Clinic", "roleDescription": "You are the clinic's assistant.",
    }).json()["id"]
    client.patch(f"/api/agents/{agent_id}", headers=auth(key), json={
        "promptBlocks": {"limits": "Never quote a price."},
        "extraRules": "Always confirm the spelling of a name.",
    })

    body = client.get(f"/api/agents/{agent_id}/prompt", headers=auth(key)).json()
    assert "You are the clinic's assistant." in body["instructions"]
    assert "Never quote a price." in body["instructions"]
    assert "Always confirm the spelling of a name." in body["instructions"]
    assert "There is no switchboard behind you" not in body["instructions"]
    assert body["characters"] == len(body["instructions"])


def test_the_preview_is_404_for_an_agent_that_does_not_exist(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    assert client.get("/api/agents/nope/prompt", headers=auth(key)).status_code == 404


def test_a_live_call_uses_the_companys_wording(fake_db, tenant_factory, auth, client):
    """The whole point: what is typed on the agent screen is what the model
    is given on the call, not a copy of it that drifted."""
    import asyncio

    from app.models.call import Call, Channel, Direction
    from app.repositories import tenants as tenant_repo
    from app.services.agent import live

    tenant, key = tenant_factory("111", "Acme")
    agent_id = client.post("/api/agents", headers=auth(key), json={
        "name": "Clinic", "roleDescription": "You are the clinic's assistant.",
    }).json()["id"]
    client.patch(f"/api/agents/{agent_id}", headers=auth(key), json={
        "promptBlocks": {"ending": "Never hang up. Wait for them."},
        "extraRules": "Refer to the doctor as Dr Sahib.",
    })

    call = Call(tenantId="111", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923001112222", agentId=agent_id)

    async def build():
        return await live.persona_for(await tenant_repo.require("111"), call)

    persona = asyncio.run(build())

    assert "Never hang up. Wait for them." in persona.instructions
    assert "Refer to the doctor as Dr Sahib." in persona.instructions
    # Replaced, not appended beside ours.
    assert "call end_call in the same turn" not in persona.instructions
