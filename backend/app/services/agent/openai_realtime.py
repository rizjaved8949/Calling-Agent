"""
An OpenAI Realtime session: audio in, the agent's voice out.

Written to exactly the interface `GeminiLiveSession` presents — same
constructor, same `feed`/`say`/`run`/`close`, same callbacks — because
`session.py` holds one of these per call and should not have to know which
company is on which engine. Swapping the engine is picking a different class.

Two differences from Gemini, both handled here so nothing downstream changes:

* **Rate.** Gemini listens at 16 kHz, which is what a phone line carries, so
  caller audio needed no conversion. OpenAI listens at 24 kHz, so it is
  upsampled on the way in. Both speak at 24 kHz, so the reply path is
  identical and `session.py` is untouched.
* **Turn-taking.** Gemini's detector is configured by sensitivity; OpenAI's by
  a threshold. The padding and silence values are the same ones, and for the
  same reason: on a phone line the agent's own voice leaks back through the
  caller's handset, and a sensitive detector reads that as the caller speaking
  and cuts her off mid-sentence.

## What has and has not been proven

Every line below is exercised by tests against a scripted server that speaks
the Realtime protocol — the handshake, the audio path, transcripts,
interruption, tool calls and their responses. What has *not* happened is a
real phone call on a real OpenAI key, because there is no OpenAI key on this
deployment to make one with. Until someone makes that call this is working
code against a documented protocol, which is not the same as a working call,
and the engine picker says so.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Awaitable, Callable

from .audio_rate import RateConverter
from .gemini import AgentPersona, AgentUnavailable, LiveSettings

log = logging.getLogger(__name__)

#: What the Realtime API means by `pcm16`: 24 kHz, mono, little-endian.
OPENAI_RATE = 24_000
#: A phone line carries 16 kHz, so caller audio is upsampled 2:3 on the way in.
PHONE_TO_OPENAI = (3, 2)

WS_URL = "wss://api.openai.com/v1/realtime"

#: Voices the Realtime API offers. Used to fall back rather than fail when a
#: company has picked a Gemini voice and been moved to this engine.
VOICES = (
    "alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage",
    "shimmer", "verse",
)
DEFAULT_VOICE = "alloy"


def _tools_for_openai(declarations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our tool catalogue, in the shape this API wants.

    The catalogue is written in Gemini's `function_declarations` form. OpenAI
    takes the same three fields with a `type` beside them, so this is a
    rename rather than a translation — which is why the catalogue stays in one
    place instead of being written twice and drifting.
    """
    return [
        {
            "type": "function",
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        }
        for tool in declarations
        if tool.get("name")
    ]


