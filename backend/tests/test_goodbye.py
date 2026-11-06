"""
Ending a call: one goodbye, nothing after it, and never hanging up on a
caller who is still talking — without being fooled by the agent's own echo,
and without any call being able to run forever.

From two real calls: the agent said "Allah hafiz", then asked whether there
was anything else, then hung up; and a caller who kept talking after the
goodbye was cut off mid-sentence.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest

from app.config import settings
from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live
from app.services.agent.audio_rate import FRAME_BYTES, SILENT_FRAME
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import ECHO_TAIL_SECONDS, CallSession


def _voice(dbfs: float = -25.0) -> bytes:
    """One 20 ms frame of a voice-loud tone."""
    samples = FRAME_BYTES // 2
    wave = 10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * 220 * np.arange(samples) / 16000)
    return (wave * 32767).astype("<i2").tobytes()


def _session() -> CallSession:
    session = CallSession("call-1", send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)

    class Ear:
        def feed(self, _pcm):
            pass

    session._agent = Ear()
    return session


# ---------------------------------------------------------------------------
# The agent's echo is not the caller
# ---------------------------------------------------------------------------

def test_the_agents_echo_does_not_count_as_the_caller_speaking():
    session = _session()
    session._to_line(_voice())                  # the agent says something
    session.feed_caller(_voice())               # and it comes straight back
    assert not session.caller_spoke_within(5), "the agent's own echo was taken for the caller"


def test_nothing_counts_while_the_agent_still_has_words_queued():
    session = _session()
    session._pacer.offer(_voice() * 10)
    session.feed_caller(_voice())
    assert not session.caller_spoke_within(5)


def test_the_caller_counts_once_the_echo_has_died_away():
    session = _session()
    session._to_line(_voice())
    session.agent_audible_at = time.time() - ECHO_TAIL_SECONDS - 0.1
    session.feed_caller(_voice())
    assert session.caller_spoke_within(5)


def test_keep_alive_silence_is_not_the_agent_talking():
    """The line is kept alive with silent frames; those are not speech, and
    must not mute the caller for a second after every one."""
    session = _session()
    session._to_line(SILENT_FRAME)
    assert session.agent_audible_at == 0.0
    session.feed_caller(_voice())
    assert session.caller_spoke_within(5)


# ---------------------------------------------------------------------------
# After the goodbye
# ---------------------------------------------------------------------------

@pytest.fixture
def goodbye(monkeypatch):
    hung_up: list[str] = []

    async def fake_hang_up(_tenant, _call, reason, **_k):
        hung_up.append(reason)

    monkeypatch.setattr(live, "hang_up", fake_hang_up)
    monkeypatch.setattr(live, "GOODBYE_GRACE_SECONDS", 0.3)
    tenant = Tenant(phoneNumberId="900", name="Acme")
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.IN_PROGRESS, counterparty="+923001112222")
    session = _session()
    live._sessions[call.id] = session
    yield tenant, call, session, hung_up
    live._sessions.pop(call.id, None)


async def test_a_caller_who_says_nothing_after_the_goodbye_is_hung_up(goodbye):
    tenant, call, _session_, hung_up = goodbye
    await live._hang_up_after_goodbye(tenant, call, "the caller said goodbye")
    assert hung_up == ["the caller said goodbye"]


async def test_a_caller_who_speaks_after_the_goodbye_keeps_the_line(goodbye):
    tenant, call, session, hung_up = goodbye
    ending = asyncio.create_task(
        live._hang_up_after_goodbye(tenant, call, "the caller's question was answered"))
    await asyncio.sleep(0.1)
    session.feed_caller(_voice())               # "wait, one more thing"
    await ending
    assert hung_up == [], "hung up on a caller who was talking"


async def test_the_agents_own_goodbye_echoing_back_does_not_keep_the_line(goodbye):
    tenant, call, session, hung_up = goodbye
    ending = asyncio.create_task(
        live._hang_up_after_goodbye(tenant, call, "the caller said goodbye"))
    await asyncio.sleep(0.05)
    session._to_line(_voice())                  # "Allah hafiz" goes out...
    session.feed_caller(_voice())               # ...and comes back as echo
    await ending
    assert hung_up == ["the caller said goodbye"]


async def test_speech_before_the_goodbye_does_not_keep_the_line(goodbye):
    """Their own "Allah hafiz" is why the agent is closing, not a reason to stay."""
    tenant, call, session, hung_up = goodbye
    session.feed_caller(_voice())
    await asyncio.sleep(0.01)
    await live._hang_up_after_goodbye(tenant, call, "the caller said goodbye")
    assert hung_up == ["the caller said goodbye"]


async def test_after_end_call_the_agent_is_told_to_say_nothing_more(goodbye, monkeypatch):
    tenant, call, _session_, _hung_up = goodbye

    async def hold(*_a):
        pass

    monkeypatch.setattr(live, "_hang_up_after_goodbye", hold)
    answer = await live._tool_handler(tenant, call)("end_call", {"reason": "done"})
    assert "say nothing more" in answer
    assert "Say your goodbye now" not in answer, "that invited a second closing line"


# ---------------------------------------------------------------------------
# No call runs forever
# ---------------------------------------------------------------------------

async def test_every_answered_call_is_ended_at_the_time_limit(goodbye, monkeypatch, fake_db):
    tenant, call, _session_, hung_up = goodbye
    monkeypatch.setattr(settings, "max_call_seconds", 0.1)
    await live._mark_answered(tenant, call)
    await asyncio.sleep(0.3)
    assert hung_up == ["the call reached its time limit"]


async def test_a_call_that_ends_first_cancels_its_time_limit(monkeypatch, fake_db):
    monkeypatch.setattr(settings, "max_call_seconds", 0.2)
    hung_up: list[str] = []

    async def fake_hang_up(_tenant, _call, reason, **_k):
        hung_up.append(reason)

    monkeypatch.setattr(live, "hang_up", fake_hang_up)
    tenant = Tenant(phoneNumberId="900", name="Acme")
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.IN_PROGRESS, counterparty="+923001112222")
    await live._mark_answered(tenant, call)
    assert call.id in live._time_limits
    await live.finish(tenant, call.id, "caller hung up")
    await asyncio.sleep(0.3)
    assert hung_up == [] and call.id not in live._time_limits


def test_the_time_limit_is_a_setting_with_a_sane_default():
    from app.config import Settings

    assert 600 <= Settings.model_fields["max_call_seconds"].default <= 3600
