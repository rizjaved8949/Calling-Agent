"""
A call that happened must leave a recording behind.

Every transport reaches the same recorder, but each arrives by its own route —
a carrier websocket, Meta's WebRTC bridge, an employee's browser — and a gap
in any one of them is invisible until somebody goes looking for audio that was
never kept. So each route is pinned here, along with the two ways the audio
was being thrown away after it had already been captured.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.services.agent.audio_rate import FRAME_BYTES
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession, Handler

FRAME = b"\x01\x02" * (FRAME_BYTES // 2)


def _session(*, human: bool = False, record: bool = True) -> CallSession:
    return CallSession(
        "rec-call",
        send_to_caller=lambda _frame: None,
        live_settings=LiveSettings(model="m", api_key=""),
        persona=AgentPersona(instructions="", greeting=""),
        record=record,
        human=human,
        on_transcript=lambda who, text: None,
    )


def _seconds(wav: bytes) -> float:
    """Length of a 24 kHz mono PCM16 WAV, from its size."""
    return max(0.0, (len(wav) - 44) / (24_000 * 2))


async def test_the_caller_is_recorded_on_a_carrier_call():
    """SIM and WhatsApp both arrive here: the bridge calls feed_caller too."""
    session = _session()
    for _ in range(50):          # one second at 16 kHz
        session.feed_caller(FRAME)
    audio = await session.end("done")

    assert audio, "a call with a second of caller audio recorded nothing"
    assert audio[:4] == b"RIFF"
    assert _seconds(audio) >= 0.9


async def test_the_employee_is_recorded_on_a_dialer_call():
    """A human call has no model in it, so nothing on the agent's own path
    runs. The operator's voice still has to reach the recording."""
    session = _session(human=True)
    assert session.handler is Handler.OPERATOR

    for _ in range(50):
        session.feed_caller(FRAME)
        session.feed_operator(FRAME)
    audio = await session.end("the employee hung up")

    assert audio, "a dialer call recorded nothing"
    assert _seconds(audio) >= 0.9


async def test_both_sides_land_on_one_timeline():
    """Not two files: the recording is the conversation, so a reviewer hears
    the caller and the answer in the order they happened."""
    session = _session()
    for _ in range(25):
        session.feed_caller(FRAME)
    session._on_agent_audio(b"\x03\x04" * 12_000, 24_000)
    for _ in range(25):
        session.feed_caller(FRAME)
    audio = await session.end("done")

    assert audio and _seconds(audio) >= 0.9


async def test_the_recording_survives_a_model_that_will_not_shut_down():
    """The bug: closing the model was unprotected and ran *before* the
    recording was harvested, so an exception there threw away a conversation
    that was already safely on disk, and the call was marked as having no
    audio at all."""
    session = _session()
    for _ in range(50):
        session.feed_caller(FRAME)

    class Stubborn:
        def close(self):
            raise RuntimeError("the socket was already gone")

    session._agent = Stubborn()
    audio = await session.end("done")

    assert audio, "the recording was lost because the model would not close"
    assert _seconds(audio) >= 0.9


async def test_ending_twice_does_not_lose_the_first_recording():
    """`finish` is called from a socket close, a webhook and a `finally`, so
    arriving twice is normal."""
    session = _session()
    for _ in range(50):
        session.feed_caller(FRAME)

    first = await session.end("done")
    second = await session.end("done again")

    assert first, "the first ending produced nothing"
    assert second is None, "the second ending must not produce a rival file"


async def test_a_storage_failure_does_not_also_lose_the_call(fake_db, monkeypatch):
    """Storing is the one step that reaches the network. A bucket having a bad
    minute used to take the row down with it: no status, no transcript, and a
    record that said the call had never ended."""
    import time as _time

    from app.models.call import Call, CallStatus, Channel, Direction, RecordingState
    from app.repositories import calls as call_repo
    from app.services import recordings as recording_service
    from app.models.tenant import Tenant
    from app.services.agent import live

    tenant = Tenant(phoneNumberId="870", displayName="Acme", recordCalls=True)
    call = Call(
        id="store-fails",
        tenantId=tenant.phone_number_id,
        channel=Channel.PHONE,
        direction=Direction.INBOUND,
        status=CallStatus.IN_PROGRESS,
        counterparty="+100",
        startedAt=_time.time() - 30,
        answeredAt=_time.time() - 30,
        recordingState=RecordingState.PENDING,
    )
    await call_repo.save_call(call)

    session = _session()
    for _ in range(50):
        session.feed_caller(FRAME)
    live._sessions[call.id] = session

    async def explode(*_a, **_k):
        raise RuntimeError("the bucket is having a bad minute")

    monkeypatch.setattr(recording_service, "store_audio", explode)

    await live.finish(tenant, call.id, "the caller hung up")

    settled = await call_repo.get_call(tenant.phone_number_id, call.id)
    assert settled.status is CallStatus.COMPLETED, "the call lost its status"
    assert settled.ended_at, "the call was left looking like it never ended"
    assert settled.recording_state is RecordingState.FAILED
    assert settled.recording_error, "a failure with no reason is not reportable"
    live._sessions.pop(call.id, None)


async def test_nothing_is_recorded_when_the_company_asked_for_none():
    """Recording is a setting, and a company that turned it off must not get a
    file anyway."""
    session = _session(record=False)
    for _ in range(50):
        session.feed_caller(FRAME)
    assert await session.end("done") is None
