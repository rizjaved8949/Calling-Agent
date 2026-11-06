"""
The calls that are happening right now.

One place that knows how to start a call, find it again while it is running,
and finish it properly — so every transport (a carrier websocket, a WebRTC leg,
a browser) does the same three things in the same way, and a recording lands in
the company's Drive whether the call came in over WhatsApp or a phone line.

Finishing is the part worth centralising. A call ends in several ways — the
caller hangs up, the agent says goodbye, the socket drops, the model dies — and
each one has to settle the row, mix the recording, store it, save the
transcript and refresh the report. Spread across four transports, three of them
would be subtly wrong.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Callable

from ...config import settings
from ...models.call import Call, CallStatus, Direction, RecordingState
from ...models.tenant import Tenant
from ...repositories import calls as call_repo
from ...repositories import knowledge
from .. import recordings as recording_service
from .. import reports
from . import prompt
from . import script
from . import voices
from . import tools as tool_catalogue
from .gemini import AgentPersona, LiveSettings
from .session import CallSession

log = logging.getLogger(__name__)

_sessions: dict[str, CallSession] = {}


def get(call_id: str) -> CallSession | None:
    return _sessions.get(call_id)


def active() -> int:
    return len(_sessions)


class AtCapacity(RuntimeError):
    """This process is already carrying as many calls as it will take.

    Raised rather than returned because every caller of `start` already settles
    a call it could not answer — the carrier leg is terminated and the row is
    marked failed — so a refusal travels the same path as any other reason the
    agent could not come up.
    """


def capacity() -> int:
    """How many calls may run at once. Zero or less means no limit."""
    return settings.max_concurrent_calls


def at_capacity() -> bool:
    """True when one more call would take this process past its limit.

    Checked before a call is admitted rather than discovered while it runs.
    Audio is a real-time workload: a process that accepts more conversations
    than it can carry does not slow down politely, it breaks every one of them
    at once, and the caller hears it.
    """
    limit = capacity()
    return limit > 0 and len(_sessions) >= limit


def live_settings() -> LiveSettings:
    """The engine from the server's environment.

    `live_settings_now` is what a call uses: it also reads what the platform
    operator set in the portal. This stays for probes and tests that have no
    database to ask.
    """
    return LiveSettings(
        model=settings.gemini_live_model,
        api_key=settings.gemini_api_key.strip(),
    )


async def live_settings_now(tenant: Tenant | None = None) -> LiveSettings:
    """The engine this call runs on.

    Three places, narrowest first: the company's own key if the operator gave
    it one, then the platform's, then the server's environment. A company
    without its own is not a special case — it is every company, until there
    is a reason to separate one.
    """
    from .. import platform_settings

    try:
        chosen = await platform_settings.current()
    except Exception:  # noqa: BLE001 — never fail a call over a settings read
        log.exception("could not read the platform engine settings")
        chosen = {"model": settings.gemini_live_model,
                  "apiKey": settings.gemini_api_key.strip(),
                  "engine": settings.voice_engine.strip() or "gemini"}
    model = (tenant.engine_model.strip() if tenant else "") or chosen["model"]
    key = (tenant.engine_api_key.strip() if tenant else "") or chosen["apiKey"]
    return LiveSettings(
        model=model, api_key=key, engine=str(chosen.get("engine") or "gemini"),
    )


async def persona_for(tenant: Tenant, call: Call | None = None) -> AgentPersona:
    """The agent for this call, built from the company's settings and material.

    Which agent, and which documents, is `services/routing.py`'s decision —
    the number's setup, a base named on the call, the agent's own default, or
    everything the company has, in that order. Resolving here rather than at
    each of the four places that answer a call means a phone call, a WhatsApp
    call and a browser test all pick the same agent for the same number.

    The knowledge goes in the system prompt rather than behind a tool, so the
    agent never has to decide to look something up mid-sentence — on a phone
    call that pause is the difference between an answer and dead air.
    """
    from ...models.agent import Direction as SetupDirection
    from .. import routing

    if call is None:
        # A caller with no call in hand (a preview, a probe) gets the company's
        # own settings and all of its documents, as before.
        context = await knowledge.context_for(tenant.phone_number_id)
        voice = {"greeting": tenant.agent_greeting, "persona": tenant.persona,
                 "language": tenant.language, "ttsVoice": tenant.tts_voice,
                 "tone": "", "escalation": "", "pace": ""}
    else:
        resolved = await routing.resolve(
            tenant,
            channel=call.channel,
            direction=(
                SetupDirection.INBOUND
                if call.direction is Direction.INBOUND
                else SetupDirection.OUTBOUND
            ),
            knowledge_base_id=call.knowledge_base_id,
            agent_id=call.agent_id,
            line_id=call.line_id,
        )
        context = await routing.context_for(tenant, resolved)
        voice = routing.persona_for(tenant, resolved)
        # Recorded on the call so "why did it answer that?" stays answerable
        # after the setups have been edited.
        call.agent_id = resolved.agent_id
        call.knowledge_base_id = resolved.knowledge_base_id
        call.knowledge_base_name = resolved.knowledge_base_name
        call.resolved_by = resolved.resolved_by

    language = (voice["language"] or "").strip()
    can_message = bool(call) and await _can_send_whatsapp(tenant)

    # Every word of this is now a block a company can read and change; see
    # `prompt.py`. It used to be a few hundred words of behaviour written
    # here, which a company could neither see nor argue with.
    instructions = prompt.build(
        persona=voice["persona"] or "",
        context=context,
        written=voice.get("promptBlocks") or {},
        language_rule=(
            f"Speak {_language_name(language)} unless the caller uses another "
            "language, in which case follow them. Mirror their mix of languages "
            "rather than forcing one. "
            # Said because it is routinely got wrong: Urdu and Hindi are one
            # spoken language and two scripts, so anything written down —
            # a WhatsApp message, a transcript line — comes back in whichever
            # the model reached for, and Devanagari is unreadable to the
            # people whose call it was.
            f"When you write anything, write {_language_name(language)} in its "
            "own script, the one its readers actually use. Never transliterate "
            "it into another script, and never use Devanagari for Urdu."
            if language
            else "Speak whatever language the caller speaks, and mirror their "
                 "mix of languages rather than forcing one. Write each language "
                 "in its own script rather than transliterating it."
        ),
        pace=(voice.get("pace") or "").strip(),
        # Derived from the chosen voice rather than asked for separately: the
        # two have to agree, and a second setting is a second thing to get
        # wrong. A woman's voice saying "main karta hoon" is audibly wrong to
        # every Urdu speaker and invisible in an English transcript.
        voice_gender=voices.gender_of(voice.get("ttsVoice") or ""),
        tone=voice.get("tone") or "",
        escalation=voice.get("escalation") or "",
        extra=voice.get("extraRules") or "",
        override=voice.get("promptOverride") or "",
        fallback_line=voice.get("fallbackLine") or "",
        pronunciations=voice.get("pronunciations") or "",
        can_send_whatsapp=can_message,
    )

    return AgentPersona(
        instructions=instructions,
        greeting=(voice["greeting"] or "").strip(),
        voice=(voice["ttsVoice"] or "").strip(),
        language=language,
        pace=(voice.get("pace") or "").strip(),
        tools=tool_catalogue.for_call(can_send_whatsapp=can_message),
    )


# A language tag is for machines. "Speak ur-PK" is not an instruction anyone,
# including a model, should have to decode.
_LANGUAGE_NAMES = {
    "ur": "Urdu", "en": "English", "ar": "Arabic", "hi": "Hindi", "pa": "Punjabi",
    "ps": "Pashto", "sd": "Sindhi", "fa": "Persian", "tr": "Turkish",
    "fr": "French", "es": "Spanish", "de": "German", "zh": "Chinese",
    "bn": "Bengali", "id": "Indonesian", "ms": "Malay", "ru": "Russian",
}


def _language_name(tag: str) -> str:
    """"ur-PK" -> "Urdu", and anything unrecognised through unchanged."""
    base = tag.replace("_", "-").split("-")[0].lower()
    return _LANGUAGE_NAMES.get(base, tag)


async def _can_send_whatsapp(tenant: Tenant) -> bool:
    """Whether this company could actually deliver a message if asked to.

    Checked rather than assumed: declaring the tool for a company with no
    WhatsApp number has the agent promise to text somebody and then fail
    silently after they have hung up.
    """
    from .. import lines

    try:
        return await lines.whatsapp_sender(tenant) is not None
    except Exception:  # noqa: BLE001 — a lookup failure is not a reason to fail a call
        log.exception("tenant %s: could not check WhatsApp availability",
                      tenant.phone_number_id)
        return False


def _tool_handler(tenant: Tenant, call: Call):
    """Run what the agent asked for, and say in one line what happened.

    Every answer is written for the model to read out, because that is what it
    does with it. "Sent." is better than a JSON blob, and a failure has to come
    back as words the agent can own — telling the caller a message is on its
    way when it is not is the one outcome worth protecting against.
    """
    from ...services.whatsapp import to_e164

    async def handle(name: str, args: dict) -> str:
        if name == "send_whatsapp_message":
            text = str(args.get("text") or "").strip()
            if not text:
                return "No message was sent: there was nothing to say."
            from .. import lines
            from ...services.whatsapp import WhatsApp

            sender = await lines.whatsapp_sender(tenant)
            if sender is None:
                return (
                    "That did not send: this company has no WhatsApp number. "
                    "Offer to have someone follow up instead."
                )
            # The number on the call unless they named a different one, which
            # is what keeps the agent from asking for a number it already has.
            destination = str(args.get("to") or "").strip() or call.counterparty
            try:
                await WhatsApp(sender).send_text(
                    to_e164(destination, sender), text, call_id=call.id
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("call %s: the agent's message did not send: %s", call.id, exc)
                return (
                    "That did not send. Tell them it will follow shortly and "
                    "do not promise it again."
                )
            log.info("call %s: the agent sent a WhatsApp message", call.id)
            return "Sent. Tell them it is on its way."

        if name == "end_call":
            reason = str(args.get("reason") or "the agent ended the call")[:200]
            session = _sessions.get(call.id)
            if (session is not None and _claims_caller_gone(reason)
                    and session.caller_spoke_within(CALLER_GONE_SECONDS)):
                # The model said they had gone while their voice was still
                # coming down the line: twice on real calls, it hung up on a
                # caller who was talking. A caller who really rings off ends
                # the call from the carrier's side; nothing needs deciding.
                log.info("call %s: not ending the call: the agent said %r, but "
                         "the caller was heard %.1fs ago", call.id, reason,
                         time.time() - session.caller_spoke_at)
                return (
                    "Do not end the call: the caller is still on the line and "
                    "has just been speaking. Listen to them and answer what "
                    "they said."
                )
            log.info("call %s: the agent is ending the call (%s)", call.id, reason)
            asyncio.create_task(_hang_up_after_goodbye(tenant, call, reason))
            # Not "say your goodbye now": the goodbye was said before this was
            # called, so that invited a second closing line, and the agent
            # said "Allah hafiz" and then asked whether there was anything
            # else. What happens if they speak again is said here, while the
            # model is still listening.
            return (
                "The call is ending. Your goodbye has been said: say nothing "
                "more. If the caller then asks something, answer it. If they "
                "only say goodbye back, call end_call again without speaking."
            )

        return "That is not something you can do on this call."

    return handle


#: An agent that ends a call because the caller has gone is not believed if
#: the caller was heard this recently.
CALLER_GONE_SECONDS = 5.0

_GONE_WORDS = ("rang off", "rung off", "hung up", "hang up", "hanged up",
               "has left", "caller left", "they left", "has gone", "caller gone",
               "disconnect", "dropped off", "line dropped", "silent", "silence",
               "no response", "not respond", "unresponsive", "no one there",
               "nobody there", "not there", "not answering")


def _claims_caller_gone(reason: str) -> bool:
    """Whether the agent's reason for ending says the caller is not there."""
    said = reason.lower()
    return any(word in said for word in _GONE_WORDS)


