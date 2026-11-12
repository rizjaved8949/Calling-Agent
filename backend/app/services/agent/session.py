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

from ...config import settings as _settings
from .audio_rate import FRAME_MS, PHONE_RATE, SILENT_FRAME, RateConverter, gemini_to_phone
from . import engines
from . import turns as turns_mod
from .caller_level import VOICE_FLOOR, CallerLevel, rms
from .gemini import AgentPersona, AgentUnavailable, LiveSettings, turn_timing
from .pacer import HUMAN_PREROLL_FRAMES, PREROLL_FRAMES, FramePacer
from .recorder import RECORD_RATE, TwoWayRecorder

log = logging.getLogger(__name__)

#: How long after the agent's last word the caller's side can still carry
#: its echo: the network to the handset and back, plus the room.
ECHO_TAIL_SECONDS = 1.0

# An outbound call's greeting waits for the person's "hello". See
# `CallSession.hold_for_hello`. Read from the settings (HELLO_WAIT_SECONDS,
# HELLO_DONE_SECONDS, HELLO_MAX_SECONDS in the environment) so they can be
# tuned on the host without a deploy; held here as module values so a test
# can set them too.
#: How long to wait for anyone to speak before greeting anyway. 1.5 s
#: missed the person's hello on six calls in thirteen; 0.8 s catches it.
HELLO_WAIT_SECONDS = float(_settings.hello_wait_seconds)
#: How much quiet after they spoke means their "hello" is finished.
HELLO_DONE_SECONDS = float(_settings.hello_done_seconds)
#: The longest the greeting is ever held, so a noisy line cannot hold it.
HELLO_MAX_SECONDS = float(_settings.hello_max_seconds)

