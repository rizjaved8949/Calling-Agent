"""
From the third real test call: the goodbye that would not end the call.

The caller said "nahin, bahut shukriya. allaah haafiz.", the agent said its
closing line, and a mis-transcribed "And that's things? Good night, sis."
followed. The guard refused on that line, the hang-up was cancelled by the
same noise, nothing ended the call from the server, the dashboard's End
took twenty seconds, and earlier the agent had said "request main record
kar li hai" with no record_request behind it.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, tools, turns
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession
from tests.test_twilio import (  # noqa: F401 — fixtures, found by name
    _fresh_twilio_spacing, dialer, twilio_api, twilio_line, twilio_socket,
)

CLOSING = "BYD Pakistan ko call karne ka shukriya. Allah hafiz."
GOODBYE = "nahin, bahut shukriya. allaah haafiz."
NOISE = "And that's things? Good night, sis."
TAIL = [
    ("agent", "Phir BYD Roadside Assist +92 111-111-293 (24 hours) par call karein. "
              "Kya main aapki gaari ki location jaan sakti hoon?"),
    ("caller", GOODBYE),
    ("agent", CLOSING),
    ("caller", NOISE),
]


def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                  counterparty="+923011114604")
    return Call(**{**fields, **extra})


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", recordCalls=False)


class _Ear:
    def __init__(self):
        self.notes: list[str] = []

    def feed(self, _pcm):
        pass

    async def note(self, text):
        self.notes.append(text)


def _session(call_id="call-3") -> CallSession:
    session = CallSession(call_id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._agent = _Ear()
    return session


@pytest.fixture
def hangs(monkeypatch):
    hung: list[str] = []

    async def hang_up(_tenant, _call, reason, **_k):
        hung.append(reason)

    monkeypatch.setattr(live, "hang_up", hang_up)
    monkeypatch.setattr(live, "GOODBYE_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(live, "CLOSING_END_SECONDS", 0.3)
    monkeypatch.setattr(live, "TRANSCRIPT_WAIT_SECONDS", 0.3)
    yield hung
    live._sessions.clear()
    live._recorded.clear()


# ---------------------------------------------------------------------------
# 1. The guard judges the last meaningful line
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    NOISE, "Muito bela essa maquiagem que ti, o que aconteceu?", "hmm?", "ok.", "Good night, sis.",
    "uh huh", "?", "Thank you for the details. Bye.", "shukriya information ke liye, Allah hafiz",
    "bas itna hi, thanks for your help",
])
def test_noise_is_not_a_new_request(said):
    assert not turns.is_new_request(said), said


@pytest.mark.parametrize("said", [
    "C9 ki price batayein.",
    "mujhse pahle suman ne kamplent ki thi. uska kya hua?",
    "Meri auto 2 se dhua nikal raha hai. Aur jalne ki bu aa rahi hai. Kya karun?",
    "mujhe Alto 3 ka test draaiw buk karna hai.",
    "mujhe faimili ke lie gaari chaahie.",
    "SEAL ki range?",
    "aur SEAL ki price kya hai?",
    "theek hai shukriya, bas SEAL ki price bata dein",
])
def test_a_real_request_is_recognised(said):
    assert turns.is_new_request(said), said


def test_the_last_meaningful_line_is_judged_not_the_noise_after_it():
    assert turns.last_meaningful_caller_turn(TAIL) == (1, GOODBYE)
    assert turns.may_end(TAIL)
    assert turns.may_end(TAIL[:3])
    # A real question after the closing line still keeps the call open.
    assert not turns.may_end(TAIL[:3] + [("caller", "aur SEAL ki price kya hai?")])


def test_a_no_or_goodbye_after_the_agents_last_question_allows_the_end():
    asked = [("agent", "Kya main aapki gaari ki location jaan sakti hoon?")]
    assert turns.may_end(asked + [("caller", "nahin")])
    assert turns.may_end(asked + [("caller", "allaah haafiz")])
    assert turns.may_end(asked + [("caller", "nahin"), ("caller", "hmm?")])
    assert not turns.may_end(asked + [("caller", "Lahore, Gulberg mein hoon")])


async def test_end_call_is_allowed_after_the_goodbye_and_the_noise(hangs, monkeypatch):
    ended: list[str] = []

    async def after_goodbye(_tenant, _call, reason):
        ended.append(reason)

    monkeypatch.setattr(live, "_hang_up_after_goodbye", after_goodbye)
    call = _call()
    session = _session(call.id)
    for who, text in TAIL:
        session._note(who, text)
    live._sessions[call.id] = session
    answer = await live._tool_handler(_tenant(), call)("end_call", {"reason": "the caller said goodbye"})
    await asyncio.sleep(0)
    assert "The call is ending" in answer
    assert ended == ["the caller said goodbye"]


# ---------------------------------------------------------------------------
# 2. The hang-up is cancelled only by a real new request
# ---------------------------------------------------------------------------

async def _goodbye_then(hangs, said: str, caplog):
    call = _call()
    session = _session(call.id)
    session._note("caller", GOODBYE)
    session._note("agent", CLOSING)
    live._sessions[call.id] = session
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        task = asyncio.create_task(live._hang_up_after_goodbye(_tenant(), call, "the caller said goodbye"))
        await asyncio.sleep(0.05)
        session.caller_spoke_at = time.time()      # a voice on the line...
        if said:
            session._note("caller", said)          # ...and this is what it said
        await task
    return [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("said", [NOISE, "ok", "thank you", "good night", "bye", "allaah haafiz", ""])
async def test_noise_thanks_or_a_goodbye_back_do_not_cancel_the_hang_up(hangs, caplog, said):
    messages = await _goodbye_then(hangs, said, caplog)
    assert hangs == ["the caller said goodbye"], said
    assert any("after the goodbye, not a new request; ending" in m for m in messages)


async def test_a_real_question_after_the_goodbye_keeps_the_line(hangs, caplog):
    messages = await _goodbye_then(hangs, "aur SEAL ki price kya hai?", caplog)
    assert hangs == []
    assert any("asked something after the goodbye" in m and "SEAL ki price" in m for m in messages)


# ---------------------------------------------------------------------------
# 3. The server ends the call after the closing line
# ---------------------------------------------------------------------------

async def test_the_server_ends_the_call_after_the_closing_line(hangs, caplog):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    session._note("caller", GOODBYE)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note("agent", "BYD Pakistan ko call karne ka")
        session._note("agent", " shukriya. Allah hafiz.")
        await asyncio.sleep(0.1)
        session._note("caller", NOISE)                 # noise after it
        await asyncio.sleep(0.8)
    assert hangs == [live.CLOSING_END_REASON]
    assert any("ending the call from the server" in r.getMessage() for r in caplog.records)


async def test_the_server_ends_the_call_when_the_caller_says_nothing(hangs):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    session._note("agent", CLOSING)
    await asyncio.sleep(0.8)
    assert hangs == [live.CLOSING_END_REASON]


async def test_a_new_request_in_the_window_stops_the_server_end(hangs, caplog):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note("agent", CLOSING)
        await asyncio.sleep(0.1)
        session._note("caller", "C9 ki price batayein.")
        await asyncio.sleep(0.8)
    assert hangs == []
    assert any("the call goes on" in r.getMessage() for r in caplog.records)
    # A second closing line later re-arms it.
    session._note("agent", "SEALION 6 hamare range mein nahi hai.")
    session._note("caller", "theek hai, shukriya")
    session._note("agent", CLOSING)
    await asyncio.sleep(0.8)
    assert hangs == [live.CLOSING_END_REASON]


async def test_the_closing_watch_is_wired_into_a_real_session(fake_db, monkeypatch):
    """The sessions `live` builds carry the watch, so nothing here depends on
    a test wiring it by hand."""
    from app.services.agent import engines

    class Engine:
        def __init__(self, *a, **k):
            self.ready = asyncio.Event()
            self.ready.set()
            self.closed = asyncio.Event()
            self.connected = True

        live = True

        async def wait_until_ready(self, timeout=30.0): ...
        def close(self): self.closed.set()
        def feed(self, _p): ...
        async def say(self, _t): ...
        async def run(self): await self.closed.wait()

    monkeypatch.setattr(engines, "build", lambda *a, **k: Engine())

    async def settings_now(_tenant=None):
        return LiveSettings(model="m", api_key="k")

    async def persona(_tenant, _call=None, _timings=None):
        return AgentPersona(instructions="x", greeting="")

    monkeypatch.setattr(live, "live_settings_now", settings_now)
    monkeypatch.setattr(live, "persona_for", persona)
    call = _call()
    session = await live.start(_tenant(), call, lambda _f: None, keepalive=False)
    try:
        assert session.on_transcript is not None
        assert session.on_transcript.__qualname__.startswith("_transcript_watch")
    finally:
        await live.finish(_tenant(), call.id, "test over")


# ---------------------------------------------------------------------------
# 4. Ended at once; settled in the background; idle Twilio sockets closed
# ---------------------------------------------------------------------------

def test_the_dashboard_end_answers_at_once(client, dialer, fake_db, monkeypatch):
    from tests.test_twilio import _calls

    finished: list[str] = []

    async def slow_finish(_tenant, call_id, _reason=""):
        await asyncio.sleep(0.8)
        finished.append(call_id)

    monkeypatch.setattr(live, "finish", slow_finish)
    client.post("/api/calls", json={"to": "+923001112222", "lineId": "tw1"},
                headers=dialer["headers"])
    [call] = _calls(fake_db)
    live._sessions[call.id] = object()
    try:
        started = time.time()
        resp = client.post(f"/api/calls/{call.id}/end", headers=dialer["headers"])
        assert resp.status_code == 200, resp.text
        assert time.time() - started < 0.5, "the dashboard waited for the recording"
        assert resp.json()["status"] == "COMPLETED" and resp.json()["endedAt"]
    finally:
        live._sessions.pop(call.id, None)
    time.sleep(1.0)
    assert finished == [call.id], "the background settling never ran"


async def test_finish_marks_the_call_ended_before_the_recording_is_stored(fake_db, monkeypatch):
    from app.repositories import calls as call_repo
    from app.services import recordings

    seen: list[tuple[str, float | None]] = []

    async def slow_store(_tenant, call, _audio, _mime, transcode=True):
        row = await call_repo.get_call("t-1", call.id)
        seen.append((row.status.value, row.ended_at))
        await asyncio.sleep(0.05)
        return call

    monkeypatch.setattr(recordings, "store_audio", slow_store)

    class Session:
        handler = type("H", (), {"value": "agent"})()
        frames_out = 10

        async def end(self, _reason):
            return b"RIFF-audio"

        def transcript_text(self):
            return "caller: hi"

    call = _call(status=CallStatus.IN_PROGRESS, answeredAt=time.time() - 30)
    await call_repo.save_call(call)
    live._sessions[call.id] = Session()
    await live.finish(_tenant(), call.id, "caller hung up")
    assert seen == [("COMPLETED", seen[0][1])] and seen[0][1] is not None


def test_a_twilio_socket_that_goes_silent_is_closed_and_settled(client, twilio_socket, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app.api.routes import media
    from tests.test_twilio import _socket_for, _start

    monkeypatch.setattr(media, "TWILIO_IDLE_SECONDS", 0.3)
    path, token, call_id = _socket_for(client)
    with client.websocket_connect(path) as socket:
        _start(socket, token)
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()                  # nothing more arrives: closed by us
    assert twilio_socket["finished"] == [call_id]


# ---------------------------------------------------------------------------
# 5. "Recorded" with nothing recorded
# ---------------------------------------------------------------------------

CLAIM = "Theek hai, main samajh gaya. Saturday ke liye request main record kar li hai."


async def test_a_false_recorded_claim_is_corrected_on_the_next_turn(hangs, caplog):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note("agent", "Theek hai, main samajh gaya. Saturday ke liye request main")
        session._note("agent", " record kar li hai.")
        await asyncio.sleep(0.05)
    assert session._agent.notes == [live.NOT_RECORDED_CORRECTION]
    assert "NOT recorded anything" in live.NOT_RECORDED_CORRECTION
    [warning] = [r.getMessage() for r in caplog.records if "claimed a request was recorded" in r.getMessage()]
    assert "record kar li hai" in warning
    # More of the same turn: no second correction.
    session._note("agent", " Slots ki confirmation location se call karke bata denge.")
    await asyncio.sleep(0.05)
    assert len(session._agent.notes) == 1


async def test_a_true_recorded_claim_is_left_alone(hangs):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    live._recorded[call.id] = ["TD-20261112-01"]
    session._note("agent", "Aapki request record kar li hai, reference TD-20261112-01.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


@pytest.mark.parametrize("said, claims", [
    (CLAIM, True),
    ("Aapki complaint darj kar li gayi hai.", True),
    ("Your request has been recorded.", True),
    ("Main aapki request record kar sakti hoon. Naam bata dein?", False),
    ("Abhi record nahi hui, pehle mobile number bata dein.", False),
    ("ATTO 3 ki price 89 lakh 90 hazar hai.", False),
])
def test_what_counts_as_claiming_a_record(said, claims):
    assert live._claims_recorded(said) is claims


async def test_record_request_tells_the_agent_what_to_ask_and_never_to_claim(fake_db):
    """The exact details from the call: only a day for the time."""
    call = _call()
    live._sessions[call.id] = _session(call.id)
    live._sessions[call.id]._note(
        "caller", "Mera naam Ali hai. Aur phone number 03001234567. Aur Lahore ke andar "
                  "mujhe arrange karwani hai. Aur time Saturday hai.")
    try:
        answer = await live._tool_handler(_tenant(), call)("record_request", {
            "type": "test_drive", "name": "Ali", "mobile": "03001234567", "city": "Lahore",
            "vehicle_model": "ATTO 3", "preferred_times": ["Saturday"],
            "details": "test drive"})
    finally:
        live._sessions.clear()
    assert answer.startswith("Not recorded yet. Read this back"), "a day alone must be enough"
    assert "Recorded" not in answer


def test_the_tool_says_a_day_alone_is_enough():
    description = tools.RECORD_REQUEST["description"]
    assert "If the caller gives only a day, record it with that day" in description
    assert "a missing time is not a reason to skip recording" in description
