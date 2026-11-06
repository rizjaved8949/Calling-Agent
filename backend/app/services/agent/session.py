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
from . import engines
from .caller_level import VOICE_FLOOR, CallerLevel, rms
from .gemini import AgentPersona, AgentUnavailable, LiveSettings, turn_timing
from .pacer import HUMAN_PREROLL_FRAMES, PREROLL_FRAMES, FramePacer
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
        human: bool = False,
    ) -> None:
        self.call_id = call_id
        self.live_settings = live_settings
        self.persona = persona
        self.on_transcript = on_transcript
        self.on_tool = on_tool
        self.on_ended = on_ended

        # How long to let the caller's silence run before answering. Set here
        # because this is the one place that holds both the line's settings
        # and the agent's pace, so every way of starting a call — inbound,
        # outbound, the browser test socket — gets the same treatment rather
        # than three places remembering to do it.
        from ...config import settings as _settings

        live_settings.vad_prefix_ms, live_settings.vad_silence_ms = turn_timing(
            persona.pace,
            prefix_ms=_settings.vad_prefix_ms,
            silence_ms=_settings.vad_silence_ms,
        )

        # A call an employee placed from the dialer: a person from the start,
        # no model, and when they leave the call ends rather than an agent
        # appearing mid-conversation.
        self.human = human
        self.handler = Handler.OPERATOR if human else Handler.AGENT
        self.started_at = time.time()
        self.transcript: list[tuple[str, str]] = []
        self.ended_reason = ""

        # Everything the far end hears goes through `_to_line`, which is also
        # where it is recorded. Recording what was *sent* rather than what the
        # model produced is the whole point: see `_to_line`.
        self._transport = send_to_caller
        # A human's voice needs almost no buffering; the model's needs some.
        self._pacer = FramePacer(
            self._to_line,
            keepalive=keepalive,
            preroll=HUMAN_PREROLL_FRAMES if human else PREROLL_FRAMES,
        )
        self._recorder = TwoWayRecorder(call_id) if record else None
        # The caller arrives at 16 kHz and the recording keeps 24 kHz, so the
        # caller is upsampled once here rather than the agent downsampled on
        # every burst.
        self._caller_to_record = RateConverter(3, 2) if record else None
        # Its own converter, not the caller's: each carries filter state
        # across frames, and sharing one would smear the two voices together.
        self._line_to_record = RateConverter(3, 2) if record else None
        self._agent_to_line = gemini_to_phone()

        self._agent: engines.Session | None = None
        self._agent_task: asyncio.Task | None = None
        self._pacer_task: asyncio.Task | None = None
        self._operator_out: Callable[[bytes], None] | None = None
        # Told when audio already sent must stop playing: the caller cut in,
        # or a person took over. Only a carrier that buffers on its side needs
        # it. Twilio plays what it has been sent until told `clear`, so
        # dropping our own queue alone leaves the agent talking over the
        # caller for as long as Twilio's buffer lasts. Infobip leaves it None.
        self.on_clear: Callable[[], None] | None = None
        # Told when the model cannot carry the call: refused at the start
        # ("out of quota") or lost mid-call. Without it the session simply
        # stopped, and the caller sat on a silent line until they gave up.
        # `live` decides what that means for the call. See `live._when_agent_fails`.
        self.on_agent_failed: Callable[["CallSession", str], Awaitable[None]] | None = None
        # Whether a real line carries this session's audio. False only while
        # a warmed-up session waits for its caller (`live.warm_up`); a failure
        # then is a reason to try again when the line arrives, not to hang up.
        self.attached = True
        self._failure_task: asyncio.Task | None = None
        self._closed = False
        # Raises a quiet caller before the model hears them. Set by the
        # transport that needs it (Twilio); None leaves the audio as it came.
        # See `caller_level.py`.
        self.caller_level: CallerLevel | None = None
        # When the caller last said something, by the wall clock; 0 if never.
        # How "they have gone" is checked against what is actually arriving.
        self.caller_spoke_at = 0.0

    # ---- lifecycle --------------------------------------------------------

    async def start(self, *, paced: bool = True) -> None:
        """Bring up the agent, and begin releasing audio at line rate.

        `paced=False` brings the agent up without starting the clock, which is
        how a session is warmed before the caller is connected. Anything the
        agent says meanwhile accumulates in the pacer's queue rather than being
        played to nobody, and is released the instant a transport arrives — so
        the greeting starts the moment the line opens instead of several
        seconds later. See `attach_transport`.
        """
        if paced:
            self._pacer_task = asyncio.create_task(self._run_pacer())
        if not self.human:
            await self._start_agent()

    def attach_transport(self, send_to_caller: Callable[[bytes], None]) -> None:
        """Point this session at the leg that will carry its audio.

        Starts the clock if `start(paced=False)` left it stopped. Whatever the
        agent has already said is still queued, so it plays from its first
        word rather than being cut into.
        """
        self._transport = send_to_caller
        self.attached = True
        if self._pacer_task is None and not self._closed:
            self._pacer_task = asyncio.create_task(self._run_pacer())

    def _to_line(self, frame: bytes) -> None:
        """One 20 ms frame to the far end — and to the recording.

        The recording is taken here, at the moment a frame actually goes down
        the line, rather than when the model produced it. The model answers in
        bursts: a five second reply arrives in a few hundred milliseconds, and
        recording it on arrival wrote five seconds of speech into a fraction
        of a second of timeline. The agent's track then ran ahead of the
        clock, so it never padded for the silences in between, and every reply
        landed earlier than the last until the two voices sat on top of each
        other.

        Taking it here also fixes barge-in for free. When the caller cuts in,
        the queued frames are dropped and never reach the line — and now never
        reach the recording either, instead of being mixed over the caller
        saying the thing that interrupted.
        """
        self._transport(frame)
        if self._recorder and self._line_to_record:
            with contextlib.suppress(Exception):
                self._recorder.feed("agent", self._line_to_record.process(frame))

    async def _start_agent(self) -> None:
        self._agent = engines.build(
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

    async def _supervise(self, agent: engines.Session) -> None:
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
                # Recorded so the operator sees it on the platform screen
                # rather than hearing about it from a customer. Explained
                # first: what arrives here is the provider's own text — "1011
                # None. Your prepayment credits are depleted..." — which names
                # the cause but buries it, and this is the one reading an
                # operator gets when nobody pressed Test.
                from .. import platform_settings
                platform_settings.note_failure(platform_settings.explain(str(exc)))
                await self.end(str(exc))
                # Reported from a task of its own: `end` has just cancelled
                # this one (it is the agent task), so anything awaited here
                # could be cut short before the call is dealt with.
                self._failure_task = asyncio.create_task(self._report_failure(str(exc)))

    async def _report_failure(self, reason: str) -> None:
        if self.on_agent_failed is None:
            return
        try:
            await self.on_agent_failed(self, reason)
        except Exception:  # noqa: BLE001 — a loose task has nowhere to raise to
            log.exception("call %s: could not act on the agent failing", self.call_id)

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
        # Every step between here and harvesting the recording is wrapped,
        # because the recording is the one thing that cannot be recreated.
        # The conversation is already on disk by this point; an exception
        # while shutting the model down used to throw it away and leave the
        # call marked as having no audio at all.
        with contextlib.suppress(Exception):
            if self._agent:
                self._agent.close()
        for task in (self._agent_task, self._pacer_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        audio = None
        try:
            audio = self._recorder.finish() if self._recorder else None
        except Exception:  # noqa: BLE001
            log.exception("call %s: the recording could not be mixed", self.call_id)
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
        if rms(pcm16) >= VOICE_FLOOR:
            self.caller_spoke_at = time.time()
        if self.handler is Handler.AGENT:
            if self._agent:
                heard = self.caller_level.process(pcm16) if self.caller_level else pcm16
                self._agent.feed(heard)
        elif self._operator_out:
            # Straight through: a person needs no model in the middle.
            self._operator_out(pcm16)

    # ---- the far end ------------------------------------------------------

    def _on_agent_audio(self, pcm24: bytes, _rate: int) -> None:
        if self._closed or self.handler is not Handler.AGENT:
            return
        # Not recorded here. This is the model's burst, not the line's
        # timeline — `_to_line` records each frame as it actually goes out.
        self._pacer.offer(self._agent_to_line.process(pcm24))

    def feed_operator(self, pcm16: bytes) -> None:
        """A frame from the person who took the call, at 16 kHz."""
        if self._closed or self.handler is not Handler.OPERATOR or not pcm16:
            return
        # Recorded by `_to_line` on its way out, on the agent's track: from
        # the caller's point of view there is one voice answering, whoever it
        # belongs to. Recording it here as well would write it twice, and the
        # old code built a fresh converter per frame, which loses the filter
        # state between them and adds a click at every boundary.
        self._pacer.offer(pcm16)

    async def hand_over(self, send_to_operator: Callable[[bytes], None]) -> None:
        """A person takes the call. The agent stops talking immediately.

        The model is closed rather than muted: left running it keeps listening,
        keeps spending tokens, and will answer the moment the operator pauses.
        """
        if self._closed:
            return
        if self.handler is Handler.OPERATOR:
            # Already a person's call (a dialer call): just connect the browser.
            self._operator_out = send_to_operator
            return
        dropped = self._pacer.flush()
        self._clear_far_end()
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
        if self._closed or self.handler is Handler.AGENT or self.human:
            return
        self._pacer.flush()
        self._operator_out = None
        self.handler = Handler.AGENT
        self._note("system", "the agent resumed the call")
        await self._start_agent()

    # ---- bookkeeping ------------------------------------------------------

    def _on_interrupted(self) -> None:
        dropped = self._pacer.flush()
        # Whether or not anything was queued here: the far end may still be
        # playing what was sent a moment ago.
        self._clear_far_end()
        if dropped:
            log.info("call %s: caller cut in, dropped %d frames", self.call_id, dropped)

    def _clear_far_end(self) -> None:
        if self.on_clear is not None:
            with contextlib.suppress(Exception):
                self.on_clear()

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

    def caller_spoke_within(self, seconds: float) -> bool:
        """Whether the caller was heard in the last `seconds`."""
        return bool(self.caller_spoke_at) and time.time() - self.caller_spoke_at <= seconds

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def frames_out(self) -> int:
        """Frames sent down the line so far. Silence only starts after the
        agent has said something, so above zero means the caller heard it."""
        return self._pacer.frames_sent

    @property
    def speaking(self) -> bool:
        """Whether the far end has audio queued to play right now."""
        return self._pacer.speaking

    @property
    def recording_rate(self) -> int:
        return RECORD_RATE

    @property
    def line_rate(self) -> int:
        return PHONE_RATE