#: How long after the goodbye has played the line stays open for the caller
#: to say something. Longer than the echo tail, so their reply is not
#: mistaken for the agent's own voice coming back.
GOODBYE_GRACE_SECONDS = 2.0


async def _hang_up_after_goodbye(tenant: Tenant, call: Call, reason: str) -> None:
    """Let the farewell finish playing, give the caller a moment, then hang up.

    Cutting the line the instant the model calls `end_call` clips the goodbye
    mid-word, which sounds exactly like a dropped call. So this waits for the
    outgoing audio to drain — bounded, because a model that never stops
    talking must not hold the line open either.

    And it does not hang up on somebody who is talking. A caller who speaks
    after the goodbye ("wait, one more thing") keeps the line: the model
    hears them and answers, and it ends the call again when they are done.
    Only their own voice counts, not the agent's echo (see
    `CallSession.agent_may_be_echoing`), and every call still ends at
    `settings.max_call_seconds` whatever happens.
    """
    session = _sessions.get(call.id)
    asked_at = time.time()
    deadline = asked_at + 12
    try:
        while session is not None and time.time() < deadline:
            await asyncio.sleep(0.4)
            if not session.speaking:
                break
        await asyncio.sleep(GOODBYE_GRACE_SECONDS)
    except asyncio.CancelledError:
        await hang_up(tenant, call, reason)
        raise
    if (session is not None and _sessions.get(call.id) is session
            and session.caller_spoke_since(asked_at)):
        log.info("call %s: the caller spoke after the goodbye; staying on the line",
                 call.id)
        return
    await hang_up(tenant, call, reason)


