"""
An outbound call's greeting waits for the person's "hello"; an inbound
caller is greeted at once.

From a real outbound call: the agent greeted one second after pickup, before
anyone had spoken, and talked straight over the person's "hello?".
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from app.services.agent import session as session_module
from app.services.agent.audio_rate import FRAME_BYTES
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession
# The Twilio fixtures, reused for the real socket path.
from tests.test_twilio import (CALL_SID, _outbound_call, _start, _stream_of,  # noqa: F401
                               _twilio_post, twilio_line, twilio_socket)


def _voice(dbfs: float = -30.0) -> bytes:
    samples = FRAME_BYTES // 2
    wave = 10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * 220 * np.arange(samples) / 16000)
    return (wave * 32767).astype("<i2").tobytes()


QUIET = bytes(FRAME_BYTES)
GREETING = _voice(-15) * 25                       # half a second of greeting


class _Ear:
    def __init__(self):
        self.heard: list[bytes] = []

    def feed(self, pcm: bytes) -> None:
        self.heard.append(pcm)


@pytest.fixture
def line():
    """A session with its greeting already generated and queued, the way a
    warmed-up session has it when the person picks up."""
    sent: list[bytes] = []
    session = CallSession("call-1", send_to_caller=sent.append,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._agent = _Ear()
    session._pacer.offer(GREETING)
    session.sent = sent
    return session


def _play(session: CallSession, seconds: float) -> None:
    """Run the line's clock for `seconds`, as the pacer task would."""
    end = time.time() + seconds
    while time.time() < end:
        session._check_hello()
        session._pacer.tick()
        time.sleep(0.005)


def _ago(session: CallSession, seconds: float) -> None:
    """Pretend the hold started `seconds` ago."""
    session._hello[0] -= seconds


# ---------------------------------------------------------------------------
# Held, then released
# ---------------------------------------------------------------------------

def test_without_a_hold_the_greeting_plays_at_once(line):
    """Inbound: nothing waits."""
    _play(line, 0.2)
    assert line.sent, "an inbound caller was kept waiting for the greeting"


def test_the_greeting_waits_while_nobody_has_spoken(line):
    line.hold_for_hello()
    _play(line, 0.2)
    assert line.sent == [], "the greeting started before anyone said hello"
    assert line.waiting_for_hello


def test_nobody_speaking_gets_the_greeting_after_the_wait(line):
    line.hold_for_hello()
    _ago(line, session_module.HELLO_WAIT_SECONDS)
    _play(line, 0.2)
    assert line.sent and not line.waiting_for_hello


def test_the_greeting_follows_the_hello_as_soon_as_it_ends(line):
    picked_up = time.time()
    line.hold_for_hello()
    for _ in range(20):                           # 0.4 s of "hello?"
        line.feed_caller(_voice())
    _play(line, 0.1)
    assert line.sent == [], "the greeting talked over the hello"
    line._hello[1] -= session_module.HELLO_DONE_SECONDS   # and then they stop
    _play(line, 0.1)
    assert line.sent, "the greeting did not follow the hello"
    # Well before the 1.5 s a silent line waits: right after the hello.
    assert time.time() - picked_up < session_module.HELLO_WAIT_SECONDS


def test_someone_still_talking_at_the_wait_is_not_talked_over(line):
    line.hold_for_hello()
    _ago(line, session_module.HELLO_WAIT_SECONDS + 0.2)
    line.feed_caller(_voice())                    # still mid-sentence
    _play(line, 0.1)
    assert line.sent == []


def test_a_noisy_line_cannot_hold_the_greeting_forever(line):
    line.hold_for_hello()
    _ago(line, session_module.HELLO_MAX_SECONDS)
    line.feed_caller(_voice())                    # noise that never stops
    _play(line, 0.1)
    assert line.sent, "the greeting was held past the limit"


def test_the_whole_greeting_plays_from_its_first_word(line):
    line.hold_for_hello()
    _ago(line, session_module.HELLO_WAIT_SECONDS)
    _play(line, 0.7)
    assert b"".join(line.sent)[:len(GREETING)] == GREETING


# ---------------------------------------------------------------------------
# What the model hears
# ---------------------------------------------------------------------------

def test_the_model_does_not_hear_the_hello_it_is_already_answering(line):
    """Heard, the model would answer the hello as a turn of its own, and the
    person would be greeted twice."""
    line.hold_for_hello()
    for _ in range(10):
        line.feed_caller(_voice())
    assert line._agent.heard == [QUIET] * 10


def test_after_the_greeting_the_model_hears_them_normally(line):
    line.hold_for_hello()
    _ago(line, session_module.HELLO_WAIT_SECONDS)
    _play(line, 0.05)
    spoken = _voice()
    line.feed_caller(spoken)
    assert line._agent.heard[-1] == spoken


def test_the_recording_keeps_the_hello(line):
    recorded: list[bytes] = []
    line._recorder = type("R", (), {"feed": lambda _s, side, pcm: recorded.append(pcm)})()
    line._caller_to_record = type("C", (), {"process": lambda _s, pcm: pcm})()
    line.hold_for_hello()
    hello = _voice()
    line.feed_caller(hello)
    assert recorded == [hello]


# ---------------------------------------------------------------------------
# Which calls wait
# ---------------------------------------------------------------------------

def test_a_twilio_outbound_call_waits_for_the_hello(client, twilio_socket, fake_db):
    _outbound_call(fake_db)
    answered = _twilio_post(client, "/api/webhooks/twilio/outbound/tw1/out1",
                            {"CallSid": CALL_SID, "CallStatus": "in-progress",
                             "Direction": "outbound-api"})
    url, token = _stream_of(answered.text)
    with client.websocket_connect(url.removeprefix("wss://api.example.com")) as socket:
        _start(socket, token, call_sid=CALL_SID)
        socket.send_json({"event": "stop", "streamSid": "MZ" + "1" * 32,
                          "stop": {"callSid": CALL_SID}})
    [(call_id, session)] = twilio_socket["started"]
    assert call_id == "out1" and session.wait_for_hello is True


async def test_a_dialer_call_with_a_person_on_it_is_never_held(fake_db, monkeypatch):
    """No greeting to hold, and holding would mute the employee."""
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.models.tenant import Tenant
    from app.services.agent import live

    async def settings_now(_tenant=None):
        return LiveSettings(model="m", api_key="k")

    monkeypatch.setattr(live, "live_settings_now", settings_now)
    tenant = Tenant(phoneNumberId="900", name="Acme", recordCalls=False)
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                status=CallStatus.RINGING, counterparty="+923001112222", mode="human")
    session = await live.start(tenant, call, lambda _f: None, keepalive=False,
                               wait_for_hello=True)
    try:
        assert not session.waiting_for_hello
    finally:
        await live.finish(tenant, call.id, "test over")