class OpenAIRealtimeSession:
    """One conversation with the model.

    Callbacks rather than queues, so the caller decides what to do with audio
    the moment it arrives — a phone bridge paces it, a recorder writes it, a
    test script appends it to a buffer.
    """

    def __init__(
        self,
        settings: LiveSettings,
        persona: AgentPersona,
        *,
        on_audio: Callable[[bytes, int], None] | None = None,
        on_transcript: Callable[[str, str], None] | None = None,
        on_interrupted: Callable[[], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
        label: str = "session",
    ) -> None:
        self.settings = settings
        self.persona = persona
        self.on_audio = on_audio
        self.on_transcript = on_transcript
        self.on_interrupted = on_interrupted
        self.on_tool = on_tool
        self.label = label

        self._socket: Any = None
        self._closed = False
        self._ready = asyncio.Event()
        self._greeted = False
        self._sends: set[asyncio.Task] = set()
        self._to_model = RateConverter(*PHONE_TO_OPENAI)

    # ---- state -----------------------------------------------------------

    @property
    def live(self) -> bool:
        return self._socket is not None and not self._closed

    async def wait_until_ready(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    def close(self) -> None:
        self._closed = True

    # ---- the caller's side ------------------------------------------------

    def feed(self, pcm16: bytes) -> None:
        """A frame of caller audio, 16 kHz PCM16 mono, as every transport sends.

        Fire and forget: a frame that cannot be sent is dropped rather than
        awaited, because blocking the caller's audio path to retry one 20 ms
        frame costs more than the frame was worth.
        """
        if self._closed or not pcm16:
            return
        socket = self._socket
        if socket is None:
            return  # not up yet; the opening of a call is silence anyway
        # Resampled here, on the audio thread's task, because the converter
        # keeps filter state and must see every frame in order.
        upsampled = self._to_model.process(pcm16)
        if not upsampled:
            return
        task = asyncio.create_task(self._send(socket, {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(upsampled).decode("ascii"),
        }))
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)

    async def _send(self, socket: Any, event: dict[str, Any]) -> None:
        try:
            await socket.send(json.dumps(event))
        except Exception as exc:  # noqa: BLE001 — a dropped frame must not end the call
            if not self._closed:
                log.debug("%s: event not sent (%s)", self.label, exc)

    async def say(self, text: str) -> None:
        """Make the agent speak something now, without waiting to be spoken to.

        This is how a call opens. Left to itself the model waits for the caller
        first, which on an answered call is two people listening to each other
        in silence.
        """
        socket = self._socket
        if socket is None or self._closed:
            return
        await self._send(socket, {
            "type": "response.create",
            "response": {"instructions": text},
        })

    # ---- running ----------------------------------------------------------

    def _session_config(self) -> dict[str, Any]:
        voice = (self.persona.voice or "").strip().lower()
        if voice not in VOICES:
            # A company that chose a Gemini voice and was moved here would
            # otherwise have every call refused over the name of a voice.
            if voice:
                log.info("%s: %r is not an OpenAI voice, using %s",
                         self.label, self.persona.voice, DEFAULT_VOICE)
            voice = DEFAULT_VOICE

        session: dict[str, Any] = {
            "modalities": ["audio", "text"],
            "instructions": self.persona.instructions,
            "voice": voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            # Both sides transcribed, which is what makes a call reviewable
            # afterwards — without it only the recording survives.
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": {
                "type": "server_vad",
                # Higher than the default on purpose: see the module docstring.
                "threshold": 0.6,
                "prefix_padding_ms": self.settings.vad_prefix_ms,
                "silence_duration_ms": self.settings.vad_silence_ms,
            },
        }
        if self.persona.tools:
            session["tools"] = _tools_for_openai(self.persona.tools)
            session["tool_choice"] = "auto"
        return session

    async def run(self) -> None:
        """Hold the session open until it is closed or the model goes away."""
        import websockets

        url = f"{WS_URL}?model={self.settings.model}"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        lost: str | None = None
        try:
            async with websockets.connect(
                url, additional_headers=headers, max_size=None,
            ) as socket:
                self._socket = socket
                await self._send(socket, {
                    "type": "session.update", "session": self._session_config(),
                })
                self._ready.set()
                log.info("%s: connected to %s", self.label, self.settings.model)
                if self.persona.greeting and not self._greeted:
                    self._greeted = True
                    await self.say(
                        f"Greet the caller with exactly this, and nothing else: "
                        f"{self.persona.greeting}"
                    )
                await self._receive(socket)
            if not self._closed:
                log.warning("%s: the model closed the session", self.label)
                lost = "the model session ended"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never end a call with a stack trace
            text = str(exc)
            lowered = text.lower()
            if any(word in lowered for word in ("quota", "insufficient_quota", "429")):
                lost = "the model is out of quota"
            elif any(word in lowered for word in ("401", "403", "invalid_api_key", "unauthorized")):
                lost = "the model rejected the API key"
            else:
                lost = "the model could not be reached"
            log.error("%s: OpenAI Realtime unavailable — %s (%s: %s)",
                      self.label, lost, type(exc).__name__, text[:200])
        finally:
            self._socket = None
            self._closed = True
            self._ready.set()  # never leave a waiter hanging on a session that failed

        if lost:
            raise AgentUnavailable(lost)

    async def _receive(self, socket: Any) -> None:
        async for raw in socket:
            if self._closed:
                return
            try:
                event = json.loads(raw)
            except Exception:  # noqa: BLE001 — one unreadable frame is not the call
                continue
            if not isinstance(event, dict):
                continue
            await self._handle(socket, event)

    async def _handle(self, socket: Any, event: dict[str, Any]) -> None:
        kind = event.get("type") or ""

        if kind == "response.audio.delta":
            chunk = event.get("delta") or ""
            if chunk and self.on_audio:
                try:
                    self.on_audio(base64.b64decode(chunk), OPENAI_RATE)
                except Exception:  # noqa: BLE001
                    log.debug("%s: undecodable audio delta", self.label)

        elif kind == "input_audio_buffer.speech_started":
            # The caller cut in. The model stops by itself; whatever is queued
            # downstream has to go too, or the agent talks over them.
            if self.on_interrupted:
                self.on_interrupted()

        elif kind == "response.audio_transcript.delta":
            said = event.get("delta") or ""
            if said and self.on_transcript:
                self.on_transcript("agent", said)

        elif kind == "conversation.item.input_audio_transcription.completed":
            heard = event.get("transcript") or ""
            if heard and self.on_transcript:
                self.on_transcript("caller", heard)

        elif kind == "response.function_call_arguments.done":
            await self._answer_tool(socket, event)

        elif kind == "error":
            detail = event.get("error") or {}
            log.error("%s: realtime error — %s: %s", self.label,
                      detail.get("code"), str(detail.get("message"))[:200])

    async def _answer_tool(self, socket: Any, event: dict[str, Any]) -> None:
        """Run the tool the model asked for and hand the result back.

        Every call must be answered, including one that failed: the model
        waits on the call id, and a missing response leaves the conversation
        stalled with the caller listening to nothing. `response.create`
        afterwards is what makes it speak again — unlike Gemini, it does not
        resume by itself.
        """
        name = event.get("name") or ""
        call_id = event.get("call_id") or ""
        try:
            args = json.loads(event.get("arguments") or "{}")
        except Exception:  # noqa: BLE001
            args = {}
        if not isinstance(args, dict):
            args = {}

        try:
            result = await self.on_tool(name, args) if self.on_tool else "not available"
        except Exception as exc:  # noqa: BLE001
            log.exception("%s: tool %s failed", self.label, name)
            result = f"that could not be looked up ({type(exc).__name__})"

        await self._send(socket, {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"result": result}),
            },
        })
        await self._send(socket, {"type": "response.create"})


def available() -> bool:
    """Whether the websocket client is installed."""
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False
    return True