async def hang_up(tenant: Tenant, call: Call, reason: str, *, error: str = "") -> None:
    """End the call with the provider and settle it here. Never raises.

    `error` is passed on to `finish`: set when the call is ending because the
    agent could not carry it.
    """
    from ...models.call import Channel
    from ...services import carriers
    from ...services.whatsapp import WhatsApp

    with contextlib.suppress(Exception):
        if call.provider_call_id and call.channel is Channel.PHONE:
            await carriers.hang_up(tenant, call.provider_call_id)
        elif call.provider_call_id and call.channel is Channel.WHATSAPP_CALL:
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
            from . import whatsapp_media

            await whatsapp_media.drop(call.id)
    await finish(tenant, call.id, reason, error=error)


def _when_agent_fails(tenant: Tenant, call: Call):
    """What a session does when its model refuses it or goes away.

    Three cases, by which session it was:

    * Not the one in the registry: an orphan or a superseded session. It
      ends quietly. It must not end a call another session is carrying.
    * A warmed-up session no line is attached to yet: dropped from the
      registry, so `start` builds a fresh one when the caller arrives. A key
      that was busy a moment ago may well be free by then.
    * The session the caller is on: the call is hung up with the carrier and
      recorded with the reason. Leaving it up meant silence until the caller
      gave up, and a history that called it "Completed".
    """
    async def handle(session: CallSession, reason: str) -> None:
        if _sessions.get(call.id) is not session:
            return
        if not session.attached:
            _sessions.pop(call.id, None)
            log.warning("call %s: the warmed-up agent was refused (%s); the line will "
                        "get a fresh one", call.id, reason)
            return
        log.warning("call %s: the agent cannot carry the call (%s); hanging up",
                    call.id, reason)
        from .. import platform_settings

        await hang_up(tenant, call, "the agent could not carry the call",
                      error=f"The agent could not carry the call: "
                            f"{platform_settings.explain(reason)}")

    return handle


