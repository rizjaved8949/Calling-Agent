"""
A call in progress, independent of how the audio reaches it.

The same object serves a WhatsApp WebRTC leg, an Infobip media stream and a
browser: each of those is only a way to move 16 kHz PCM in and out. What they
share is everything that matters — who is on the far end, the pacing, the
recording, the transcript.

The far end is swappable, and that is the whole operator story. A call starts
with the agent answering; when a person takes over, the Gemini session is
closed and a human's browser leg takes its place. Nothing else changes: the
caller's audio keeps arriving at the same `feed_caller`, the recording keeps
one timeline, and the transcript keeps accumulating.

    caller ──▶ feed_caller ──▶ [ agent | operator ] ──▶ pacer ──▶ transport
                    └────────────── recorder ──────────────┘
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable

from .audio_rate import FRAME_MS, PHONE_RATE, RateConverter, gemini_to_phone
from .gemini import AgentPersona, AgentUnavailable, GeminiLiveSession, LiveSettings
from .pacer import FramePacer
from .recorder import RECORD_RATE, TwoWayRecorder

log = logging.getLogger(__name__)


class Handler(str, Enum):
    AGENT = "agent"
    OPERATOR = "operator"


class CallSession:
    """One live conversation: caller on one side, agent or operator on the other."""

    def __init__(
        self,
        call_id: str,
        *,
        send_to_caller: Callable[[bytes], None],
        live_settings: LiveSettings,
        persona: AgentPersona,
        record: bool = True,
        keepalive: bool = True,
        on_transcript: Callable[[str, str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], Awaitable[str]] | None = None,
        on_ended: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.call_id = call_id
        self.live_settings = live_settings
        self.persona = persona
        self.on_transcript = on_transcript
        self.on_tool = on_tool
        self.on_ended = on_ended

        self.handler = Handler.AGENT
        self.started_at = time.time()
        self.transcript: list[tuple[str, str]] = []
        self.ended_reason = ""

        self._pacer = FramePacer(send_to_caller, keepalive=keepalive)
        self._recorder = TwoWayRecorder(call_id) if record else None
        # The caller arrives at 16 kHz and the recording keeps 24 kHz, so the
        # caller is upsampled once here rather than the agent downsampled on
        # every burst.
        self._caller_to_record = RateConverter(3, 2) if record else None
        self._agent_to_line = gemini_to_phone()

        self._agent: GeminiLiveSession | None = None
        self._agent_task: asyncio.Task | None = None
        self._pacer_task: asyncio.Task | None = None
        self._operator_out: Callable[[bytes], None] | None = None
        self._closed = False

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Bring up the agent and begin releasing audio at line rate."""
        self._pacer_task = asyncio.create_task(self._run_pacer())
        await self._start_agent()

    async def _start_agent(self) -> None:
        self._agent = GeminiLiveSession(
            self.live_settings,
            self.persona,
            on_audio=self._on_agent_audio,
            on_transcript=self._note,
            on_interrupted=self._on_interrupted,
            on_tool=self.on_tool,
            label=f"call {self.call_id[:12]}",
        )
        self._agent_task = asyncio.create_task(self._supervise(self._agent))
        with contextlib.suppress(asyncio.TimeoutError):
            await self._agent.wait_until_ready(timeout=20)

    async def _supervise(self, agent: GeminiLiveSession) -> None:
        """Run the model, and end the call rather than leave a silent line.

        A caller holding a line with nobody on it, hearing nothing, is worse
        than a call that ends with an explanation.
        """
        try:
            await agent.run()
        except asyncio.CancelledError:
            raise
        except AgentUnavailable as exc:
            if self.handler is Handler.AGENT and not self._closed:
                log.error("call %s: %s", self.call_id, exc)
                await self.end(str(exc))

    async def _run_pacer(self) -> None:
        # 5 ms, so a 20 ms frame is never more than a quarter-frame late.
        while not self._closed:
            self._pacer.tick()
            await asyncio.sleep(FRAME_MS / 4000)

    async def end(self, reason: str = "") -> bytes | None:
        """Stop everything and return the mixed recording, if there is one."""
        if self._closed:
            return None
        self._closed = True
        self.ended_reason = reason
        if self._agent:
            self._agent.close()
        for task in (self._agent_task, self._pacer_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        audio = self._recorder.finish() if self._recorder else None
        if self.on_ended:
            with contextlib.suppress(Exception):
                await self.on_ended(reason)
        log.info(
            "call %s ended (%s) after %.0fs, %d frames out",
            self.call_id, reason or "no reason given",
            time.time() - self.started_at, self._pacer.frames_sent,
        )
        return audio

    # ---- the caller's audio -----------------------------------------------

    def feed_caller(self, pcm16: bytes) -> None:
        """A frame from the caller: 16 kHz PCM16 mono."""
        if self._closed or not pcm16:
            return
        if self._recorder and self._caller_to_record:
            self._recorder.feed("caller", self._caller_to_record.process(pcm16))
        if self.handler is Handler.AGENT:
            if self._agent:
                self._agent.feed(pcm16)
        elif self._operator_out:
            # Straight through: a person needs no model in the middle.
            self._operator_out(pcm16)

    # ---- the far end ------------------------------------------------------

    def _on_agent_audio(self, pcm24: bytes, _rate: int) -> None:
        if self._closed or self.handler is not Handler.AGENT:
            return
        if self._recorder:
            self._recorder.feed("agent", pcm24)
        self._pacer.offer(self._agent_to_line.process(pcm24))

    def feed_operator(self, pcm16: bytes) -> None:
        """A frame from the person who took the call, at 16 kHz."""
        if self._closed or self.handler is not Handler.OPERATOR or not pcm16:
            return
        if self._recorder:
            # Recorded on the agent's track: from the caller's point of view
            # there is one voice answering, whoever it belongs to.
            self._recorder.feed("agent", RateConverter(3, 2).process(pcm16))
        self._pacer.offer(pcm16)

    async def hand_over(self, send_to_operator: Callable[[bytes], None]) -> None:
        """A person takes the call. The agent stops talking immediately.

        The model is closed rather than muted: left running it keeps listening,
        keeps spending tokens, and will answer the moment the operator pauses.
        """
        if self._closed or self.handler is Handler.OPERATOR:
            return
        dropped = self._pacer.flush()
        self.handler = Handler.OPERATOR
        self._operator_out = send_to_operator
        if self._agent:
            self._agent.close()
        if self._agent_task:
            self._agent_task.cancel()
        self._note("system", "a person took over the call")
        log.info("call %s: handed to an operator (dropped %d queued frames)",
                 self.call_id, dropped)

    async def hand_back(self) -> None:
        """The person leaves and the agent resumes."""
        if self._closed or self.handler is Handler.AGENT:
            return
        self._pacer.flush()
        self._operator_out = None
        self.handler = Handler.AGENT
        self._note("system", "the agent resumed the call")
        await self._start_agent()

    # ---- bookkeeping ------------------------------------------------------

    def _on_interrupted(self) -> None:
        dropped = self._pacer.flush()
        if dropped:
            log.info("call %s: caller cut in, dropped %d frames", self.call_id, dropped)

    def _note(self, who: str, text: str) -> None:
        self.transcript.append((who, text))
        if self.on_transcript:
            with contextlib.suppress(Exception):
                self.on_transcript(who, text)

    def transcript_text(self) -> str:
        """The conversation as one block, which is how a call record keeps it.

        The model streams a sentence in fragments — "Assalam o", "Alaikum!",
        "main" — and whether a fragment carries its own leading space is not
        consistent. Joining them raw produces "mainvirtualassistanthoon", so a
        space is added only where neither side already has one, and never
        before punctuation.
        """
        lines: list[str] = []
        for who, text in self.transcript:
            if not text.strip():
                continue
            if lines and lines[-1].startswith(f"{who}:"):
                previous = lines[-1]
                piece = text.strip()
                # The fragment is stripped either way, so its own leading space
                # cannot double as the separator — decide that from what is
                # already there and what the fragment starts with.
                joiner = (
                    ""
                    if previous.endswith((" ", "\n"))
                    or piece.startswith((",", ".", "!", "?", ":", ";", "،", "۔"))
                    else " "
                )
                lines[-1] = previous + joiner + piece
            else:
                lines.append(f"{who}: {text.strip()}")
        return "\n".join(lines)

    @property
    def recording_rate(self) -> int:
        return RECORD_RATE

    @property
    def line_rate(self) -> int:
        return PHONE_RATE
