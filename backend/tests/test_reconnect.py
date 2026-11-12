"""
A model that drops a conversation in progress is reconnected once.

Four minutes into a real call Gemini answered "1011 Internal error" and the
caller got a dial tone. The session now opens a fresh model session carrying
the transcript so far, and only hangs up if that fails too.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.agent import engines, session as session_module
from app.services.agent.gemini import AgentPersona, AgentUnavailable, LiveSettings
from app.services.agent.session import CallSession


class Engine:
    """A model session that can be dropped from the test."""

    built: list["Engine"] = []

    def __init__(self, settings, persona, *, on_audio=None, on_transcript=None,
                 on_interrupted=None, on_tool=None, label="session"):
        self.persona = persona
        self.label = label
        self.connected = False
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.dropped = asyncio.Event()
        self.refuse = False
        Engine.built.append(self)

    @property
    def live(self) -> bool:
        return self.connected and not self.closed.is_set() and not self.dropped.is_set()

    async def wait_until_ready(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self.ready.wait(), timeout)

    def close(self) -> None:
        self.closed.set()

    def feed(self, _pcm: bytes) -> None:
        pass

    async def say(self, _text: str) -> None:
        pass

    async def run(self) -> None:
        if self.refuse:
            self.ready.set()
            raise AgentUnavailable("the model is out of quota")
        self.connected = True
        self.ready.set()
        waits = [asyncio.create_task(self.dropped.wait()), asyncio.create_task(self.closed.wait())]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()
        if self.dropped.is_set() and not self.closed.is_set():
            raise AgentUnavailable("the model could not be reached")


@pytest.fixture
def engine(monkeypatch):
    Engine.built = []
    monkeypatch.setattr(engines, "build", lambda *a, **k: Engine(*a, **k))
    return Engine


async def _session(failed: list[str]) -> CallSession:
    session = CallSession("call-1", send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions="Be helpful.", greeting="Salam!"),
                          record=False, keepalive=False)

    async def on_failed(_session, reason):
        failed.append(reason)

    session.on_agent_failed = on_failed
    await session.start(paced=False)
    session._note("caller", "SEAL ki price kya hai?")
    session._note("agent", "1 crore 69 lakh 90 hazar.")
    return session


async def test_a_drop_mid_call_reconnects_once_with_the_transcript(engine, caplog):
    failed: list[str] = []
    with caplog.at_level(logging.INFO, logger="app.services.agent.session"):
        session = await _session(failed)
        first = engine.built[0]
        first.dropped.set()
        await asyncio.sleep(0.2)
    try:
        assert len(engine.built) == 2, "no reconnect, or more than one"
        second = engine.built[1]
        assert session._agent is second and second.live
        assert not session.closed and failed == []
        assert second.persona.greeting == "", "the caller would be greeted twice"
        assert "SEAL ki price kya hai?" in second.persona.instructions
        assert "1 crore 69 lakh 90 hazar" in second.persona.instructions
        assert "Do not greet again" in second.persona.instructions
        assert second.persona.instructions.startswith("Be helpful.")
        messages = [r.getMessage() for r in caplog.records]
        assert any("reconnecting, attempt 1 of 1" in m for m in messages)
        assert any("reconnected to the model" in m for m in messages)
    finally:
        await session.end("test over")


async def test_a_second_drop_ends_the_call_as_before(engine):
    failed: list[str] = []
    session = await _session(failed)
    engine.built[0].dropped.set()
    await asyncio.sleep(0.2)
    engine.built[1].dropped.set()
    await asyncio.sleep(0.3)
    assert len(engine.built) == 2, "a third session was attempted"
    assert session.closed
    assert failed and "could not be reached" in failed[0]


async def test_a_reconnect_that_never_comes_up_ends_the_call(engine, monkeypatch):
    failed: list[str] = []
    session = await _session(failed)
    original = engines.build

    def refusing(*a, **k):
        made = Engine(*a, **k)
        made.refuse = True
        return made

    monkeypatch.setattr(engines, "build", refusing)
    engine.built[0].dropped.set()
    await asyncio.sleep(0.3)
    assert len(engine.built) == 2
    assert session.closed and failed
    monkeypatch.setattr(engines, "build", original)


async def test_a_refusal_at_the_start_is_not_retried(engine, monkeypatch):
    """Out of quota at the start is not a drop: a retry would only repeat it."""
    failed: list[str] = []

    def refusing(*a, **k):
        made = Engine(*a, **k)
        made.refuse = True
        return made

    monkeypatch.setattr(engines, "build", refusing)
    session = CallSession("call-2", send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False, keepalive=False)

    async def on_failed(_session, reason):
        failed.append(reason)

    session.on_agent_failed = on_failed
    await session.start(paced=False)
    await asyncio.sleep(0.2)
    assert len(engine.built) == 1
    assert session.closed and failed == ["the model is out of quota"]


def test_the_resume_note_keeps_only_the_tail_of_a_long_call():
    session = CallSession("call-3", send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions="Base."), record=False)
    for n in range(500):
        session._note("caller", f"line {n} " + "x" * 40)
    resumed = session._resume_instructions()
    assert resumed.startswith("Base.")
    assert len(resumed) < len("Base.") + session_module.RESUME_TRANSCRIPT_CHARS + 400
    assert "line 499" in resumed and "line 0 " not in resumed