#: Calls whose warm-up is building its session right now, and the event set
#: once it has registered one (or given up). `start` waits on it rather than
#: building a second session; see `start`.
_warming: dict[str, asyncio.Event] = {}

#: How long `start` waits for a warm-up already in progress. Building the
#: session is two database reads; the model connecting happens after the
#: session is registered and is not waited for here.
WARM_UP_WAIT_SECONDS = 8.0


def _discard(_frame: bytes) -> None:
    """Where a warmed session's audio goes until a transport arrives.

    Nothing reaches here in practice: the pacer holds its queue until it is
    ticked, and a warmed session is not ticked. This is the sink of last
    resort if one ever is.
    """


async def warm_up(tenant: Tenant, call: Call, *, keepalive: bool = True) -> None:
    """Connect the agent before the caller is on the line.

    Six seconds used to pass between a caller answering and hearing anything:
    one and a half to read the company's material, nearly four to open the
    model session, and the rest for the first word. All of it ran *after* the
    carrier had connected the leg, so all of it was silence the caller sat
    through — long enough that they say "hello?" first, which is how the call
    starts on the wrong foot.

    None of it needs the caller. Started when the leg is dialled instead, it
    overlaps the carrier's own setup, and the greeting is usually waiting in
    the pacer's queue before the socket arrives.

    Never raises: a warm-up that fails leaves `start` to do the work as before.
    """
    if call.id in _sessions:
        return
    if at_capacity():
        # Warming is an optimisation, so this is not an error: the call has not
        # arrived yet and may never. `start` decides whether it is admitted.
        log.info(
            "call %s: not warming the agent up, %d calls already running",
            call.id, len(_sessions),
        )
        return
    if call.mode == "human":
        # A dialer call is a person talking to a person. Warming a model for it
        # would put an agent on a line the employee is about to speak on.
        return
    if call.id in _warming:
        return
    # Claimed before the first await, so a line connecting while this reads
    # the database waits for this session instead of building a second one.
    # Two sessions for one call is two model connections: the key's limit on
    # open sessions refused the second ("out of quota"), the call ran on that
    # dead one, and the caller heard nothing.
    registered = asyncio.Event()
    _warming[call.id] = registered
    session: CallSession | None = None
    try:
        session = CallSession(
            call.id,
            send_to_caller=_discard,
            live_settings=await live_settings_now(tenant),
            persona=await persona_for(tenant, call),
            record=tenant.record_calls,
            keepalive=keepalive,
            on_transcript=lambda who, text: None,
            on_tool=_tool_handler(tenant, call),
        )
        session.attached = False
        session.on_agent_failed = _when_agent_fails(tenant, call)
        # Checked again, because building that session awaited the database
        # twice. `start` now waits for this rather than building its own, but
        # it stops waiting after `WARM_UP_WAIT_SECONDS`, and then whatever it
        # registered is the session on the line and must not be overwritten.
        #
        # What overwriting cost once: the real conversation ran on a session
        # nothing was tracking, while the one that got settled, recorded and
        # transcribed was this one, which had only ever spoken its greeting.
        if call.id in _sessions:
            log.info("call %s: the line connected while warming; standing down",
                     call.id)
            with contextlib.suppress(Exception):
                await session.end("superseded by the live session")
            return
        _sessions[call.id] = session
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not warm the agent up", call.id)
        return
    finally:
        _warming.pop(call.id, None)
        registered.set()

    try:
        await session.start(paced=False)
        if not session.closed:
            log.info("call %s: the agent is connected and waiting for the line", call.id)
    except Exception:  # noqa: BLE001
        if _sessions.get(call.id) is session:
            _sessions.pop(call.id, None)
        log.exception("call %s: could not warm the agent up", call.id)


