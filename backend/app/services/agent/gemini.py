"""
A Gemini Live session: audio in, the agent's voice out.

Deliberately knows nothing about telephony. Feed it 16 kHz PCM16 from wherever
the caller is — a WhatsApp WebRTC leg, a carrier socket, a browser microphone —
and it calls back with the agent's reply and both sides' transcripts. The
transport is the caller's problem; this is only the model.

That separation is why this can be proven before any of the call plumbing
exists: `scripts/agent_probe.py` drives it from a WAV file.

Ported from University-Calling-Agent's `gemini_agent.py`. The settings below
are the ones that module arrived at on live calls, and the reasons are kept
with them.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .audio_rate import GEMINI_IN_RATE, GEMINI_OUT_RATE

log = logging.getLogger(__name__)


@dataclass
class AgentPersona:
    """What the company decided their agent is.

    Every field comes from their tenant row. There is no platform-wide
    greeting: a default here would be a sentence spoken to someone else's
    callers.
    """

    instructions: str
    greeting: str = ""
    voice: str = ""
    language: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LiveSettings:
    """Tuning that is about the line, not about the company."""

    model: str
    api_key: str
    # Less eager than the default on purpose: on a phone line the agent's own
    # voice leaks back through the caller's handset, and a sensitive detector
    # reads that as the caller speaking and cuts her off mid-sentence.
    vad_prefix_ms: int = 300
    vad_silence_ms: int = 700


class AgentUnavailable(RuntimeError):
    """The model could not be reached, or went away mid-call."""


class GeminiLiveSession:
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

        self._session: Any = None
        self._closed = False
        self._ready = asyncio.Event()
        self._greeted = False
        self._sends: set[asyncio.Task] = set()

    # ---- state -----------------------------------------------------------

    @property
    def live(self) -> bool:
        return self._session is not None and not self._closed

    async def wait_until_ready(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    def close(self) -> None:
        self._closed = True

    # ---- the caller's side ------------------------------------------------

    def feed(self, pcm16: bytes) -> None:
        """A frame of caller audio, already 16 kHz PCM16 mono.

        Fire and forget: a frame that cannot be sent is dropped rather than
        awaited, because blocking the caller's audio path to retry one 20 ms
        frame costs more than the frame was worth.
        """
        if self._closed or not pcm16:
            return
        session = self._session
        if session is None:
            return  # not up yet; the opening of a call is silence anyway
        task = asyncio.create_task(self._send(session, pcm16))
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)

    async def _send(self, session: Any, pcm16: bytes) -> None:
        from google.genai import types

        try:
            await session.send_realtime_input(
                audio=types.Blob(data=pcm16, mime_type=f"audio/pcm;rate={GEMINI_IN_RATE}")
            )
        except Exception as exc:  # noqa: BLE001 — a dropped frame must not end the call
            if not self._closed:
                log.debug("%s: audio frame not sent (%s)", self.label, exc)

    async def say(self, text: str) -> None:
        """Make the agent speak something now, without waiting to be spoken to.

        This is how a call opens. Left to itself the model waits for the caller
        first, which on an answered call is two people listening to each other
        in silence.
        """
        session = self._session
        if session is None or self._closed:
            return
        from google.genai import types

        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )

    # ---- running ----------------------------------------------------------

    async def run(self) -> None:
        """Hold the session open until it is closed or the model goes away."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.settings.api_key)
        config: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "system_instruction": self.persona.instructions,
            # Both sides transcribed, which is what makes a call reviewable
            # afterwards — without it only the recording survives.
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "realtime_input_config": {
                "automatic_activity_detection": {
                    "start_of_speech_sensitivity": "START_SENSITIVITY_LOW",
                    "end_of_speech_sensitivity": "END_SENSITIVITY_LOW",
                    "prefix_padding_ms": self.settings.vad_prefix_ms,
                    "silence_duration_ms": self.settings.vad_silence_ms,
                }
            },
        }
        if self.persona.voice:
            config["speech_config"] = {
                "voice_config": {"prebuilt_voice_config": {"voice_name": self.persona.voice}}
            }
        if self.persona.tools:
            config["tools"] = [{"function_declarations": self.persona.tools}]

        lost: str | None = None
        try:
            async with client.aio.live.connect(model=self.settings.model, config=config) as session:
                self._session = session
                self._ready.set()
                log.info("%s: connected to %s", self.label, self.settings.model)
                if self.persona.greeting and not self._greeted:
                    self._greeted = True
                    await self.say(
                        f"Greet the caller with exactly this, and nothing else: "
                        f"{self.persona.greeting}"
                    )
                await self._receive(session)
            if not self._closed:
                log.warning("%s: the model closed the session", self.label)
                lost = "the model session ended"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never end a call with a stack trace
            text = str(exc)
            quota = any(w in text.lower() for w in ("quota", "resource_exhausted", "429"))
            log.error(
                "%s: Gemini unavailable%s (%s: %s)",
                self.label, " — OUT OF QUOTA" if quota else "",
                type(exc).__name__, text[:200],
            )
            lost = "the model is out of quota" if quota else "the model could not be reached"
        finally:
            self._session = None
            self._closed = True
            self._ready.set()  # never leave a waiter hanging on a session that failed

        if lost:
            raise AgentUnavailable(lost)

    async def _receive(self, session: Any) -> None:
        from google.genai import types

        while not self._closed:
            async for message in session.receive():
                if self._closed:
                    return
                content = getattr(message, "server_content", None)

                if content is not None:
                    # The caller cut in. The model has already stopped; whatever
                    # is queued downstream has to go too, or the agent talks
                    # over them.
                    if getattr(content, "interrupted", False) and self.on_interrupted:
                        self.on_interrupted()

                    turn = getattr(content, "model_turn", None)
                    for part in getattr(turn, "parts", None) or []:
                        data = getattr(getattr(part, "inline_data", None), "data", None)
                        if data and self.on_audio:
                            self.on_audio(data, GEMINI_OUT_RATE)

                    if self.on_transcript:
                        heard = getattr(content, "input_transcription", None)
                        said = getattr(content, "output_transcription", None)
                        if heard is not None and getattr(heard, "text", ""):
                            self.on_transcript("caller", heard.text)
                        if said is not None and getattr(said, "text", ""):
                            self.on_transcript("agent", said.text)

                tool_call = getattr(message, "tool_call", None)
                calls = getattr(tool_call, "function_calls", None) if tool_call else None
                if calls:
                    await self._answer_tools(session, types, calls)

    async def _answer_tools(self, session: Any, types: Any, calls: list[Any]) -> None:
        """Run the tools the model asked for and hand back every result.

        Every call must be answered, including ones that failed: the model
        waits for a response per id, and a missing one leaves the conversation
        stalled with the caller listening to nothing.
        """
        responses = []
        for call in calls:
            name = getattr(call, "name", "")
            args = dict(getattr(call, "args", None) or {})
            try:
                result = await self.on_tool(name, args) if self.on_tool else "not available"
            except Exception as exc:  # noqa: BLE001
                log.exception("%s: tool %s failed", self.label, name)
                result = f"that could not be looked up ({type(exc).__name__})"
            responses.append(
                types.FunctionResponse(
                    id=getattr(call, "id", None), name=name, response={"result": result}
                )
            )
        if responses:
            await session.send_tool_response(function_responses=responses)


def available() -> bool:
    """Whether the SDK is installed. It is an optional dependency."""
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return True
