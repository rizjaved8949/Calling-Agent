"""
A person calling a customer from the company's number.

No agent is involved: the employee's browser is the far end of the call. The
path has four joins — place the call, bridge a leg, attach the employee's
socket, carry audio both ways — and a mistake at any one of them is a call that
connects to silence.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.agent.audio_rate import FRAME_BYTES
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession, Handler


def _human_session(to_caller: list[bytes]) -> CallSession:
    return CallSession(
        "call-1",
        send_to_caller=to_caller.append,
        live_settings=LiveSettings(model="none", api_key=""),
        persona=AgentPersona(instructions="", greeting=""),
        record=False,
        keepalive=False,
        human=True,
    )


def test_a_human_call_starts_with_the_person_not_the_agent():
    session = _human_session([])
    assert session.human is True
    assert session.handler is Handler.OPERATOR, "the agent had the call"


@pytest.mark.asyncio
async def test_no_model_is_started_for_a_human_call():
    """`start` brings up the agent for a normal call. For this one it must not
    — there is no API key here, so a model would fail loudly if attempted."""
    session = _human_session([])
    await session.start()
    assert session._agent is None
    await session.end("done")


@pytest.mark.asyncio
async def test_the_caller_is_heard_by_the_employee_and_nobody_else():
    to_caller: list[bytes] = []
    session = _human_session(to_caller)
    await session.start()

    to_employee: list[bytes] = []
    await session.hand_over(to_employee.append)

    session.feed_caller(b"\x01\x02" * (FRAME_BYTES // 2))
    assert to_employee, "the employee heard nothing the caller said"
    assert to_caller == [], "the caller's own voice was echoed back"
    await session.end("done")


@pytest.mark.asyncio
async def test_the_employee_is_heard_by_the_caller():
    to_caller: list[bytes] = []
    session = _human_session(to_caller)
    await session.start()
    await session.hand_over(lambda frame: None)

    for _ in range(5):
        session.feed_operator(b"" * (FRAME_BYTES // 2))
    for _ in range(12):
        session._pacer.tick()
        await asyncio.sleep(0.021)
    assert to_caller, "the caller heard nothing the employee said"
    await session.end("done")


@pytest.mark.asyncio
async def test_a_human_voice_is_barely_buffered():
    """The 300 ms jitter buffer exists for the model's bursty output. Applied
    to a person it is just delay, and delay is what has two people talk over
    each other and then both stop."""
    from app.services.agent.pacer import HUMAN_PREROLL_FRAMES, PREROLL_FRAMES

    human = _human_session([])
    assert human._pacer.preroll == HUMAN_PREROLL_FRAMES
    assert HUMAN_PREROLL_FRAMES * 20 <= 60, "more than 60ms of delay on a voice"

    agent = CallSession(
        "call-2", send_to_caller=lambda f: None,
        live_settings=LiveSettings(model="none", api_key=""),
        persona=AgentPersona(instructions="", greeting=""), record=False,
    )
    assert agent._pacer.preroll == PREROLL_FRAMES


@pytest.mark.asyncio
async def test_the_agent_never_takes_over_when_the_employee_leaves():
    """On an agent call, an operator leaving hands it back. On a dialer call
    there is nothing to hand it back to, and a model appearing mid-conversation
    would be worse than the line going quiet."""
    session = _human_session([])
    await session.start()
    await session.hand_over(lambda frame: None)
    await session.hand_back()
    assert session.handler is Handler.OPERATOR
    assert session._agent is None
    await session.end("done")


def test_the_call_records_who_placed_it(client, tenant_factory, auth, monkeypatch):
    """So "my calls" can find it, and so a recording has a name against it."""
    from app.services import lines, telephony

    async def verified(_tenant):
        return "ok"
    monkeypatch.setattr(lines, "_verify_infobip", verified)

    _, key = tenant_factory("950")
    number = client.post("/api/numbers", json={
        "kind": "sim", "phoneNumber": "+923001112222", "mode": "outbound",
        "infobipApiKey": "k-12345678", "infobipBaseUrl": "x.api.infobip.com",
    }, headers=auth(key)).json()

    async def fake_place(self, to, *, from_number=""):
        return {"id": "provider-1"}
    monkeypatch.setattr(telephony.Infobip, "place_call", fake_place)

    placed = client.post("/api/calls", json={
        "to": "+923334445555", "channel": "PHONE", "lineId": number["id"],
        "human": True, "metadata": {"placedBy": "omar@acme.com"},
    }, headers=auth(key))
    assert placed.status_code == 201, placed.text
    body = placed.json()
    assert body["mode"] == "human"
    assert body["placedBy"] == "omar@acme.com"
    # A human call needs no outbound agent, which is the whole point of it.
    assert body["lineId"] == number["id"]


def test_a_human_call_needs_no_outbound_agent(client, tenant_factory, auth, monkeypatch):
    from app.services import lines, telephony

    async def verified(_tenant):
        return "ok"
    monkeypatch.setattr(lines, "_verify_infobip", verified)
    async def fake_place(self, to, *, from_number=""):
        return {"id": "p-2"}
    monkeypatch.setattr(telephony.Infobip, "place_call", fake_place)

    _, key = tenant_factory("951")
    client.post("/api/numbers", json={
        "kind": "sim", "phoneNumber": "+923001112222", "mode": "both",
        "infobipApiKey": "k-12345678", "infobipBaseUrl": "x.api.infobip.com",
    }, headers=auth(key))

    # The agent path is refused without an agent...
    refused = client.post("/api/calls", json={"to": "+923334445555", "channel": "PHONE"},
                          headers=auth(key))
    assert refused.status_code == 409
    # ...but a person may still call.
    allowed = client.post("/api/calls", json={
        "to": "+923334445555", "channel": "PHONE", "human": True,
    }, headers=auth(key))
    assert allowed.status_code == 201