async def start(
    tenant: Tenant,
    call: Call,
    send_to_caller: Callable[[bytes], None],
    *,
    keepalive: bool = True,
    on_clear: Callable[[], None] | None = None,
    wait_for_hello: bool = False,
) -> CallSession:
    """Answer a call: bring up the agent and start moving audio.

    `on_clear` is for a carrier that buffers audio on its side (Twilio): it is
    called when what was already sent should stop playing. See
    `CallSession.on_clear`.

    `wait_for_hello` holds the greeting until the person who picked up has
    said hello (an outbound call). See `CallSession.hold_for_hello`.

    Exactly one session per call. A warm-up still reading the database is
    waited for, not raced: a Twilio stream connects within half a second of
    the webhook, sooner than the warm-up registers, and both used to build a
    session. See `_warming`.
    """
    warming = _warming.get(call.id)
    if warming is not None:
        try:
            await asyncio.wait_for(warming.wait(), timeout=WARM_UP_WAIT_SECONDS)
        except asyncio.TimeoutError:
            log.warning("call %s: the warm-up took too long; starting the agent here",
                        call.id)

    existing = _sessions.get(call.id)
    if existing is not None and existing.closed:
        # Warmed, but its model was refused or went away before the caller
        # arrived. Attaching it would connect the caller to nobody.
        _sessions.pop(call.id, None)
        existing = None
    if existing is not None:
        return await _attach(existing, tenant, call, send_to_caller, on_clear,
                             wait_for_hello=wait_for_hello)

    # Past the warmed-session check above, so a call that was warmed while
    # there was room is still answered: it is already counted, and refusing it
    # here would drop a caller the carrier has connected.
    if at_capacity():
        log.warning(
            "call %s refused: %d calls already running, the limit is %d",
            call.id, len(_sessions), capacity(),
        )
        raise AtCapacity(
            f"This number is carrying {len(_sessions)} calls at once, which is "
            "all it is configured to take. Please ring back in a moment."
        )

    human = call.mode == "human"
    session = CallSession(
        call.id,
        send_to_caller=send_to_caller,
        live_settings=await live_settings_now(tenant),
        # A human call never starts the model, so it needs no persona.
        persona=AgentPersona(instructions="", greeting="") if human
        else await persona_for(tenant, call),
        record=tenant.record_calls,
        keepalive=keepalive,
        on_transcript=lambda who, text: None,
        on_tool=None if human else _tool_handler(tenant, call),
        human=human,
    )
    # Building awaited the database; a warm-up that outlasted the wait above
    # may have registered meanwhile. Use it rather than open a second model
    # session, which the key would refuse.
    other = _sessions.get(call.id)
    if other is not None and not other.closed:
        with contextlib.suppress(Exception):
            await session.end("superseded by the warmed-up session")
        return await _attach(other, tenant, call, send_to_caller, on_clear,
                             wait_for_hello=wait_for_hello)

    session.on_clear = on_clear
    session.on_agent_failed = _when_agent_fails(tenant, call)
    if wait_for_hello and not human:
        session.hold_for_hello()
    _sessions[call.id] = session
    await session.start()
    if session.closed:
        # Refused while starting ("out of quota"). `_when_agent_fails` hangs
        # the call up and files it as failed; marking it answered and in
        # progress here, after that, is how it used to read "Completed".
        return session

    await _mark_answered(tenant, call)
    log.info("call %s answered for %s (%d live)", call.id, tenant.phone_number_id, len(_sessions))
    return session


