"""
A quiet caller is raised before the model hears them, and the agent cannot
end a call as "the caller rang off" while the caller is still talking.

Measured on two real Twilio calls: the caller arrived at -28 to -36 dBFS,
about 15 dB under the agent. The model talked over their "hello?", and twice
ended the call with "the caller rang off" while they were still speaking.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import caller_level, live
from app.services.agent.caller_level import MAX_GAIN, PEAK_CEILING, CallerLevel, rms

RATE = 16_000
FRAME = RATE // 50                     # 20 ms


def _speech(dbfs: float, frames: int = 50, hz: float = 220.0) -> list[bytes]:
    """`frames` 20 ms frames of a voice-like tone at `dbfs` RMS."""
    amplitude = 10 ** (dbfs / 20) * np.sqrt(2)
    t = np.arange(frames * FRAME) / RATE
    wave = amplitude * np.sin(2 * np.pi * hz * t)
    pcm = (wave * 32767).astype("<i2").tobytes()
    return [pcm[i * FRAME * 2:(i + 1) * FRAME * 2] for i in range(frames)]


def _db(frame: bytes) -> float:
    return 20 * np.log10(max(rms(frame), 1e-9))


# ---------------------------------------------------------------------------
# The level
# ---------------------------------------------------------------------------

def test_a_quiet_caller_is_raised_towards_normal_speech():
    level = CallerLevel()
    out = [level.process(f) for f in _speech(-32, frames=100)]
    assert _db(out[-1]) == pytest.approx(-20, abs=1.5), "not raised to speech level"
    assert level.gain <= MAX_GAIN


def test_the_raise_is_capped_so_a_whisper_is_not_made_loud():
    level = CallerLevel()
    out = [level.process(f) for f in _speech(-45, frames=100)]
    assert level.gain == pytest.approx(MAX_GAIN)
    assert _db(out[-1]) == pytest.approx(-45 + 20 * np.log10(MAX_GAIN), abs=1.0)


def test_a_caller_already_loud_enough_passes_through_untouched():
    level = CallerLevel()
    frames = _speech(-15)
    assert [level.process(f) for f in frames] == frames


def test_line_noise_alone_is_never_boosted():
    level = CallerLevel()
    hiss = _speech(-60, hz=3000)
    assert [level.process(f) for f in hiss] == hiss
    assert level.gain == 1.0


def test_no_peak_is_pushed_past_the_ceiling():
    level = CallerLevel()
    for f in _speech(-32, frames=100):
        level.process(f)
    # A sudden loud word after a quiet stretch, while the gain is high.
    for f in _speech(-6, frames=10):
        out = np.frombuffer(level.process(f), dtype="<i2")
        assert np.max(np.abs(out)) / 32768 <= PEAK_CEILING + 0.02


def test_the_gain_changes_smoothly_inside_a_frame():
    level = CallerLevel()
    first = level.process(_speech(-32, frames=1)[0])
    samples = np.frombuffer(first, dtype="<i2").astype(float)
    # Ramped from 1x: the start of the frame is not jumped up.
    assert np.max(np.abs(samples[:20])) < np.max(np.abs(samples[-20:]))


# ---------------------------------------------------------------------------
# In the session
# ---------------------------------------------------------------------------

class _Ear:
    """Stands in for the model, keeping what it was fed."""

    def __init__(self):
        self.heard: list[bytes] = []

    def feed(self, pcm: bytes) -> None:
        self.heard.append(pcm)


def _session(record: bool = True):
    from app.services.agent.gemini import AgentPersona, LiveSettings
    from app.services.agent.session import CallSession

    session = CallSession("call-1", send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=record)
    session._agent = _Ear()
    return session


def test_the_model_hears_the_raised_caller_and_the_recording_keeps_the_original():
    session = _session()
    session.caller_level = CallerLevel()
    recorded: list[bytes] = []
    session._recorder.feed = lambda side, pcm: recorded.append(pcm)
    session._caller_to_record.process = lambda pcm: pcm
    frames = _speech(-32, frames=100)
    for f in frames:
        session.feed_caller(f)
    assert _db(session._agent.heard[-1]) > _db(frames[-1]) + 8
    assert recorded == frames, "the recording must keep the call as it arrived"
    session._recorder.finish()


def test_without_a_level_the_model_hears_the_caller_as_they_came():
    """Infobip and Meta calls: nothing set, nothing changed."""
    session = _session(record=False)
    frames = _speech(-32)
    for f in frames:
        session.feed_caller(f)
    assert session._agent.heard == frames


def test_the_session_knows_when_the_caller_last_spoke():
    session = _session(record=False)
    for f in _speech(-60, hz=3000):                 # a quiet line
        session.feed_caller(f)
    assert not session.caller_spoke_within(5)
    session.feed_caller(_speech(-34, frames=1)[0])  # a quiet "hello?"
    assert session.caller_spoke_within(5)
    session.caller_spoke_at = time.time() - 6
    assert not session.caller_spoke_within(5)


# ---------------------------------------------------------------------------
# "The caller rang off"
# ---------------------------------------------------------------------------

class _Line:
    def __init__(self, spoke_ago: float | None):
        self.caller_spoke_at = 0.0 if spoke_ago is None else time.time() - spoke_ago

    def caller_spoke_within(self, seconds: float) -> bool:
        return bool(self.caller_spoke_at) and time.time() - self.caller_spoke_at <= seconds


@pytest.fixture
def ending(monkeypatch):
    ended: list[str] = []

    async def hang_up_after_goodbye(_tenant, _call, reason):
        ended.append(reason)

    monkeypatch.setattr(live, "_hang_up_after_goodbye", hang_up_after_goodbye)
    tenant = Tenant(phoneNumberId="900", name="Acme")
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                status=CallStatus.IN_PROGRESS, counterparty="+923001112222")
    yield tenant, call, ended
    live._sessions.pop(call.id, None)


async def _end(tenant, call, reason):
    import asyncio

    answer = await live._tool_handler(tenant, call)("end_call", {"reason": reason})
    await asyncio.sleep(0)
    return answer


@pytest.mark.parametrize("reason", [
    "the caller rang off", "Caller hung up", "the caller is silent",
    "no response from the caller", "the line disconnected",
])
async def test_rang_off_is_refused_while_the_caller_is_talking(ending, reason):
    tenant, call, ended = ending
    live._sessions[call.id] = _Line(spoke_ago=1.0)
    answer = await _end(tenant, call, reason)
    assert ended == [], "hung up on a caller who was talking"
    assert "still on the line" in answer


async def test_a_caller_who_really_went_quiet_can_be_let_go(ending):
    tenant, call, ended = ending
    live._sessions[call.id] = _Line(spoke_ago=20.0)
    await _end(tenant, call, "the caller is silent")
    assert ended == ["the caller is silent"]


async def test_a_normal_goodbye_is_not_held_up(ending):
    """They just said "Allah hafiz", so they were heard a moment ago — and
    that is exactly when ending is right."""
    tenant, call, ended = ending
    live._sessions[call.id] = _Line(spoke_ago=0.5)
    await _end(tenant, call, "the caller said goodbye")
    assert ended == ["the caller said goodbye"]


def test_the_model_is_no_longer_offered_rang_off_as_a_reason():
    from app.services.agent import tools

    described = tools.END_CALL["parameters"]["properties"]["reason"]["description"]
    assert "Never 'the caller rang off'" in described
