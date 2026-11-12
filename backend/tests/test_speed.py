"""
Faster to the first word, without moving the server.

From the logs: the outbound greeting came 1.6 to 3.2 seconds after pickup,
because the agent only started getting ready when the person answered,
although the phone had been ringing for seven to twelve seconds. And the
hold for the person's "hello" missed it six times in thirteen.
"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np
import pytest

from app.config import settings
from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live
from app.services.agent import session as session_module
from app.services.agent.audio_rate import FRAME_BYTES
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession
from tests.test_twilio import (  # noqa: F401 — fixtures, found by name
    CALL_SID, _calls, _fresh_twilio_spacing, _outbound_call, _status, dialer, twilio_api,
    twilio_line,
)


# ---------------------------------------------------------------------------
# E1: warm up while the phone rings
# ---------------------------------------------------------------------------

def test_placing_a_twilio_call_warms_the_agent_at_once(client, dialer, fake_db):
    resp = client.post("/api/calls", json={"to": "+923001112222", "lineId": "tw1"},
                       headers=dialer["headers"])
    assert resp.status_code == 201, resp.text
    [call] = _calls(fake_db)
    assert dialer["warmed"] == [call.id], "the warm-up waited for the pickup"


def test_a_human_dialer_call_is_never_warmed(client, dialer, fake_db):
    resp = client.post("/api/calls", json={"to": "+923001112222", "lineId": "tw1", "human": True},
                       headers=dialer["headers"])
    assert resp.status_code == 201, resp.text
    assert dialer["warmed"] == []


async def test_a_second_warm_up_for_the_same_call_does_nothing(fake_db, monkeypatch):
    """Placement warms; the answer webhook asks again and must not build a
    second agent."""
    built: list[str] = []

    async def prepared(_tenant, call):
        built.append(call.id)
        raise RuntimeError("stop here: the point is whether we got this far")

    monkeypatch.setattr(live, "_prepared", prepared)
    tenant = Tenant(phoneNumberId="t-1", name="Acme")
    call = Call(id="out-9", tenantId="t-1", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                counterparty="+923001112222")

    class Warm:
        closed = False

    live._sessions["out-9"] = Warm()
    try:
        await live.warm_up(tenant, call)
        assert built == [], "a second agent was built over the warmed one"
        live._warming["out-9"] = asyncio.Event()
        await live.warm_up(tenant, call)
        assert built == []
    finally:
        live._sessions.pop("out-9", None)
        live._warming.pop("out-9", None)


def test_no_answer_closes_the_warmed_session_after_the_row_is_saved(client, twilio_line, fake_db):
    """The status is saved before the session is finished: `finish` re-reads
    the row, and a warmed session racing the save lost the outcome."""
    from app.repositories import calls as call_repo

    _outbound_call(fake_db)
    twilio_line["live"].add("out1")
    seen: list[str] = []

    async def finish(_tenant, call_id, _reason=""):
        row = await call_repo.get_call("700", call_id)
        seen.append(row.status.value)

    import app.services.agent.live as live_module

    original = live_module.finish
    live_module.finish = finish
    try:
        assert _status(client, "no-answer").status_code == 200
    finally:
        live_module.finish = original
    assert seen == ["NO_ANSWER"], "finish ran before the outcome was saved"
    assert twilio_line["synced"] == 1


@pytest.mark.parametrize("outcome", ["busy", "failed", "canceled"])
def test_every_unanswered_outcome_finishes_the_session(client, twilio_line, fake_db, outcome):
    _outbound_call(fake_db)
    twilio_line["live"].add("out1")
    _status(client, outcome)
    assert twilio_line["finished"] == ["out1"]


def test_campaign_dialling_warms_on_a_twilio_line(fake_db, monkeypatch):
    from app.models.campaign import Campaign
    from app.services import campaign_runner, carriers

    warmed: list[str] = []

    async def warm_up(_tenant, call, **_kw):
        warmed.append(call.id)

    async def place(_tenant, call, _destination):
        call.provider_call_id = CALL_SID
        return {}

    monkeypatch.setattr(live, "warm_up", warm_up)
    monkeypatch.setattr(carriers, "place_call", place)
    tenant = Tenant(phoneNumberId="700", name="Acme", carrier="twilio", lineId="tw1")
    campaign = Campaign(tenantId="700", name="List", channel=Channel.PHONE)
    call = Call(tenantId="700", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                counterparty="+923001112222", lineId="tw1")

    async def run():
        await campaign_runner._dial(tenant, campaign, call, "+923001112222")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert warmed == [call.id]


# ---------------------------------------------------------------------------
# E2: the hello hold
# ---------------------------------------------------------------------------

def test_the_hello_hold_values_are_settings():
    from app.config import Settings

    assert Settings.model_fields["hello_wait_seconds"].default == 0.8
    assert Settings.model_fields["hello_done_seconds"].default == 0.4
    assert Settings.model_fields["hello_max_seconds"].default == 4.0
    assert session_module.HELLO_WAIT_SECONDS == settings.hello_wait_seconds == 0.8
    assert session_module.HELLO_DONE_SECONDS == settings.hello_done_seconds
    assert session_module.HELLO_MAX_SECONDS == settings.hello_max_seconds


def _frame(dbfs: float) -> bytes:
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


def test_a_quiet_hello_is_heard_through_the_raised_audio():
    """At -45 dBFS the raw frame sits under the voice floor; raised by the
    level control it is a caller saying hello, and the greeting waits."""
    from app.services.agent.caller_level import CallerLevel

    raw = _session()
    raw.hold_for_hello()
    for _ in range(10):
        raw.feed_caller(_frame(-45))
    assert raw._hello[1] == 0.0, "the raw audio should be under the floor"

    raised = _session()
    raised.caller_level = CallerLevel()
    raised.hold_for_hello()
    for _ in range(10):
        raised.feed_caller(_frame(-45))
    assert raised._hello[1] > 0.0, "the raised audio did not count as a hello"


# ---------------------------------------------------------------------------
# E3: turn-taking settings
# ---------------------------------------------------------------------------

def test_the_turn_taking_values_are_settings_with_the_new_defaults():
    from app.config import Settings

    assert Settings.model_fields["vad_silence_ms"].default == 500
    assert Settings.model_fields["vad_start_sensitivity"].default == "low"
    assert Settings.model_fields["vad_end_sensitivity"].default == "high"
    assert Settings.model_fields["gemini_temperature"].default is None


def test_the_session_config_carries_them(monkeypatch):
    from app.services.agent.gemini import GeminiLiveSession, sensitivity

    assert sensitivity("high") == "HIGH" and sensitivity("HIGH ") == "HIGH"
    assert sensitivity("low") == "LOW" and sensitivity("anything") == "LOW"

    session = _session()
    live_settings = session.live_settings
    assert live_settings.vad_silence_ms == 500
    assert live_settings.vad_start_sensitivity == "low"
    assert live_settings.vad_end_sensitivity == "high"
    assert live_settings.temperature is None

    config = GeminiLiveSession(live_settings, session.persona).session_config()
    detection = config["realtime_input_config"]["automatic_activity_detection"]
    assert detection["start_of_speech_sensitivity"] == "START_SENSITIVITY_LOW"
    assert detection["end_of_speech_sensitivity"] == "END_SENSITIVITY_HIGH"
    assert detection["silence_duration_ms"] == 500
    assert "temperature" not in config, "off unless the owner sets it"

    monkeypatch.setattr(settings, "gemini_temperature", 0.3)
    monkeypatch.setattr(settings, "vad_end_sensitivity", "low")
    tuned = _session()
    config = GeminiLiveSession(tuned.live_settings, tuned.persona).session_config()
    assert config["temperature"] == 0.3
    assert (config["realtime_input_config"]["automatic_activity_detection"]
            ["end_of_speech_sensitivity"] == "END_SENSITIVITY_LOW")


# ---------------------------------------------------------------------------
# E4: measured, per call
# ---------------------------------------------------------------------------

async def test_pickup_to_first_word_and_reply_latency_are_measured():
    session = _session()
    session.attach_transport(lambda _f: None)
    try:
        session.pickup_at -= 1.0                      # picked up a second ago
        session._to_line(_frame(-15))                 # the greeting starts
        assert 0.9 <= session.first_audio_at - session.pickup_at <= 1.3

        session.agent_audible_at = time.time() - 5    # the greeting ended a while ago
        session._to_line(session_module.SILENT_FRAME)
        session.feed_caller(_frame(-20))              # the caller asks something
        session.caller_spoke_at -= 0.7                # ...and stopped 0.7 s ago
        session._to_line(_frame(-15))                 # the reply begins
        summary = session.timing_summary()
    finally:
        await session.end("done")
    assert summary.startswith("first audio 1."), summary
    assert "reply after the caller stopped avg 0.7s max 0.7s over 1 turns" in summary


async def test_the_settled_log_line_carries_the_timing(fake_db, caplog):
    from app.repositories import calls as call_repo

    class Session:
        handler = type("H", (), {"value": "agent"})()
        frames_out = 10

        async def end(self, _reason):
            return None

        def transcript_text(self):
            return ""

        def timing_summary(self):
            return "first audio 0.4s after pickup, reply after the caller stopped avg 0.9s max 1.2s over 3 turns"

    tenant = Tenant(phoneNumberId="900", name="Acme", recordCalls=False)
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.IN_PROGRESS, counterparty="+923001112222", answeredAt=1.0)
    await call_repo.save_call(call)
    live._sessions[call.id] = Session()
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        await live.finish(tenant, call.id, "done")
    [line] = [r.getMessage() for r in caplog.records if "settled" in r.getMessage()]
    assert "first audio 0.4s after pickup" in line and "max 1.2s over 3 turns" in line


# ---------------------------------------------------------------------------
# E5: the free plan
# ---------------------------------------------------------------------------

def test_keepalive_runs_with_the_documented_setting(monkeypatch):
    from app.services import keepalive

    monkeypatch.setattr(settings, "public_base_url", "https://calling-agent-zpxl.onrender.com")
    monkeypatch.setattr(settings, "keepalive_seconds", 600)
    assert keepalive.should_run()
    assert keepalive.target() == "https://calling-agent-zpxl.onrender.com/health"
    monkeypatch.setattr(settings, "keepalive_seconds", 0)
    assert not keepalive.should_run()
    monkeypatch.setattr(settings, "keepalive_seconds", 600)
    monkeypatch.setattr(settings, "public_base_url", "")
    assert not keepalive.should_run()


def test_health_touches_no_database_and_reports_the_session_counts(client, monkeypatch):
    from app.db import supabase as supabase_module

    async def boom(*_a, **_k):
        raise AssertionError("/health reached the database")

    monkeypatch.setattr(supabase_module.supabase, "select", boom, raising=False)
    monkeypatch.setattr(supabase_module.supabase, "select_one", boom, raising=False)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    caps = body["capabilities"]
    assert caps["liveSessions"] == caps["callsInProgress"] == live.active()
    assert caps["callCapacity"] == settings.max_concurrent_calls
    assert isinstance(caps["atCapacity"], bool)
    assert not any("key" in k.lower() and v not in (True, False) for k, v in caps.items())