async def _attach(session: CallSession, tenant: Tenant, call: Call,
                  send_to_caller: Callable[[bytes], None],
                  on_clear: Callable[[], None] | None,
                  *, wait_for_hello: bool = False) -> CallSession:
    """Hand a warmed-up session the line; what it has already said plays
    from its first word (after the person's hello, with `wait_for_hello`)."""
    session.on_clear = on_clear
    if wait_for_hello and not session.human:
        # Before the transport, so not one frame of the greeting goes out first.
        session.hold_for_hello()
    session.attach_transport(send_to_caller)
    await _mark_answered(tenant, call)
    log.info("call %s answered for %s on the warmed-up agent (%d live)",
             call.id, tenant.phone_number_id, len(_sessions))
    return session


#: Every answered call's time limit, by call id. See `_limit`.
_time_limits: dict[str, asyncio.Task] = {}


def _limit(tenant: Tenant, call: Call) -> None:
    """End the call at `settings.max_call_seconds`, whatever else happens.

    The backstop for everything that keeps a line open on purpose — a caller
    who keeps talking after goodbyes, a model that never calls end_call — so
    no call can run, and bill, forever.
    """
    if call.id in _time_limits:
        return

    async def at_the_limit() -> None:
        await asyncio.sleep(settings.max_call_seconds)
        if call.id in _sessions:
            log.warning("call %s: reached the %ds limit; ending it", call.id,
                        settings.max_call_seconds)
            await hang_up(tenant, call, "the call reached its time limit")

    task = asyncio.create_task(at_the_limit())
    _time_limits[call.id] = task
    task.add_done_callback(lambda _t: _time_limits.pop(call.id, None))


async def _mark_answered(tenant: Tenant, call: Call) -> None:
    _limit(tenant, call)
    call.status = CallStatus.IN_PROGRESS
    call.answered_at = call.answered_at or time.time()
    if tenant.record_calls and call.recording_state is RecordingState.NONE:
        call.recording_state = RecordingState.PENDING
    await call_repo.save_call(call)


