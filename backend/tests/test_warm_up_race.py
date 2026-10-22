"""
Warming the agent up must never displace the call that actually connected.

The bug this pins, seen on a real call: the recording and the transcript both
stopped after the agent's greeting, while the caller had a full conversation
and heard nothing wrong.

`warm_up` checked that no session existed, then awaited the database twice to
build one. The line connected during those awaits, so `start` found nothing,
built its own session and attached it to the carrier -- and then `warm_up`
finished and overwrote the registry with its own.

From that moment the real conversation ran on a session nothing was tracking,
and the session that got settled, recorded and transcribed was the warm-up
one, which had only ever spoken its greeting into its own recorder.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live


@pytest.fixture
def no_sessions():
    live._sessions.clear()
    yield live._sessions
    live._sessions.clear()


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="880", displayName="Acme", recordCalls=True)


def _call() -> Call:
    import time
    return Call(
        id="racing-call",
        tenantId="880",
        channel=Channel.WHATSAPP_CALL,
        direction=Direction.OUTBOUND,
        fromNumber="+100",
        toNumber="+200",
        startedAt=time.time(),
    )


class _FakeSession:
    """Stands in for the session `start` would build and attach to the line."""

    def __init__(self) -> None:
        self.ended_with = None

    def attach_transport(self, _send) -> None:
        pass

    async def end(self, reason: str = "") -> None:
        self.ended_with = reason


async def test_warming_stands_down_when_the_line_connected_first(
    fake_db, no_sessions, monkeypatch,
):
    """The registry must hold the session the carrier is attached to."""
    building = asyncio.Event()
    may_finish = asyncio.Event()
    built = []

    async def slow_settings(_tenant=None):
        # Stands in for the two database round trips warm_up makes while
        # holding no claim on the registry.
        building.set()
        await may_finish.wait()
        from app.services.agent.gemini import LiveSettings
        return LiveSettings(model="m", api_key="k")

    async def persona(_tenant, _call=None):
        from app.services.agent.gemini import AgentPersona
        return AgentPersona(instructions="be helpful", greeting="hello")

    class _Recording(_FakeSession):
        def __init__(self, *a, **k):
            super().__init__()
            built.append(self)

        async def start(self, paced: bool = True) -> None:
            pass

    monkeypatch.setattr(live, "live_settings_now", slow_settings)
    monkeypatch.setattr(live, "persona_for", persona)
    monkeypatch.setattr(live, "CallSession", _Recording)

    tenant, call = _tenant(), _call()
    warming = asyncio.create_task(live.warm_up(tenant, call))
    await building.wait()           # warm_up is mid-build, registry still empty

    # The line connects now. This is the session that carries the real call.
    live_session = _FakeSession()
    no_sessions[call.id] = live_session

    may_finish.set()
    await warming

    assert no_sessions[call.id] is live_session, (
        "the warm-up session displaced the one attached to the carrier, so the "
        "real conversation would be recorded and settled on the wrong session"
    )
    assert built and built[0].ended_with, (
        "the superseded warm-up session must be closed, not left holding a "
        "model socket for the rest of the call"
    )


async def test_warming_still_registers_when_nothing_else_did(
    fake_db, no_sessions, monkeypatch,
):
    """The ordinary path must keep working: warming early is the whole point."""
    async def settings(_tenant=None):
        from app.services.agent.gemini import LiveSettings
        return LiveSettings(model="m", api_key="k")

    async def persona(_tenant, _call=None):
        from app.services.agent.gemini import AgentPersona
        return AgentPersona(instructions="be helpful", greeting="hello")

    class _Fine(_FakeSession):
        def __init__(self, *a, **k):
            super().__init__()

        async def start(self, paced: bool = True) -> None:
            pass

    monkeypatch.setattr(live, "live_settings_now", settings)
    monkeypatch.setattr(live, "persona_for", persona)
    monkeypatch.setattr(live, "CallSession", _Fine)

    tenant, call = _tenant(), _call()
    await live.warm_up(tenant, call)
    assert call.id in no_sessions, "a warmed call should be waiting in the registry"
    assert no_sessions[call.id].ended_with is None
