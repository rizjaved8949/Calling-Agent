"""
The OpenAI Realtime engine, driven against a server that speaks its protocol.

There is no OpenAI key on this deployment, so no real call can be placed. What
*can* be pinned is everything between `session.py` and the wire: that the
session announces itself correctly, that caller audio is resampled to the rate
this API wants, that audio, transcripts and interruptions come back out
through the same callbacks Gemini uses, and that a tool call is answered and
the model told to speak again.

A scripted websocket server stands in for OpenAI. It is not a mock of our own
code — it is the other end of the protocol, so the thing under test is the
real session class talking real frames.
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
import websockets

from app.services.agent.gemini import AgentPersona, AgentUnavailable, LiveSettings
from app.services.agent.openai_realtime import (
    OPENAI_RATE, OpenAIRealtimeSession, VOICES, _tools_for_openai,
)


class FakeRealtime:
    """The other end of the socket, saying whatever a test needs it to."""

    def __init__(self, script=None):
        self.received: list[dict] = []
        self.script = script
        self.server = None
        self.url = ""
        self.headers: dict = {}

    async def __aenter__(self):
        async def handle(socket):
            self.headers = dict(getattr(socket, "request", None).headers) \
                if getattr(socket, "request", None) else {}
            try:
                async for raw in socket:
                    event = json.loads(raw)
                    self.received.append(event)
                    if self.script:
                        await self.script(self, socket, event)
            except websockets.exceptions.ConnectionClosed:
                pass

        self.server = await websockets.serve(handle, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    def sent(self, kind: str) -> list[dict]:
        return [e for e in self.received if e.get("type") == kind]


def _session(url, persona=None, **callbacks):
    """A session pointed at the fake server instead of api.openai.com."""
    import app.services.agent.openai_realtime as module

    module.WS_URL = url
    return OpenAIRealtimeSession(
        LiveSettings(model="gpt-realtime", api_key="sk-test", engine="openai"),
        persona or AgentPersona(instructions="You are a test.", voice="alloy"),
        label="test",
        **callbacks,
    )


async def _run_briefly(session, doing=None, seconds=1.5):
    """Run the session, do something, then close it."""
    task = asyncio.create_task(session.run())
    try:
        await asyncio.wait_for(session.wait_until_ready(), timeout=seconds)
        if doing:
            await doing()
        await asyncio.sleep(0.25)
    finally:
        session.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, AgentUnavailable, Exception):
            pass


# ---------------------------------------------------------------------------
# Shape, without a socket
# ---------------------------------------------------------------------------


def test_our_tool_catalogue_is_a_rename_not_a_translation():
    """The catalogue is written once in Gemini's shape. If this ever needs
    real translation the two will drift and a tool will quietly stop firing."""
    from app.services.agent import tools

    converted = _tools_for_openai(tools.for_call(can_send_whatsapp=True))
    assert converted, "no tools came through"
    for tool in converted:
        assert tool["type"] == "function"
        assert tool["name"] and tool["description"]
        assert isinstance(tool["parameters"], dict)
    assert {t["name"] for t in converted} == {
        t["name"] for t in tools.for_call(can_send_whatsapp=True)
    }


def test_a_tool_without_parameters_still_gets_a_schema():
    """An empty `parameters` is rejected by the API, so it is filled in."""
    assert _tools_for_openai([{"name": "x", "description": "d"}])[0]["parameters"] == {
        "type": "object", "properties": {},
    }


def test_a_nameless_tool_is_dropped_rather_than_sent():
    assert _tools_for_openai([{"description": "no name"}]) == []


def test_the_engine_picker_returns_the_right_class():
    from app.services.agent import engines
    from app.services.agent.gemini import GeminiLiveSession

    persona = AgentPersona(instructions="x")
    openai = engines.build(
        LiveSettings(model="m", api_key="k", engine="openai"), persona)
    gemini = engines.build(
        LiveSettings(model="m", api_key="k", engine="gemini"), persona)
    assert isinstance(openai, OpenAIRealtimeSession)
    assert isinstance(gemini, GeminiLiveSession)


def test_an_unknown_engine_falls_back_rather_than_failing():
    """A typo in a settings screen must not leave a caller on a silent line."""
    from app.services.agent import engines
    from app.services.agent.gemini import GeminiLiveSession

    built = engines.build(
        LiveSettings(model="m", api_key="k", engine="wibble"),
        AgentPersona(instructions="x"))
    assert isinstance(built, GeminiLiveSession)


# ---------------------------------------------------------------------------
# The handshake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_announces_the_session_the_way_the_api_expects():
    async with FakeRealtime() as fake:
        session = _session(fake.url, AgentPersona(
            instructions="You are Ayesha.", voice="sage", greeting="Hello there.",
        ))
        await _run_briefly(session)

    updates = fake.sent("session.update")
    assert updates, "no session.update was sent"
    body = updates[0]["session"]
    assert body["instructions"] == "You are Ayesha."
    assert body["voice"] == "sage"
    assert body["input_audio_format"] == "pcm16"
    assert body["output_audio_format"] == "pcm16"
    assert body["input_audio_transcription"]["model"]
    assert body["turn_detection"]["type"] == "server_vad"


@pytest.mark.asyncio
async def test_the_turn_detector_is_deliberately_less_eager():
    """On a phone line the agent's own voice leaks back through the handset,
    and a sensitive detector reads that as the caller and cuts her off."""
    async with FakeRealtime() as fake:
        await _run_briefly(_session(fake.url))

    detection = fake.sent("session.update")[0]["session"]["turn_detection"]
    assert detection["threshold"] > 0.5
    assert detection["prefix_padding_ms"] == 300
    assert detection["silence_duration_ms"] == 700


@pytest.mark.asyncio
async def test_a_gemini_voice_name_does_not_fail_the_call():
    """A company moved between engines would otherwise have every call
    refused over the name of a voice."""
    async with FakeRealtime() as fake:
        await _run_briefly(_session(fake.url, AgentPersona(
            instructions="x", voice="Kore",  # a Gemini voice
        )))

    assert fake.sent("session.update")[0]["session"]["voice"] in VOICES


@pytest.mark.asyncio
async def test_it_speaks_first_rather_than_waiting_for_the_caller():
    """Left to itself the model waits, which on an answered call is two
    people listening to each other in silence."""
    async with FakeRealtime() as fake:
        await _run_briefly(_session(fake.url, AgentPersona(
            instructions="x", greeting="Thank you for calling Acme.",
        )))

    greetings = fake.sent("response.create")
    assert greetings, "nothing was said first"
    assert "Thank you for calling Acme." in greetings[0]["response"]["instructions"]


@pytest.mark.asyncio
async def test_no_greeting_means_no_opening_turn():
    async with FakeRealtime() as fake:
        await _run_briefly(_session(fake.url, AgentPersona(instructions="x")))
    assert fake.sent("response.create") == []


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_audio_is_resampled_to_the_rate_this_api_wants():
    """A phone line carries 16 kHz and this API listens at 24. Sent through
    unconverted, every caller sounds slowed down and is misheard."""
    async with FakeRealtime() as fake:
        session = _session(fake.url)

        async def feed():
            # 100 ms of 16 kHz PCM16 = 1600 samples = 3200 bytes.
            session.feed(b"\x00\x01" * 1600)
            await asyncio.sleep(0.3)

        await _run_briefly(session, feed)

    frames = fake.sent("input_audio_buffer.append")
    assert frames, "no audio reached the model"
    sent = base64.b64decode(frames[0]["audio"])
    # 100 ms at 24 kHz is 2400 samples: half as many again. The resampler's
    # filter delay makes it approximate, so this is a ratio rather than equality.
    ratio = (len(sent) / 2) / 1600
    assert 1.3 < ratio < 1.7, f"ratio was {ratio}, expected about 1.5"


@pytest.mark.asyncio
async def test_the_agents_audio_comes_back_at_the_rate_it_says():
    heard: list[tuple[bytes, int]] = []

    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({
                "type": "response.audio.delta",
                "delta": base64.b64encode(b"\x11\x22" * 50).decode(),
            }))

    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_audio=lambda d, r: heard.append((d, r))))

    assert heard, "no audio came back"
    assert heard[0][0] == b"\x11\x22" * 50
    assert heard[0][1] == OPENAI_RATE


@pytest.mark.asyncio
async def test_feeding_before_the_socket_is_up_is_dropped_not_queued():
    """The opening of a call is silence anyway, and a queue here would play
    the first second of the call several seconds late."""
    session = _session("ws://127.0.0.1:1")  # nothing listening
    session.feed(b"\x00" * 640)  # must not raise
    assert not session.live


# ---------------------------------------------------------------------------
# Transcripts and interruption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_sides_are_transcribed():
    """Without this only the recording survives, and a call cannot be read."""
    lines: list[tuple[str, str]] = []

    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({
                "type": "response.audio_transcript.delta", "delta": "Good morning.",
            }))
            await socket.send(json.dumps({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "Is this the clinic?",
            }))

    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_transcript=lambda who, t: lines.append((who, t))))

    assert ("agent", "Good morning.") in lines
    assert ("caller", "Is this the clinic?") in lines


@pytest.mark.asyncio
async def test_the_caller_cutting_in_is_reported():
    """The model stops by itself; whatever is queued downstream has to go too,
    or the agent talks over them."""
    cut = []

    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({"type": "input_audio_buffer.speech_started"}))

    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_interrupted=lambda: cut.append(True)))

    assert cut == [True]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_call_is_run_and_answered_and_the_model_told_to_resume():
    """Unlike Gemini it does not speak again by itself, so the `response.create`
    afterwards is what stops the caller listening to nothing."""
    asked: list[tuple[str, dict]] = []

    async def on_tool(name, args):
        asked.append((name, args))
        return "sent"

    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({
                "type": "response.function_call_arguments.done",
                "call_id": "call-1", "name": "send_whatsapp_message",
                "arguments": json.dumps({"message": "the fees"}),
            }))

    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_tool=on_tool))

    assert asked == [("send_whatsapp_message", {"message": "the fees"})]
    answers = fake.sent("conversation.item.create")
    assert answers, "the tool call was never answered"
    item = answers[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call-1"
    assert "sent" in item["output"]
    # And told to carry on speaking.
    assert fake.sent("response.create")


@pytest.mark.asyncio
async def test_a_tool_that_raises_is_still_answered():
    """The model waits on the call id. A missing response leaves the
    conversation stalled with the caller listening to nothing."""
    async def on_tool(name, args):
        raise RuntimeError("the database is down")

    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({
                "type": "response.function_call_arguments.done",
                "call_id": "call-2", "name": "end_call", "arguments": "{}",
            }))

    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_tool=on_tool))

    answers = fake.sent("conversation.item.create")
    assert answers and answers[0]["item"]["call_id"] == "call-2"
    assert "could not be looked up" in answers[0]["item"]["output"]


@pytest.mark.asyncio
async def test_unreadable_tool_arguments_do_not_break_the_call():
    seen: list[dict] = []

    async def on_tool(name, args):
        seen.append(args)
        return "ok"

    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({
                "type": "response.function_call_arguments.done",
                "call_id": "c", "name": "end_call", "arguments": "{not json",
            }))

    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_tool=on_tool))

    assert seen == [{}]


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_model_raises_rather_than_hanging():
    """A caller holding a line with nobody on it is worse than a call that
    ends with an explanation."""
    session = _session("ws://127.0.0.1:1")
    with pytest.raises(AgentUnavailable):
        await session.run()


@pytest.mark.asyncio
async def test_a_failed_connection_never_leaves_a_waiter_hanging():
    session = _session("ws://127.0.0.1:1")
    task = asyncio.create_task(session.run())
    await asyncio.wait_for(session.wait_until_ready(timeout=5), timeout=5)
    assert not session.live
    with pytest.raises(AgentUnavailable):
        await task


@pytest.mark.asyncio
async def test_an_error_frame_is_logged_rather_than_ending_the_call():
    async def script(fake, socket, event):
        if event.get("type") == "session.update":
            await socket.send(json.dumps({
                "type": "error",
                "error": {"code": "rate_limit_exceeded", "message": "slow down"},
            }))
            await socket.send(json.dumps({
                "type": "response.audio.delta",
                "delta": base64.b64encode(b"\x00\x01").decode(),
            }))

    heard = []
    async with FakeRealtime(script) as fake:
        await _run_briefly(_session(fake.url, on_audio=lambda d, r: heard.append(d)))

    # Still carried on afterwards.
    assert heard