#: How many times a model session that dropped mid-call is reconnected.
RECONNECT_ATTEMPTS = 1
#: How long the model's words after the goodbye may wait for the end-call
#: guard's decision before they are played anyway.
HOLD_MAX_SECONDS = 20.0
#: How much of the conversation the reconnected session is given.
RESUME_TRANSCRIPT_CHARS = 6_000
RESUME_NOTE = (
    "\n\n### The connection dropped and was restored\n"
    "This call was already in progress when the connection dropped. Here is "
    "the conversation so far:\n{transcript}\n"
    "Continue from exactly where it left off. Do not greet again and do not "
    "introduce yourself again. Say one short sentence apologising for the "
    "pause, then carry on with what the caller was asking."
)


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
        live_settings.vad_prefix_ms, live_settings.vad_silence_ms = turn_timing(
            persona.pace,
            prefix_ms=_settings.vad_prefix_ms,
            silence_ms=_settings.vad_silence_ms,
        )
        live_settings.vad_start_sensitivity = _settings.vad_start_sensitivity
        live_settings.vad_end_sensitivity = _settings.vad_end_sensitivity
        live_settings.temperature = _settings.gemini_temperature

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
        # Only counted while the agent is quiet: see `feed_caller`.
        self.caller_spoke_at = 0.0
        # When the agent's voice last went down the line (not keep-alive
        # silence); 0 if it has not spoken yet.
        self.agent_audible_at = 0.0
        # Set while an outbound greeting waits for the person's "hello":
        # (since, heard_at), wall clock; heard_at is 0 until they speak.
        self._hello: list[float] | None = None
        # How quickly the agent answers, measured rather than guessed: when
        # the line was picked up, when the agent's first word went out, and
        # how long after the caller stopped speaking each reply began.
        self.pickup_at = 0.0
        self.first_audio_at = 0.0
        self._reply_latencies: list[float] = []
        self._last_frame_silent = True
        self._answered_up_to = 0.0
        # When the caller's words last arrived as text, by the wall clock.
        # Against `caller_spoke_at` it says whether what they just said has
        # been transcribed yet, which a guard must wait for before judging.
        self.caller_transcribed_at = 0.0
        # When the recogniser last said the caller's turn was complete; what
        # must be judged on a whole turn (a number's length) is judged then.
        self.caller_turn_done_at = 0.0
        # After the goodbye: what the model says next waits, unheard, until
        # the end-call guard decides whether the caller asked something new.
        # See `hold_after_goodbye`.
        self._closing_hold = False
        self._held_since = 0.0
        self._held_audio: list[bytes] = []
        self._held_lines: list[tuple[str, str]] = []
        # How many times a dropped model session has been reconnected.
        self._reconnects = 0

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
            self.pickup_at = self.pickup_at or time.time()
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
        self.pickup_at = self.pickup_at or time.time()
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
        if frame != SILENT_FRAME:
            now = time.time()
            self.agent_audible_at = now
            if not self.first_audio_at:
                self.first_audio_at = now
            if self._last_frame_silent and self.caller_spoke_at > self._answered_up_to:
                # A reply has just begun, and the caller said something since
                # the last one: how long they waited for it.
                self._reply_latencies.append(now - self.caller_spoke_at)
                self._answered_up_to = self.caller_spoke_at
            self._last_frame_silent = False
        else:
            self._last_frame_silent = True
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
        than a call that ends with an explanation. But a model that drops a
        conversation already in progress (a 1011 "internal error", four
        minutes into a real call) is tried once more first, with the
        transcript so far, so the caller hears a short pause rather than a
        dial tone.
        """
        try:
            await agent.run()
        except asyncio.CancelledError:
            raise
        except AgentUnavailable as exc:
            if self.handler is not Handler.AGENT or self._closed:
                return
            dropped = getattr(agent, "connected", False)
            if dropped and self._agent is agent and self._reconnects < RECONNECT_ATTEMPTS:
                self._reconnects += 1
                log.warning("call %s: the model dropped mid-call (%s); reconnecting, "
                            "attempt %d of %d", self.call_id, exc, self._reconnects,
                            RECONNECT_ATTEMPTS)
                try:
                    came_up = await self._reconnect_agent()
                except Exception:  # noqa: BLE001 — fall through to the failure path
                    log.exception("call %s: the reconnect could not be started", self.call_id)
                    came_up = None
                if came_up:
                    log.info("call %s: reconnected to the model; continuing with the "
                             "transcript so far", self.call_id)
                    return
                if came_up is False:
                    # The new session is up and failing in its own supervisor,
                    # which reports the loss; nothing more to do here.
                    log.error("call %s: the reconnect did not come up", self.call_id)
                    return
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

    def _resume_instructions(self) -> str:
        """The agent's instructions plus the conversation so far, for a new
        session that has to carry on where the dropped one stopped."""
        transcript = self.transcript_text()
        if len(transcript) > RESUME_TRANSCRIPT_CHARS:
            transcript = "…" + transcript[-RESUME_TRANSCRIPT_CHARS:]
        return self.persona.instructions + RESUME_NOTE.format(transcript=transcript or "(nothing yet)")

    async def _reconnect_agent(self) -> bool:
        """Start a fresh model session carrying the transcript so far.

        True once it is up. False when it came up and went straight down
        again: its own supervisor then reports the loss. The new session is
        given no greeting, so the caller is not welcomed twice.
        """
        from dataclasses import replace

        persona = replace(self.persona, greeting="", instructions=self._resume_instructions())
        agent = engines.build(
            self.live_settings,
            persona,
            on_audio=self._on_agent_audio,
            on_transcript=self._note,
            on_interrupted=self._on_interrupted,
            on_tool=self.on_tool,
            label=f"call {self.call_id[:12]} (reconnected)",
        )
        self._agent = agent
        self._agent_task = asyncio.create_task(self._supervise(agent))
        with contextlib.suppress(asyncio.TimeoutError):
            await agent.wait_until_ready(timeout=20)
        return bool(agent.live)

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
            if self._hello is not None:
                self._check_hello()
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
        # Judged on what the model hears, after the level is raised: a quiet
        # caller's "hello?" sat under the floor in the raw audio, and the
        # greeting talked over it.
        heard = self.caller_level.process(pcm16) if self.caller_level else pcm16
        voiced = rms(heard) >= VOICE_FLOOR
        if voiced and not self.agent_may_be_echoing():
            self.caller_spoke_at = time.time()
        if self._hello is not None:
            if voiced:
                self._hello[1] = time.time()
            self._check_hello()
        if self.handler is Handler.AGENT:
            if self._agent:
                if self._hello is not None:
                    # Their "hello" is answered by the greeting, not by the
                    # model: heard as well, it would reply to it as a turn of
                    # its own and greet twice. Silence keeps the stream whole.
                    heard = bytes(len(heard))
                self._agent.feed(heard)
        elif self._operator_out:
            # Straight through: a person needs no model in the middle.
            self._operator_out(pcm16)

    # ---- the far end ------------------------------------------------------

    def _on_agent_audio(self, pcm24: bytes, _rate: int) -> None:
        if self._closed or self.handler is not Handler.AGENT:
            return
        if self._closing_hold:
            if time.time() - self._held_since > HOLD_MAX_SECONDS:
                self.release_hold()                  # nobody decided; better heard than lost
            else:
                self._held_audio.append(self._agent_to_line.process(pcm24))
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
        if who == "caller:done":
            # The recogniser has finished the caller's turn. Not a line of
            # the transcript; a moment the watchers can act on.
            self.caller_turn_done_at = time.time()
            if self.on_transcript:
                with contextlib.suppress(Exception):
                    self.on_transcript(who, "")
            return
        if who == "agent":
            from .script import strip_tool_calls

            text, leaked = strip_tool_calls(text)
            if leaked:
                log.warning("call %s: the model spoke a function call (%d); taken out of "
                            "the transcript", self.call_id, leaked)
                if not text.strip():
                    return
            if self._closing_hold:
                # Said after the goodbye and not heard yet (see
                # hold_after_goodbye): kept aside, with its audio.
                self._held_lines.append((who, text))
                return
        self.transcript.append((who, text))
        if who == "caller" and (text or "").strip():
            self.caller_transcribed_at = time.time()
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
        from .script import strip_meta

        lines: list[str] = []
        for who, text in self.transcript:
            # A model's note to itself ("Final output generated.") is not
            # part of the conversation.
            text = strip_meta(text) if who == "agent" else text
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

    def turns(self) -> list[tuple[str, str]]:
        """The conversation as turns, for the tool guards to read."""
        return turns_mod.turns(self.transcript)

    def caller_said_since(self, index: int) -> str:
        """What the caller has said from transcript position `index` on."""
        return " ".join(text.strip() for who, text in self.transcript[index:]
                        if who == "caller" and text.strip())

    async def note_to_agent(self, text: str, *, urgent: bool = False) -> None:
        """Tell the model something. Not urgent: for its next turn, without
        making it speak. Urgent: now, cutting in on whatever it was about to
        say, which is how "that number is too short" reaches it before the
        read-back. Never raises: a note that cannot be delivered is logged."""
        agent = self._agent
        note = getattr(agent, "note", None)
        if self._closed or agent is None or not callable(note):
            return
        try:
            try:
                await note(text, urgent=urgent)
            except TypeError:
                await note(text)
        except Exception:  # noqa: BLE001
            log.exception("call %s: could not pass a note to the model", self.call_id)

    def hold_after_goodbye(self) -> None:
        """The goodbye has been said: what the model says next waits, unheard.

        A model told "say nothing more" said the closing line a second time
        on a real call. What it says after the goodbye, with the caller
        silent or making noise, is the goodbye again, and the hang-up
        discards it. What it says after the caller asks something new is an
        answer, and `release_hold` plays it, audio and transcript, when the
        end-call guard decides the call goes on.
        """
        if self._closing_hold:
            return
        self._closing_hold = True
        self._held_since = time.time()
        self._held_audio = []
        self._held_lines = []

    def release_hold(self) -> None:
        """The caller asked something after the goodbye: what the model said
        meanwhile is said now, and the line is open again."""
        if not self._closing_hold:
            return
        self._closing_hold = False
        audio, lines = self._held_audio, self._held_lines
        self._held_audio, self._held_lines = [], []
        for pcm in audio:
            self._pacer.offer(pcm)
        for who, text in lines:
            self._note(who, text)
        log.info("call %s: the call goes on after the goodbye; %d held frames released",
                 self.call_id, len(audio))

    @property
    def holding_after_goodbye(self) -> bool:
        return self._closing_hold

    def timing_summary(self) -> str:
        """How quickly the agent answered, for the settled log line."""
        parts: list[str] = []
        if self.pickup_at and self.first_audio_at:
            parts.append(f"first audio {self.first_audio_at - self.pickup_at:.1f}s after pickup")
        if self._reply_latencies:
            average = sum(self._reply_latencies) / len(self._reply_latencies)
            parts.append(
                f"reply after the caller stopped avg {average:.1f}s max "
                f"{max(self._reply_latencies):.1f}s over {len(self._reply_latencies)} turns"
            )
        return ", ".join(parts)

    def caller_spoke_within(self, seconds: float) -> bool:
        """Whether the caller was heard in the last `seconds`."""
        return bool(self.caller_spoke_at) and time.time() - self.caller_spoke_at <= seconds

    def hold_for_hello(self) -> None:
        """Hold the greeting until the person who picked up has said hello.

        For an outbound call. Whoever answers says "hello?" first, and a
        greeting already playing talks straight over it; on a real call the
        agent greeted a second after pickup, before anyone spoke. So the
        greeting (already generated, so nothing is lost by waiting) is held
        and released the moment their hello ends, or after
        `HELLO_WAIT_SECONDS` if nobody speaks, and never later than
        `HELLO_MAX_SECONDS`.
        """
        self._hello = [time.time(), 0.0]
        self._pacer.held = True

    @property
    def waiting_for_hello(self) -> bool:
        return self._hello is not None

    def _check_hello(self) -> None:
        if self._hello is None:
            return
        since, heard_at = self._hello
        now = time.time()
        waited = now - since
        if heard_at:
            if now - heard_at < HELLO_DONE_SECONDS and waited < HELLO_MAX_SECONDS:
                return
            why = f"after their hello, {waited:.1f}s in"
        elif waited < HELLO_WAIT_SECONDS:
            return
        else:
            why = f"nobody spoke in {HELLO_WAIT_SECONDS:.1f}s"
        self._hello = None
        self._pacer.held = False
        log.info("call %s: greeting %s", self.call_id, why)

    def caller_spoke_since(self, moment: float) -> bool:
        """Whether the caller was heard after `moment` (wall clock)."""
        return self.caller_spoke_at > moment

    def agent_may_be_echoing(self) -> bool:
        """Whether what arrives from the caller could be the agent's own voice.

        A phone sends back some of what it plays: the handset's speaker into
        its microphone, or the network's own echo. While the agent is talking,
        and for `ECHO_TAIL_SECONDS` after its last word has gone out (the trip
        to the handset and back), loud audio from the caller's side may be
        the agent, so it does not count as the caller speaking. Deciding the
        caller is still there, or spoke after the goodbye, from the agent's
        own echo would keep calls open that should end.
        """
        if self._pacer.speaking:
            return True
        return bool(self.agent_audible_at) and (
            time.time() - self.agent_audible_at < ECHO_TAIL_SECONDS)

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