async def finish(tenant: Tenant, call_id: str, reason: str = "", *, error: str = "") -> None:
    """Settle a call: stop the agent, store the recording, save the transcript.

    Never raises. Called from a socket that has already closed, from a webhook,
    and from a `finally` — none of which have anywhere to put an exception, and
    all of which must still settle the row.

    `error` is why the agent could not carry the call. It is recorded on the
    call either way, and the call is filed as FAILED when the caller never
    heard the agent at all: a connected line with nobody on it is not a
    completed call, and filing it as one hid every one of these.
    """
    limit = _time_limits.pop(call_id, None)
    if limit is not None and limit is not asyncio.current_task():
        limit.cancel()
    session = _sessions.pop(call_id, None)
    if session is None:
        return
    try:
        audio = await session.end(reason)
    except Exception:  # noqa: BLE001
        log.exception("call %s: the session did not stop cleanly", call_id)
        audio = None

    try:
        call = await call_repo.get_call(tenant.phone_number_id, call_id)
        if call is None:
            return

        now = time.time()
        call.ended_at = call.ended_at or now
        if call.answered_at:
            call.duration_seconds = max(0, round(call.ended_at - call.answered_at))
            call.status = CallStatus.COMPLETED
        else:
            call.status = CallStatus.NO_ANSWER
        if error:
            call.error = error[:300]
            if session.frames_out == 0:
                call.status = CallStatus.FAILED
        if session.handler.value == "operator":
            call.handled_by = "operator"
        transcript = session.transcript_text()
        if transcript:
            # Hindi script as Roman Urdu straight away, so the row never holds
            # Devanagari; the text model then spells it properly, below.
            call.transcript = script.readable(transcript)
            # The speech model's own words, kept whenever they were rewritten.
            call.transcript_original = (
                transcript if call.transcript != transcript else "")
        if reason:
            call.metadata = {**call.metadata, "endedBecause": reason}

        if audio:
            # Transcoded and stored by the same path a browser upload takes, so
            # there is one answer to "where do recordings go".
            try:
                call = await recording_service.store_audio(
                    tenant, call, audio, "audio/wav", transcode=True
                )
            except Exception:  # noqa: BLE001
                # Storing is the one step that reaches the network, and the
                # row must not go down with it: without this, a bucket having
                # a bad minute cost the call its status and its transcript
                # too, and the record said the call never ended.
                log.exception("call %s: the recording could not be stored", call_id)
                call.recording_state = RecordingState.FAILED
                call.recording_error = "The recording could not be stored."
                with contextlib.suppress(Exception):
                    await call_repo.save_call(call)
        else:
            if call.recording_state is RecordingState.PENDING:
                call.recording_state = RecordingState.ABSENT
            await call_repo.save_call(call)

        reports.schedule_sync(tenant)
        log.info(
            "call %s settled: %ds, %s, recording %s",
            call_id, call.duration_seconds, call.status.value,
            call.recording_state.value.lower(),
        )
        if script.has_devanagari(transcript):
            task = asyncio.create_task(_respell_in_roman_urdu(tenant, call_id, transcript))
            _respelling.add(task)
            task.add_done_callback(_respelling.discard)
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not settle the row", call_id)


#: Transcripts being respelled after their call, held so none is collected
#: half-way through.
_respelling: set[asyncio.Task] = set()


async def _respell_in_roman_urdu(tenant: Tenant, call_id: str, transcript: str) -> None:
    """Rewrite a finished call's Hindi-script lines as Roman Urdu with the text
    model, which spells loanwords and Urdu words the way people write them.

    After the call is settled rather than before, so a slow or missing model
    never holds up the call's own record — that already reads in Roman Urdu,
    letter by letter, and this only improves it. Never raises.
    """
    try:
        chosen = await live_settings_now(tenant)
        # Only a Gemini key can call the Gemini text model.
        key = chosen.api_key if (chosen.engine or "gemini") == "gemini" else ""
        if not key:
            return
        speakers: list[str] = []
        said: list[str] = []
        for line in transcript.split("\n"):
            who, sep, text = line.partition(": ")
            if sep and who in ("caller", "agent"):
                speakers.append(f"{who}: ")
                said.append(text)
            else:
                speakers.append("")
                said.append(line)
        respelled = await script.to_roman_urdu(
            said, api_key=key, model=settings.gemini_text_model)
        call = await call_repo.get_call(tenant.phone_number_id, call_id)
        if call is None:
            return
        call.transcript = "\n".join(p + t for p, t in zip(speakers, respelled))
        # Only the readable copy changes; the original stays as heard.
        call.transcript_original = call.transcript_original or transcript
        await call_repo.save_call(call)
        reports.schedule_sync(tenant)
        log.info("call %s: transcript written in Roman Urdu", call_id)
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not respell the transcript", call_id)


async def end_all(reason: str = "shutting down") -> None:
    """Stop every live call. Used when the process is going away."""
    for limit in list(_time_limits.values()):
        # A task from a loop that has already closed cannot be cancelled, and
        # needs nothing more; shutting down must not fail over it.
        with contextlib.suppress(RuntimeError):
            limit.cancel()
    _time_limits.clear()
    for call_id, session in list(_sessions.items()):
        _sessions.pop(call_id, None)
        with contextlib.suppress(Exception):
            await session.end(reason)
    await asyncio.sleep(0)
