"""
One agent session per call, and a call that cannot get one is ended, not left silent.

Taken from production on a Twilio line. Every call where the caller heard
nothing logged `answered … (1 live)`, and every call that worked did not. That
line is only written when `live.start` builds a session of its own, which
it did whenever the audio socket arrived while `warm_up` was still reading
the database: neither had registered yet, so both built one, and `start`
then overwrote the warmed one in the registry.

So every such call opened two model sessions. The speech key allows only
one at a time, so the second (the one the call actually used) was refused
with "out of quota", and the caller heard nothing. The first stayed
connected to nobody until the provider closed it two minutes later for
inactivity, which also blocked the next call. And because nothing hung up,
the caller sat on a silent line and the call was filed as "Completed".

The engine here behaves like that key: one open session, and the second is
refused the way Gemini refuses it.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live
from app.services.agent.gemini import AgentPersona, AgentUnavailable, LiveSettings


class OneSlotEngine:
    """A speech engine whose key allows one open session at a time."""

    # Who holds a slot. A set rather than a count, so an engine task left over
    # from an earlier test can only ever remove itself.
    holding: set = set()
    built: list["OneSlotEngine"] = []
    slots: int = 1
    speaks: bool = True

    def __init__(self, *_a, on_audio=None, **_k):
        self.on_audio = on_audio
        self.closed = asyncio.Event()
        self.ready = asyncio.Event()
        self.refused = False
        OneSlotEngine.built.append(self)

    @property
    def live(self) -> bool:
        return self.ready.is_set() and not self.refused

    async def wait_until_ready(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self.ready.wait(), timeout)

    def close(self) -> None:
        self.closed.set()

    def feed(self, _pcm: bytes) -> None:
        pass

    async def say(self, _text: str) -> None:
        pass

    async def run(self) -> None:
        if len(OneSlotEngine.holding) >= OneSlotEngine.slots:
            self.refused = True
            self.ready.set()
            raise AgentUnavailable("OUT OF QUOTA: You exceeded your current quota")
        OneSlotEngine.holding.add(self)
        try:
            self.ready.set()
            if OneSlotEngine.speaks and self.on_audio:
                # A greeting: 200 ms of 24 kHz audio, as the model sends it.
                self.on_audio(b"\x01\x00" * 4800, 24000)
            await self.closed.wait()
        finally:
            OneSlotEngine.holding.discard(self)


@pytest.fixture
async def engine(monkeypatch, fake_db):
    """The one-slot engine, and the database reads a call makes, slowed down
    to the ~0.3 s they take in production so the race actually happens."""
    from app.services.agent import engines

    OneSlotEngine.holding = set()
    OneSlotEngine.built = []
    OneSlotEngine.slots = 1
    OneSlotEngine.speaks = True
    monkeypatch.setattr(engines, "build", lambda *a, **k: OneSlotEngine(*a, **k))

    async def settings_from_db(_tenant=None):
        await asyncio.sleep(0.3)
        return LiveSettings(model="m", api_key="k")

    async def persona_from_db(_tenant, _call=None):
        await asyncio.sleep(0.05)
        return AgentPersona(instructions="be helpful", greeting="hello")

    monkeypatch.setattr(live, "live_settings_now", settings_from_db)
    monkeypatch.setattr(live, "persona_for", persona_from_db)
    live._sessions.clear()
    yield OneSlotEngine
    # Ended properly, in this test's own loop, so no engine or pacer task
    # outlives the test and touches the next one.
    for session in list(live._sessions.values()):
        await session.end("test over")
    for built in OneSlotEngine.built:
        built.close()
    await asyncio.sleep(0.05)
    live._sessions.clear()


@pytest.fixture
def hangups(monkeypatch):
    from app.services import carriers

    ended: list[str] = []

    async def hang_up(_tenant, provider_call_id):
        ended.append(provider_call_id)

    monkeypatch.setattr(carriers, "hang_up", hang_up)
    return ended


async def _call(**extra) -> tuple[Tenant, Call]:
    from app.repositories import calls as call_repo

    tenant = Tenant(phoneNumberId="900", name="Acme", recordCalls=False)
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.RINGING, counterparty="+923001112222",
                providerCallId="CA-provider-1", **extra)
    await call_repo.save_call(call)
    return tenant, call


async def _saved(call: Call) -> Call:
    from app.repositories import calls as call_repo

    return await call_repo.get_call(call.tenant_id, call.id)


async def _settle():
    """Let background tasks (the agent, the pacer, a hang-up) run."""
    for _ in range(10):
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# One session per call
# ---------------------------------------------------------------------------

async def test_the_line_connecting_mid_warm_up_uses_the_warmed_session(engine, hangups):
    """The production failure, reproduced: socket arrives while warming."""
    tenant, call = await _call()
    heard: list[bytes] = []

    warming = asyncio.create_task(live.warm_up(tenant, call))
    await asyncio.sleep(0.05)                      # the socket is quicker than the DB
    session = await live.start(tenant, call, heard.append, keepalive=False)
    await warming
    await _settle()

    assert len(engine.built) == 1, (
        f"{len(engine.built)} model sessions were opened for one call; the "
        "second is refused for quota and the caller hears nothing")
    assert live.get(call.id) is session and not session.closed
    assert heard, "the agent's greeting never reached the line"
    assert hangups == []


async def test_a_line_with_no_warm_up_still_gets_exactly_one_session(engine, hangups):
    tenant, call = await _call()
    heard: list[bytes] = []
    session = await live.start(tenant, call, heard.append, keepalive=False)
    await _settle()
    assert len(engine.built) == 1 and not session.closed and heard


async def test_a_warm_up_that_finished_first_is_reused(engine, hangups):
    tenant, call = await _call()
    await live.warm_up(tenant, call)
    heard: list[bytes] = []
    session = await live.start(tenant, call, heard.append, keepalive=False)
    await _settle()
    assert len(engine.built) == 1 and not session.closed and heard


async def test_a_warm_up_refused_before_the_line_connects_is_retried_once(engine, hangups):
    """A key that was busy a moment ago may be free when the caller arrives."""
    tenant, call = await _call()
    engine.holding.add("another call")                                # someone else holds the slot
    await live.warm_up(tenant, call)
    await _settle()
    assert live.get(call.id) is None, "a dead warm-up session was left for the call"

    engine.holding.clear()                                # the slot is free again
    heard: list[bytes] = []
    session = await live.start(tenant, call, heard.append, keepalive=False)
    await _settle()
    assert not session.closed and heard
    assert hangups == []


# ---------------------------------------------------------------------------
# A call that cannot get an agent
# ---------------------------------------------------------------------------

async def test_no_agent_means_the_call_is_hung_up_and_marked_failed(engine, hangups):
    tenant, call = await _call()
    engine.holding.add("another call")                                # the key is busy for the whole call
    session = await live.start(tenant, call, lambda _f: None, keepalive=False)
    await _settle()

    assert session.closed
    assert hangups == ["CA-provider-1"], "the caller was left on a silent line"
    saved = await _saved(call)
    assert saved.status is CallStatus.FAILED, f"filed as {saved.status.value}"
    assert "quota" in saved.error.lower()
    assert live.get(call.id) is None


async def test_a_failure_while_start_is_still_running_is_not_overwritten(engine, hangups):
    """The refusal lands while `start` is awaiting the model; `start` must not
    then mark the call as answered and in progress over the failure."""
    tenant, call = await _call()
    engine.holding.add("another call")
    await live.start(tenant, call, lambda _f: None, keepalive=False)
    await _settle()
    saved = await _saved(call)
    assert saved.status is CallStatus.FAILED


async def test_an_orphaned_session_failing_does_not_hang_up_the_call(engine, hangups):
    """Only the session the call is using may end it."""
    tenant, call = await _call()
    session = await live.start(tenant, call, lambda _f: None, keepalive=False)
    await _settle()
    stray = OneSlotEngine()
    from app.services.agent.session import CallSession

    orphan = CallSession(call.id, send_to_caller=lambda _f: None,
                         live_settings=LiveSettings(model="m", api_key="k"),
                         persona=AgentPersona(instructions=""), record=False)
    orphan.on_agent_failed = live._when_agent_fails(tenant, call)
    await orphan._report_failure("the model could not be reached")
    await _settle()
    assert hangups == [] and not session.closed
    del stray


async def test_an_agent_lost_mid_conversation_keeps_the_call_as_completed(engine, hangups):
    """It talked, so the call happened; the reason is still recorded."""
    tenant, call = await _call()
    session = await live.start(tenant, call, lambda _f: None, keepalive=False)
    await _settle()
    assert session.frames_out > 0
    await session._report_failure("the model could not be reached")
    await _settle()
    saved = await _saved(call)
    assert hangups == ["CA-provider-1"]
    assert saved.status is CallStatus.COMPLETED
    assert "could not be reached" in saved.error


# ---------------------------------------------------------------------------
# The whole Twilio path
# ---------------------------------------------------------------------------

def test_a_real_inbound_call_opens_one_agent_and_the_caller_hears_it(
        client, fake_db, monkeypatch):
    """The production failure, end to end and unpatched between webhook and
    socket: the webhook starts the warm-up in the background, Twilio's stream
    connects while it is still reading the database, and the speech key
    allows one open session. Before the fix this opened two sessions, the one
    the call used was refused for quota, and the caller heard nothing."""
    from app.api.routes import media, webhooks
    from tests.test_twilio import (BASE, _calls, _ring, _start, _stream_of,
                                   _twilio_number)
    from app.config import settings
    from app.models.number import NumberMode, NumberStatus
    from app.repositories import numbers as repo
    from app.repositories import tenants as tenant_repo
    from app.services.agent import engines, live
    from app.services.agent.gemini import AgentPersona, LiveSettings

    monkeypatch.setattr(webhooks, "_seen_inbound", {})
    monkeypatch.setattr(media, "_twilio_attached", set())
    monkeypatch.setattr(settings, "public_base_url", BASE)
    asyncio.run(tenant_repo.save(Tenant(phoneNumberId="700", name="Acme", recordCalls=False)))
    asyncio.run(repo.save(_twilio_number(status=NumberStatus.VERIFIED, mode=NumberMode.BOTH,
                                         inboundAgentId="agent-1")))

    OneSlotEngine.holding = set()
    OneSlotEngine.built = []
    OneSlotEngine.slots = 1
    OneSlotEngine.speaks = True
    monkeypatch.setattr(engines, "build", lambda *a, **k: OneSlotEngine(*a, **k))

    async def settings_from_db(_tenant=None):
        await asyncio.sleep(0.3)                  # slower than Twilio's socket
        return LiveSettings(model="m", api_key="k")

    async def persona_from_db(_tenant, _call=None):
        return AgentPersona(instructions="be helpful", greeting="hello")

    monkeypatch.setattr(live, "live_settings_now", settings_from_db)
    monkeypatch.setattr(live, "persona_for", persona_from_db)

    url, token = _stream_of(_ring(client).text)
    with client.websocket_connect(url.removeprefix("wss://api.example.com")) as phone:
        _start(phone, token)                      # at once, mid warm-up
        greeting = phone.receive_json()
        assert greeting["event"] == "media", "the caller heard nothing"

    assert len(OneSlotEngine.built) == 1, (
        f"{len(OneSlotEngine.built)} model sessions were opened for one call")
    from app.models.call import CallStatus

    [call] = _calls(fake_db)
    assert call.status is not CallStatus.FAILED
