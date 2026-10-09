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
from .gemini import AgentPersona, LiveSettings
from .session import CallSession

log = logging.getLogger(__name__)

_sessions: dict[str, CallSession] = {}


def get(call_id: str) -> CallSession | None:
    return _sessions.get(call_id)


def active() -> int:
    return len(_sessions)


def live_settings() -> LiveSettings:
    return LiveSettings(
        model=settings.gemini_live_model,
        api_key=settings.gemini_api_key.strip(),
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
                 "language": tenant.language, "ttsVoice": tenant.tts_voice}
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
        )
        context = await routing.context_for(tenant, resolved)
        voice = routing.persona_for(tenant, resolved)
        # Recorded on the call so "why did it answer that?" stays answerable
        # after the setups have been edited.
        call.agent_id = resolved.agent_id
        call.knowledge_base_id = resolved.knowledge_base_id
        call.knowledge_base_name = resolved.knowledge_base_name
        call.resolved_by = resolved.resolved_by

    persona = (voice["persona"] or "").strip() or (
        "You are the voice assistant answering calls for this business. "
        "Be warm, brief and practical."
    )
    rules = [
        persona,
        "",
        "You are on a telephone call. Speak in short, natural sentences. Never "
        "read out punctuation, lists or formatting — there is nothing to look at.",
    ]
    language = (voice["language"] or "").strip()
    rules.append(
        f"Speak {language} unless the caller uses another language, in which "
        f"case follow them." if language
        else "Speak whatever language the caller speaks."
    )
    if context:
        rules += [
            "",
            "Answer only from the material below. If the answer is not in it, "
            "say you will check and have someone call back — never invent a "
            "figure, a date or a policy.",
            "",
            context,
        ]
    else:
        rules += [
            "",
            "You have no reference material, so do not state specific facts "
            "about this business. Offer to take a message instead.",
        ]
    return AgentPersona(
        instructions="\n".join(rules),
        greeting=(voice["greeting"] or "").strip(),
        voice=(voice["ttsVoice"] or "").strip(),
        language=language,
    )


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
        return existing

    session = CallSession(
        call.id,
        send_to_caller=send_to_caller,
        live_settings=live_settings(),
        persona=await persona_for(tenant, call),
        record=tenant.record_calls,
        keepalive=keepalive,
        on_transcript=lambda who, text: None,
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
