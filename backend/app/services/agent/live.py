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
from . import tools as tool_catalogue
from .gemini import AgentPersona, LiveSettings
from .session import CallSession

log = logging.getLogger(__name__)

_sessions: dict[str, CallSession] = {}


def get(call_id: str) -> CallSession | None:
    return _sessions.get(call_id)


def active() -> int:
    return len(_sessions)


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
                  "apiKey": settings.gemini_api_key.strip()}
    model = (tenant.engine_model.strip() if tenant else "") or chosen["model"]
    key = (tenant.engine_api_key.strip() if tenant else "") or chosen["apiKey"]
    return LiveSettings(model=model, api_key=key)


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
            "rather than forcing one."
            if language
            else "Speak whatever language the caller speaks, and mirror their "
                 "mix of languages rather than forcing one."
        ),
        pace=(voice.get("pace") or "").strip(),
        tone=voice.get("tone") or "",
        escalation=voice.get("escalation") or "",
        extra=voice.get("extraRules") or "",
        override=voice.get("promptOverride") or "",
        can_send_whatsapp=can_message,
    )

    return AgentPersona(
        instructions=instructions,
        greeting=(voice["greeting"] or "").strip(),
        voice=(voice["ttsVoice"] or "").strip(),
        language=language,
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
            log.info("call %s: the agent is ending the call (%s)", call.id, reason)
            asyncio.create_task(_hang_up_after_goodbye(tenant, call, reason))
            return "The call is ending. Say your goodbye now and stop talking."

        return "That is not something you can do on this call."

    return handle


async def _hang_up_after_goodbye(tenant: Tenant, call: Call, reason: str) -> None:
    """Let the farewell finish playing, then hang up.

    Cutting the line the instant the model calls `end_call` clips the goodbye
    mid-word, which sounds exactly like a dropped call. So this waits for the
    outgoing audio to drain — bounded, because a model that never stops
    talking must not hold the line open either.
    """
    session = _sessions.get(call.id)
    deadline = time.time() + 12
    try:
        while session is not None and time.time() < deadline:
            await asyncio.sleep(0.4)
            if not session.speaking:
                # A moment past the last frame, so the final word lands.
                await asyncio.sleep(0.6)
                break
    finally:
        await hang_up(tenant, call, reason)


async def hang_up(tenant: Tenant, call: Call, reason: str) -> None:
    """End the call with the provider and settle it here. Never raises."""
    from ...models.call import Channel
    from ...services.telephony import Infobip
    from ...services.whatsapp import WhatsApp

    with contextlib.suppress(Exception):
        if call.provider_call_id and call.channel is Channel.PHONE:
            await Infobip(tenant).hangup(call.provider_call_id)
        elif call.provider_call_id and call.channel is Channel.WHATSAPP_CALL:
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
            from . import whatsapp_media

            await whatsapp_media.drop(call.id)
    await finish(tenant, call.id, reason)


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
    if call.mode == "human":
        # A dialer call is a person talking to a person. Warming a model for it
        # would put an agent on a line the employee is about to speak on.
        return
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
        _sessions[call.id] = session
        await session.start(paced=False)
        log.info("call %s: the agent is connected and waiting for the line", call.id)
    except Exception:  # noqa: BLE001
        _sessions.pop(call.id, None)
        log.exception("call %s: could not warm the agent up", call.id)


async def start(
    tenant: Tenant,
    call: Call,
    send_to_caller: Callable[[bytes], None],
    *,
    keepalive: bool = True,
) -> CallSession:
    """Answer a call: bring up the agent and start moving audio."""
    existing = _sessions.get(call.id)
    if existing is not None:
        # Warmed while the carrier was connecting. Hand it the line; anything
        # it has already said is queued and plays from its first word.
        existing.attach_transport(send_to_caller)
        call.status = CallStatus.IN_PROGRESS
        call.answered_at = call.answered_at or time.time()
        if tenant.record_calls and call.recording_state is RecordingState.NONE:
            call.recording_state = RecordingState.PENDING
        await call_repo.save_call(call)
        return existing

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
    _sessions[call.id] = session
    await session.start()

    call.status = CallStatus.IN_PROGRESS
    call.answered_at = call.answered_at or time.time()
    if tenant.record_calls and call.recording_state is RecordingState.NONE:
        call.recording_state = RecordingState.PENDING
    await call_repo.save_call(call)
    log.info("call %s answered for %s (%d live)", call.id, tenant.phone_number_id, len(_sessions))
    return session


async def finish(tenant: Tenant, call_id: str, reason: str = "") -> None:
    """Settle a call: stop the agent, store the recording, save the transcript.

    Never raises. Called from a socket that has already closed, from a webhook,
    and from a `finally` — none of which have anywhere to put an exception, and
    all of which must still settle the row.
    """
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
        if session.handler.value == "operator":
            call.handled_by = "operator"
        transcript = session.transcript_text()
        if transcript:
            call.transcript = transcript
        if reason:
            call.metadata = {**call.metadata, "endedBecause": reason}

        if audio:
            # Transcoded and stored by the same path a browser upload takes, so
            # there is one answer to "where do recordings go".
            call = await recording_service.store_audio(
                tenant, call, audio, "audio/wav", transcode=True
            )
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
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not settle the row", call_id)


async def end_all(reason: str = "shutting down") -> None:
    """Stop every live call. Used when the process is going away."""
    for call_id, session in list(_sessions.items()):
        _sessions.pop(call_id, None)
        with contextlib.suppress(Exception):
            await session.end(reason)
    await asyncio.sleep(0)
