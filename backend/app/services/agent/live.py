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
import json
import logging
import re
import time
from typing import Callable

from ...config import settings
from ...models.call import Call, CallStatus, Direction, RecordingState
from ...models.tenant import Tenant
from ...repositories import calls as call_repo
from ...repositories import knowledge
from .. import recordings as recording_service
from .. import reports
from . import amounts
from . import calculator
from . import phones
from . import prompt
from . import script
from . import turns as turns_mod
from . import voices
from . import tools as tool_catalogue
from .gemini import AgentPersona, LiveSettings
from .session import CallSession

log = logging.getLogger(__name__)

_sessions: dict[str, CallSession] = {}

#: The most a tool may wait on the network. Function calling on the live
#: model is synchronous: the agent cannot speak until the tool returns, and
#: a caller hears every second of that as dead air.
TOOL_TIMEOUT_SECONDS = 5.0


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


async def persona_for(tenant: Tenant, call: Call | None = None,
                      timings: dict[str, float] | None = None) -> AgentPersona:
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
        clock = time.monotonic()
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
        _took(timings, "routing", clock)
        clock = time.monotonic()
        context = await routing.context_for(tenant, resolved)
        _took(timings, "documents", clock)
        voice = routing.persona_for(tenant, resolved)
        # Recorded on the call so "why did it answer that?" stays answerable
        # after the setups have been edited.
        call.agent_id = resolved.agent_id
        call.knowledge_base_id = resolved.knowledge_base_id
        call.knowledge_base_name = resolved.knowledge_base_name
        call.resolved_by = resolved.resolved_by

    language = (voice["language"] or "").strip()
    clock = time.monotonic()
    can_message = bool(call) and await _can_send_whatsapp(tenant)
    _took(timings, "WhatsApp check", clock)

    # Every word of this is now a block a company can read and change; see
    # `prompt.py`. It used to be a few hundred words of behaviour written
    # here, which a company could neither see nor argue with.
    instructions = prompt.build(
        persona=voice["persona"] or "",
        context=context,
        written=voice.get("promptBlocks") or {},
        language_rule=spoken_language_rule(language),
        writing_rule=written_language_rule(language),
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
        calling_number_ending=phones.ending(call.counterparty) if call else "",
    )

    # The agent's own "Opening line" field, word for word; without one, a
    # line in the agent's language and gender that names the organisation.
    greeting = (voice["greeting"] or "").strip() or prompt.default_greeting(
        tenant.name, language, voices.gender_of(voice.get("ttsVoice") or ""))
    return AgentPersona(
        instructions=instructions,
        greeting=greeting,
        voice=(voice["ttsVoice"] or "").strip(),
        language=language,
        pace=(voice.get("pace") or "").strip(),
        tools=tool_catalogue.for_call(can_send_whatsapp=can_message),
    )


def _took(timings: dict[str, float] | None, step: str, since: float) -> None:
    if timings is not None:
        timings[step] = time.monotonic() - since


def _timed(timings: dict[str, float], total: float) -> str:
    """For a log line: 4.2s (engine settings 0.1s, documents 3.9s, ...)."""
    steps = ", ".join(f"{step} {seconds:.1f}s" for step, seconds in timings.items())
    return f"{total:.1f}s ({steps})" if steps else f"{total:.1f}s"


#: Preparing an agent slower than this is logged as a warning.
SLOW_PREPARE_SECONDS = 2.0


async def _prepared(tenant: Tenant, call: Call) -> tuple[LiveSettings, AgentPersona]:
    """The engine and the agent for a call, timed step by step in the log.

    On a real call this took nine seconds once, the first after a deploy, and
    the caller heard nothing for all of it. Timed so the slow step is named
    in the log rather than guessed at.
    """
    timings: dict[str, float] = {}
    started = clock = time.monotonic()
    chosen = await live_settings_now(tenant)
    _took(timings, "engine settings", clock)
    persona = await persona_for(tenant, call, timings)
    total = time.monotonic() - started
    report = log.warning if total >= SLOW_PREPARE_SECONDS else log.info
    report("call %s: agent prepared in %s", call.id, _timed(timings, total))
    return chosen, persona


async def prepare() -> None:
    """Load what every first call needs, while nobody is waiting for it.

    The first call after a deploy found nothing loaded: the speech library,
    the engine settings, the connection to the database and every company's
    documents were all read while the caller sat in silence (nine seconds,
    once). Done here instead, in the background, when the server starts.
    The knowledge cache then keeps the documents warm between calls. Never
    raises: a call that finds something missing just reads it, as before.
    """
    import importlib

    from ...repositories import agents as agent_repo
    from ...repositories import tenants as tenant_repo
    from .. import platform_settings

    started = time.monotonic()
    try:
        # Heavy to import, and imported by the first model session otherwise.
        await asyncio.to_thread(importlib.import_module, "google.genai")
    except Exception:  # noqa: BLE001
        log.info("prepare: the speech library could not be loaded ahead of time")
    companies = 0
    try:
        await platform_settings.current()
        for tenant in await tenant_repo.list_all():
            try:
                await tenant_repo.get(tenant.phone_number_id)
                bases = await agent_repo.list_knowledge_bases(tenant.phone_number_id)
                spoken = amounts.wants_spoken(
                    tenant.language,
                    tenant.default_country_code or settings.default_country_code,
                )
                if not await knowledge.warm(tenant.phone_number_id,
                                            [kb.id for kb in bases],
                                            spoken_amounts=spoken):
                    log.info("prepare: the document cache is full (%d MB); the "
                             "remaining companies load on their first call",
                             settings.document_cache_mb)
                    break
                companies += 1
            except Exception:  # noqa: BLE001 — one company must not stop the rest
                log.exception("prepare: could not load company %s", tenant.phone_number_id)
    except Exception:  # noqa: BLE001
        log.exception("prepare: could not load the companies")
    log.info("prepare: ready for calls in %.1fs (%d companies' documents loaded, "
             "%.1f MB of %d MB document cache used)",
             time.monotonic() - started, companies,
             knowledge.cached_bytes() / (1024 * 1024), settings.document_cache_mb)


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


#: The "mirror their mix of languages" rule this replaced produced English
#: sentences inside Urdu calls. One language per call, in the caller's own.
_ONE_LANGUAGE = (
    "With an Urdu speaker, speak Urdu throughout; keep only model names, units "
    "and technical terms in English. Do not insert English sentences, and do "
    "not switch language mid-reply unless the caller switches."
)


def spoken_language_rule(language: str) -> str:
    """What the agent speaks, from the agent's language setting."""
    if language:
        return (f"Speak {_language_name(language)} unless the caller speaks another "
                f"language, in which case speak theirs. {_ONE_LANGUAGE}")
    return f"Speak the caller's language. {_ONE_LANGUAGE}"


def written_language_rule(language: str) -> str:
    """How a WhatsApp message is written: in the language's own script.

    Urdu and Hindi are one spoken language and two scripts, and a model left
    to choose picks Devanagari, which the people reading the message cannot.
    Scoped to messages: a transcript is the recogniser's, not the agent's.
    """
    if language:
        return (f"When you write a WhatsApp message, write {_language_name(language)} "
                "in its own script, the one its readers actually use. Never "
                "transliterate it into another script, and never use Devanagari "
                "for Urdu.")
    return ("When you write a WhatsApp message, write each language in its own "
            "script rather than transliterating it, and never use Devanagari for "
            "Urdu.")


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

    The guards here decide; the prompt only explains. Prompt rules alone had
    the agent say "sent" with nothing sent in thirteen calls out of thirteen,
    so each tool now checks the transcript for what the caller actually said
    before it acts.

    `pending` is this call's and nobody else's: a closure per call, so a
    read-back given on one call cannot confirm a send on another.
    """
    pending: dict[str, object] = {"confirmed": set()}
    _pending[call.id] = pending

    async def handle(name: str, args: dict) -> str:
        if name == "send_whatsapp_message":
            return await _send_message(tenant, call, args, pending)
        if name == "record_request":
            return await _record_request(tenant, call, args, pending)
        if name == "calculate":
            return _calculate(call.id, args)
        if name == "end_call":
            return await _end_call(tenant, call, args, pending)
        return "That is not something you can do on this call."

    return handle


def _confirmed_numbers(pending: dict) -> set[str]:
    """Numbers the caller has heard read back and said yes to on this call.

    One memory for both tools: a number confirmed for a request is not read
    back again for a message, and the other way round. On a real call the
    same number was read back twice and the caller asked why.
    """
    found = pending.get("confirmed")
    if not isinstance(found, set):
        found = set()
        pending["confirmed"] = found
    return found


# What the tools tell the model. Written as instructions, because the model
# reads a tool result as the next thing to act on.
QUEUED = (
    "Queued. Tell the caller the details will be sent on WhatsApp after the call. "
    "Do not say it has been sent."
)
QUEUED_AGAIN = (
    "Queued, replacing the message queued earlier for this number. Tell the caller "
    "the details will be sent on WhatsApp after the call. Do not say it has been sent."
)
CANCELLED = (
    "Cancelled: the caller said not to send it. Nothing is queued and nothing will be "
    "sent. Say briefly that it will not be sent. Queue again only if they ask again."
)
NOT_ASKED = (
    "NOT sent: the caller has not asked for this and has not said yes to an "
    "offer. Give the information on the call. If they would like it on WhatsApp "
    "as well, offer once and wait for a clear yes."
)
NOT_SPOKEN = (
    "NOT sent: the caller has not said that number on this call. Ask them to "
    "say the number, then call again."
)
NOT_SAME_NUMBER = (
    "NOT sent: the caller has not said to use the number they are calling from. "
    "Ask which number to send it to."
)
NO_SENDER = (
    "NOT sent: this company has no WhatsApp number that can send right now. "
    "Give the information on the call, and do not promise a message."
)
NOT_YET = (
    "The caller may not be finished: answer what they actually said. If they "
    "said goodbye, say the closing line once and call end_call again."
)
NOT_SAVED = (
    "Not recorded: the request could not be saved. Tell the caller plainly that "
    "it could not be recorded, give them the customer-care route from your "
    "material (the helpline or care centre) so they can make it there, and do "
    "not give a reference or say it is noted."
)
#: How long the end-call guard waits for the caller's last words to arrive
#: as text before judging them. Transcription trails speech by about a
#: second; judging the turn before it refused a goodbye that had been said.
TRANSCRIPT_WAIT_SECONDS = 1.5


def _country(tenant: Tenant) -> str:
    return (tenant.default_country_code or settings.default_country_code).strip()


def _conversation(call_id: str) -> list[tuple[str, str]] | None:
    """The call's turns so far, or None when there is no session to ask."""
    session = _sessions.get(call_id)
    if session is None:
        return None
    turns = getattr(session, "turns", None)
    if callable(turns):
        return turns()
    transcript = getattr(session, "transcript", None)
    if isinstance(transcript, list):
        return turns_mod.turns(transcript)
    return None


async def _note_on_call(tenant: Tenant, call: Call, line: str) -> None:
    """Append one line to the call's summary, which the call page shows.

    Read fresh and written back, so a line added mid-call survives the
    settling that happens when it ends. Never raises, and never waits long:
    this runs inside a tool call.
    """
    line = script.strip_meta(line)
    try:
        async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
            fresh = await call_repo.get_call(tenant.phone_number_id, call.id)
            target = fresh or call
            target.summary = f"{target.summary}\n{line}" if target.summary else line
            call.summary = target.summary
            await call_repo.save_call(target)
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not note %r on the call", call.id, line)


async def _send_message(tenant: Tenant, call: Call, args: dict, pending: dict[str, str]) -> str:
    """Queue a WhatsApp message, only when the transcript says the caller
    wants it. It is sent when the call ends (`flush_queued_messages`).

    Five checks, in order: they asked or said yes; the number is a whole
    number; it is a number they said on this call (or they said to use the
    one they are calling from); it was read back and confirmed; and only
    then is it queued. Sending during the call was the problem: on a real
    call the message left on "ji haan" while "balki nahin bheje" was still
    being transcribed, and the agent then denied what the caller had in
    their hand. Queued, a late no still cancels it.
    """
    from .. import lines

    text = str(args.get("text") or "").strip()
    if not text:
        return "No message was queued: there was nothing to say."
    conversation = _conversation(call.id) or []
    last_caller = turns_mod.last_turn(conversation, "caller")
    if turns_mod.last_word_on_sending(last_caller) == "no" and _cancel_queued(call, pending):
        return CANCELLED
    if not turns_mod.consented_to_message(conversation):
        return NOT_ASKED
    to = str(args.get("to") or "").strip()
    if not to:
        return "NOT sent: ask which number to send it to, then call again with it."

    country = _country(tenant)
    # What the caller most recently chose decides, not what the model passed.
    # On a real call the caller said "the number I'm calling from" and the
    # model passed the number they had given earlier for a request, which had
    # already been confirmed, so it would have gone out unread.
    chosen = _caller_chose(conversation, call, country)
    if phones.normalise_digits(to).lower().replace(" ", "_") == phones.CALLING_NUMBER:
        destination = call.counterparty
        if chosen and chosen[0] == "number" and chosen[1] != destination:
            # They chose a number they dictated ("that number I gave you");
            # the model passed the calling number instead.
            pending["number"] = chosen[1]
            theirs = _spoken_number(chosen[1], country)
            return (f"Not sent yet: the caller last gave {theirs}, not the number they are "
                    f"calling from. Read this back and ask them to confirm: {theirs}. Only "
                    "then call again with that number and number_confirmed true.")
        if not turns_mod.said_calling_number(conversation):
            return NOT_SAME_NUMBER
        if not destination:
            return "NOT sent: the number they are calling from is not known. Ask which number to use."
        spoken = f"the number ending {phones.ending(destination)}"
    else:
        parsed = phones.parse(to, country)
        if parsed is None:
            return "Not sent: that number looks incomplete. Ask for it again."
        destination, national = parsed
        if chosen and chosen[0] == "calling" and destination != chosen[1]:
            pending["number"] = chosen[1]
            return (f"Not sent yet: the caller chose the number they are calling from, not "
                    f"{phones.grouped(national)}. Read this back and ask them to confirm: the "
                    f"number ending {phones.ending(chosen[1])}. Only then call again with to "
                    "calling_number and number_confirmed true.")
        if chosen and chosen[0] == "number" and destination != chosen[1]:
            pending["number"] = chosen[1]
            theirs = _spoken_number(chosen[1], country)
            return (f"Not sent yet: the caller last gave {theirs}, not "
                    f"{phones.grouped(national)}. Read this back and ask them to confirm: "
                    f"{theirs}. Only then call again with that number and number_confirmed true.")
        if not phones.said_on_call(turns_mod.caller_lines(conversation), national):
            return NOT_SPOKEN
        spoken = phones.grouped(national)
    key = destination

    confirmed = bool(args.get("number_confirmed"))
    already = key in _confirmed_numbers(pending)
    if not already and (not confirmed or pending.get("number") != key
                        or not turns_mod.last_caller_affirmative(conversation)):
        pending["number"] = key
        return (f"Not sent yet. Read this back and ask the caller to confirm: {spoken}. "
                "Only then call again with number_confirmed true.")

    try:
        sender = await asyncio.wait_for(lines.whatsapp_sender(tenant), TOOL_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — a slow lookup is a line that cannot send now
        log.exception("call %s: could not find the WhatsApp sender in time", call.id)
        sender = None
    if sender is None:
        return NO_SENDER
    pending.pop("number", None)
    _confirmed_numbers(pending).add(key)
    queue: list[dict] = pending.setdefault("queued", [])
    replaced = False
    for message in queue:
        if message["to"] == destination and message["state"] == "queued":
            message["text"] = text
            message["queued_at"] = time.time()
            replaced = True
    if not replaced:
        queue.append({"to": destination, "text": text, "state": "queued",
                      "queued_at": time.time()})
    log.info("call %s: a WhatsApp to %s is queued for after the call", call.id,
             phones.ending(destination).rjust(5, "…"))
    await _note_on_call(tenant, call, f"WhatsApp to …{phones.ending(destination)}: queued, "
                                      "to be sent after the call")
    return QUEUED_AGAIN if replaced else QUEUED


def queued_messages(pending: dict | None) -> list[dict]:
    """The messages waiting to go out after the call."""
    return [m for m in (pending or {}).get("queued") or [] if m.get("state") == "queued"]


def _cancel_queued(call: Call, pending: dict) -> bool:
    """Drop every queued message: the caller said not to send. True if
    there was anything to drop."""
    waiting = queued_messages(pending)
    for message in waiting:
        message["state"] = "cancelled"
    if waiting:
        log.info("call %s: the caller cancelled the WhatsApp message (%d queued)",
                 call.id, len(waiting))
    return bool(waiting)


async def flush_queued_messages(tenant: Tenant, call: Call, pending: dict | None) -> list[str]:
    """Send what the call queued, now that it is over. Once each, never a
    cancelled one. Each result goes into the call's summary and the log,
    with the number masked. Never raises."""
    from .. import lines
    from ...services import whatsapp as whatsapp_service
    from ...services.whatsapp import mask

    results: list[str] = []
    for message in queued_messages(pending):
        message["state"] = "sending"
        destination, text = message["to"], message["text"]
        ending = phones.ending(destination)
        try:
            sender = await asyncio.wait_for(lines.whatsapp_sender(tenant), TOOL_TIMEOUT_SECONDS)
            if sender is None:
                raise RuntimeError("no WhatsApp number can send right now")
            how = await asyncio.wait_for(
                _deliver(sender, destination, text, call), TOOL_TIMEOUT_SECONDS + 1)
        except Exception as exc:  # noqa: BLE001
            message["state"] = "failed"
            rejected = whatsapp_service.auth_rejected(exc)
            why = "token rejected" if rejected else _refusal_reason(exc)
            log.warning("call %s: the queued WhatsApp to %s did not send after the call "
                        "(%s; %s)", call.id, mask(destination), why, type(exc).__name__)
            if rejected:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(lines.note_send_auth_failure(sender),
                                           TOOL_TIMEOUT_SECONDS)
            await _note_on_call(tenant, call, f"WhatsApp to …{ending}: not sent ({why})")
            results.append(f"not sent ({why})")
            continue
        message["state"] = "sent"
        log.info("call %s: sent the queued WhatsApp %s to %s after the call",
                 call.id, how, mask(destination))
        await _note_on_call(tenant, call, f"WhatsApp to …{ending}: sent after the call ({how})")
        results.append(f"sent ({how})")
    return results


#: Meta's customer-service window: a plain text reaches only somebody who
#: wrote to the business this recently. Everyone else needs a template.
CUSTOMER_WINDOW_SECONDS = 24 * 3600


async def _messaged_us_recently(sender: Tenant, destination: str) -> bool:
    """Whether this number wrote to the company inside Meta's 24-hour window."""
    from ...models.call import MessageDirection

    try:
        messages = await asyncio.wait_for(
            call_repo.list_messages(sender.phone_number_id, counterparty=destination, limit=20),
            TOOL_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — unknown is treated as "not recently"
        log.exception("tenant %s: could not read recent messages", sender.phone_number_id)
        return False
    since = time.time() - CUSTOMER_WINDOW_SECONDS
    return any(m.direction is MessageDirection.INBOUND and m.created_at >= since
               for m in messages)


async def _deliver(sender: Tenant, destination: str, text: str, call: Call) -> str:
    """Send `text` the way Meta will deliver it, and say which way that was.

    A plain text inside the 24-hour window; otherwise the approved follow-up
    template, with the company's name in {{1}} and the message in {{2}}.
    Sent as plain text, a phone caller's message was accepted by Meta and
    never delivered, which is a worse failure than a refusal: the agent said
    "sent" and meant it.
    """
    from ...services.whatsapp import WhatsApp, clean_parameter, strip_pleasantries

    client = WhatsApp(sender)
    if await _messaged_us_recently(sender, destination):
        await client.send_text(destination, text, call_id=call.id, timeout=TOOL_TIMEOUT_SECONDS)
        return "text"
    # The template greets and closes by itself; a greeting and a sign-off
    # inside the details read twice.
    body = clean_parameter(strip_pleasantries(text))
    await client.send_template(
        destination, settings.whatsapp_followup_template.strip() or "call_followup",
        language=settings.whatsapp_followup_language.strip() or "en",
        parameters=[await _sender_name(sender, call), body],
        call_id=call.id, timeout=TOOL_TIMEOUT_SECONDS, body=body,
    )
    return "template"


async def _sender_name(tenant: Tenant, call: Call) -> str:
    """{{1}} of the follow-up: the agent the caller spoke to, by name.

    The agent's name ("BYD Pakistan"), not the account's ("Calling Agent"),
    which a real follow-up arrived signed with. The company's name only when
    the call had no named agent.
    """
    from ...repositories import agents as agent_repo

    if call.agent_id:
        try:
            agent = await asyncio.wait_for(
                agent_repo.get_agent(tenant.phone_number_id, call.agent_id), TOOL_TIMEOUT_SECONDS)
            if agent is not None and agent.name.strip():
                return agent.name.strip()
        except Exception:  # noqa: BLE001 — the company's name still serves
            log.exception("call %s: could not read the agent's name", call.id)
    return (tenant.name or "").strip() or "the team"


def _refusal_reason(exc: BaseException) -> str:
    """Why a send failed, in the provider's own words where it gave any."""
    detail = getattr(exc, "details", None)
    text = str(detail or exc or type(exc).__name__).strip()
    text = text.replace("\n", " ")[:160]
    return f"could not be sent: {text}" if text else "could not be sent"


def _spoken_number(e164: str, country: str) -> str:
    """A number as it is read back: grouped, in its national form."""
    parsed = phones.parse(e164, country)
    return phones.grouped(parsed[1]) if parsed else e164


def _caller_chose(conversation: list[tuple[str, str]], call: Call,
                  country: str) -> tuple[str, str] | None:
    """The number the caller most recently chose, from their own words.

    `("number", e164)` for a number they dictated, `("calling", e164)` for
    "the number I am calling from", and a "that number" points back at the
    one they dictated before. None when they have not said. Read from the
    latest caller turn backwards, so a choice made a minute ago does not
    outrank one made just now.
    """
    for index in range(len(conversation) - 1, -1, -1):
        who, text = conversation[index]
        if who != "caller":
            continue
        found = phones.find_number(text, country)
        if found is not None:
            return ("number", found[0])
        if turns_mod.mentions_calling_number(text):
            # "Ji, yahi number hai" straight after the agent read a number
            # back is "yes, that one", not "the number I am calling from".
            agent_before = next((t for w, t in reversed(conversation[:index]) if w == "agent"), "")
            read_back = (phones.find_number(agent_before, country)
                         if turns_mod.is_affirmative(text) else None)
            if read_back is not None:
                return ("number", read_back[0])
            if call.counterparty:
                return ("calling", call.counterparty)
        if turns_mod.mentions_earlier_number(text):
            for earlier_who, earlier in reversed(conversation[:index]):
                if earlier_who != "caller":
                    continue
                found = phones.find_number(earlier, country)
                if found is not None:
                    return ("number", found[0])
    return None


def _read_back(kind, values: dict, grouped_mobile: str, *, mobile_confirmed: bool = False) -> str:
    """One paragraph the agent reads to the caller before anything is stored."""
    what = kind.value.replace("_", " ")
    pieces = [f"A {what} request"]
    if values.get("name"):
        pieces.append(f"for {values['name']}")
    if grouped_mobile and mobile_confirmed:
        pieces.append(f"the mobile ending {grouped_mobile[-4:]} they already confirmed")
    elif grouped_mobile:
        pieces.append(f"mobile {grouped_mobile}")
    if values.get("city"):
        pieces.append(f"in {values['city']}")
    if values.get("location"):
        pieces.append(f"at {values['location']}")
    if values.get("vehicle_model"):
        pieces.append(f"for the {values['vehicle_model']}")
    if values.get("registration"):
        pieces.append(f"registration {values['registration']}")
    if values.get("mileage"):
        pieces.append(f"mileage {values['mileage']}")
    if values.get("preferred_times"):
        pieces.append("preferred " + " or ".join(values["preferred_times"]))
    if values.get("occupation"):
        pieces.append(f"occupation {values['occupation']}")
    if values.get("purchase_timeline"):
        pieces.append(f"planning to buy {values['purchase_timeline']}")
    sentence = ", ".join(pieces) + "."
    if values.get("details"):
        sentence += f" Details: {values['details']}."
    sentence += f" The team will reply by {values.get('reply_by', 'call')}."
    return sentence


#: The tool's answer when the details are complete but not yet confirmed.
#: An instruction to act on, not a sentence to repeat: on a real call the
#: agent turned "when the caller says it is right" into a "conditions" step
#: it announced to the caller.
#: How a missing detail is asked for. An instruction, not a verdict.
ASK_NEXT = "Ask next for "
READ_BACK_INSTRUCTION = (
    "Not recorded yet. Read this back once, in the caller's language, and ask if "
    "it is right. When they say yes, call record_request again with confirmed "
    "true at once: no second read-back, no asking permission, no extra "
    "conditions. Do not ask for optional details. "
)


def _agent_read_back(conversation: list[tuple[str, str]], mobile: str) -> bool:
    """Whether the agent's turn before the caller's last one read the
    request back: the mobile number, in digits or in words, is the mark."""
    if not mobile or not conversation:
        return False
    agent_turn = ""
    seen_caller = False
    for who, text in reversed(conversation):
        if who == "caller" and not seen_caller:
            seen_caller = True
        elif who == "agent" and seen_caller:
            agent_turn = text
            break
    return bool(agent_turn) and phones.read_back_in(agent_turn, mobile)


async def _record_request(tenant: Tenant, call: Call, args: dict,
                          pending: dict[str, str]) -> str:
    """Record what the caller asked for, once it is complete and read back.

    Nothing is stored until the required details are there, the mobile is a
    whole number the caller said on this call, and the caller has heard the
    read-back and said it is right. The reference comes from the repository:
    the model never makes one up.
    """
    from ...models.request import ReplyBy, RequestType, missing_fields
    from ...repositories import requests as request_repo

    pending["request_started"] = True
    try:
        kind = RequestType(str(args.get("type") or "other").strip().lower())
    except ValueError:
        kind = RequestType.OTHER
    # Everything the caller gave on an earlier call to this tool stays given:
    # on a real call the model dropped the model name it had been told and
    # asked for it again ("main model aapko pahle hi bata chuka hun").
    given: dict = pending.setdefault("given", {})
    earlier = dict(given.get(kind.value) or {})
    args = {**earlier, **{k: v for k, v in args.items() if v not in (None, "", [])}}
    given[kind.value] = {k: v for k, v in args.items() if k != "confirmed" and v}
    times = args.get("preferred_times") or []
    if isinstance(times, str):
        times = [times]
    values = {
        "name": str(args.get("name") or "").strip(),
        "mobile": "",
        "city": str(args.get("city") or "").strip(),
        "location": str(args.get("location") or "").strip(),
        "preferred_times": [str(t).strip() for t in list(times)[:2] if str(t).strip()],
        "vehicle_model": str(args.get("vehicle_model") or "").strip(),
        "registration": str(args.get("registration") or "").strip(),
        "mileage": str(args.get("mileage") or "").strip(),
        "occupation": str(args.get("occupation") or "").strip(),
        "purchase_timeline": str(args.get("purchase_timeline") or "").strip(),
        "details": str(args.get("details") or "").strip(),
        "reply_by": ("whatsapp" if kind is RequestType.QUESTION
                     else str(args.get("reply_by") or "call").strip().lower()),
    }
    if values["reply_by"] not in {r.value for r in ReplyBy}:
        values["reply_by"] = "call"

    conversation = _conversation(call.id) or []
    grouped_mobile = ""
    mobile_confirmed = False
    mobile = str(args.get("mobile") or "").strip()
    if mobile:
        parsed = phones.parse(mobile, _country(tenant))
        if parsed is None:
            return ("Not recorded yet: that mobile number looks incomplete. A Pakistani "
                    "mobile has 11 digits starting 03. Ask for it again.")
        e164, national = parsed
        if not phones.said_on_call(turns_mod.caller_lines(conversation), national):
            return ("Not recorded yet: the caller has not said that number on this "
                    "call. Ask them to say the number, then call again.")
        values["mobile"] = e164
        grouped_mobile = phones.grouped(national)
        mobile_confirmed = e164 in _confirmed_numbers(pending)

    missing = missing_fields(kind, values)
    if missing:
        # The next one or two only: named all at once, the agent asked for
        # five things in one breath.
        ask = " and ".join(missing[:2])
        rest = len(missing) - len(missing[:2])
        # Not "not recorded": the model turned that into "shikayat darj nahi
        # ho saki" on a real call. Nothing has failed; a detail is wanted.
        return (f"{ASK_NEXT}{ask}"
                + (f"; {rest} more after that" if rest > 0 else "")
                + ". Nothing has failed: do not mention recording or any problem to the "
                "caller. Ask for these, at most two per turn, then call record_request "
                "again with every detail they have given.")

    fingerprint = json.dumps({**values, "type": kind.value}, sort_keys=True)
    already = pending.get("recorded") or {}
    if fingerprint in already:
        return (f"Already recorded. Reference {already[fingerprint]}. Do not record it "
                "again; give the reference if the caller asks.")
    confirmed = bool(args.get("confirmed"))
    # The caller has heard these details and said yes: read back by this
    # tool, or by the agent in its own words, which the number in its turn
    # shows. Insisting on the tool's own read-back after the agent's sent a
    # real call round a second read-back and a "conditions" step.
    heard = (pending.get("request") == fingerprint
             or _agent_read_back(conversation, values["mobile"]))
    if not confirmed or not heard or not turns_mod.last_caller_affirmative(conversation):
        pending["request"] = fingerprint
        return (READ_BACK_INSTRUCTION
                + _read_back(kind, values, grouped_mobile, mobile_confirmed=mobile_confirmed))

    try:
        record = await asyncio.wait_for(
            request_repo.create(tenant, call, kind, values), TOOL_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        log.exception("call %s: the request could not be stored", call.id)
        return NOT_SAVED
    pending.pop("request", None)
    pending.pop("request_started", None)
    pending.setdefault("recorded", {})[fingerprint] = record.reference
    if values["mobile"]:
        _confirmed_numbers(pending).add(values["mobile"])
    _recorded.setdefault(call.id, []).append(record.reference)
    await _note_on_call(tenant, call, "Request recorded: " + record.one_line())
    if kind is RequestType.QUESTION:
        return (f"Recorded. Reference {record.reference}. Say the team will send the "
                "answer on WhatsApp. Do not say anything has been sent.")
    return (f"Recorded. Reference {record.reference}. Tell the caller it is a request, "
            "not a confirmation, then give the next step from your material: who will "
            "contact them and by when, and the customer-care route if it has one.")


#: Every figure the calculator was given or gave on each live call, so what
#: the agent then says can be checked against it.
_calculated: dict[str, list[float]] = {}


def _calculate(call_id: str, args: dict) -> str:
    """The sum, and the exact words to say it in.

    On a real call the agent was given 210000 and said "20 lakh 10 hazar":
    the figure was right and the reading of it was not. The reply now says
    the words, so there is nothing left to read.
    """
    expression = str(args.get("expression") or "")
    try:
        result = calculator.evaluate(expression)
    except calculator.CalculationError as exc:
        return f"Could not calculate that: {exc}. Write the sum with numbers and + - * / only."
    known = _calculated.setdefault(call_id, [])
    known.extend(amounts.values_in(expression))
    try:
        value = float(result)
    except ValueError:
        return result
    known.append(value)
    if abs(value) >= 1_000 and value.is_integer():
        return (f"{result}. Say exactly: {amounts.spoken_pkr(value)} rupay. Never re-word "
                "or round it.")
    return f"{result}. Say exactly: {result}. Never re-word or round it."


#: Reasons for ending that are about the caller not taking part, which the
#: transcript cannot confirm and only the line can: nobody there, or nobody
#: worth talking to.
_NOT_ENGAGING_WORDS = (
    "not engaging", "abusive", "testing", "tester", "repeating", "repeat",
    "going nowhere", "nowhere", "silent", "silence", "no response", "not respond",
    "unresponsive", "prank", "time waster", "wasting time", "nobody there",
    "no one there", "not there",
)


def _claims_not_engaging(reason: str) -> bool:
    said = reason.lower()
    return any(word in said for word in _NOT_ENGAGING_WORDS)


def _may_end(conversation: list[tuple[str, str]], reason: str, session) -> bool:
    """Whether the call may end now: the caller is done, or is not taking part."""
    if turns_mod.may_end(conversation):
        return True
    spoke_within = getattr(session, "caller_spoke_within", None)
    recently = bool(spoke_within(CALLER_GONE_SECONDS)) if callable(spoke_within) else False
    return _claims_not_engaging(reason) and not recently


async def _let_transcription_land(session) -> None:
    """Wait, briefly, for the caller's last words to arrive as text.

    The caller's speech reaches `caller_spoke_at` at once and the transcript
    about a second later. A guard that judged the turn before refused
    goodbyes that had already been said.
    """
    transcribed_at = getattr(session, "caller_transcribed_at", None)
    if transcribed_at is None:
        return
    deadline = time.time() + TRANSCRIPT_WAIT_SECONDS
    while time.time() < deadline:
        if session.caller_transcribed_at >= session.caller_spoke_at:
            return
        await asyncio.sleep(0.1)


async def _end_call(tenant: Tenant, call: Call, args: dict, pending: dict) -> str:
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
    # The transcript, not the model, says whether the caller is done. On
    # real calls the agent hung up right after answering ("...Aur koi sawal?
    # Allah Hafiz."), and once said "the caller said goodbye" over a caller
    # who had said "nahin". It may end after a goodbye, a "nothing else", a
    # no to "anything else?", a request to end the call, or its own closing
    # line.
    #
    # And it is refused at most once per call. Refused four times on a real
    # call, with a goodbye it could not read, it asked "Kya main call end kar
    # doon?" until the caller hung up on it; the one check that can be wrong
    # must not be able to hold a line hostage. The one exception is a request
    # half taken: that is refused until the caller says goodbye or asks to
    # end, because a booking with the name and number taken and nothing
    # recorded is the call having failed at the one thing it was for.
    if session is not None:
        await _let_transcription_land(session)
    conversation = _conversation(call.id)
    if conversation is not None:
        _index, last_words = turns_mod.last_meaningful_caller_turn(conversation)
        explicit = turns_mod.is_explicit_end(last_words)
        if not explicit and _request_in_progress(call.id, conversation, pending, tenant):
            spoke_within = getattr(session, "caller_spoke_within", None)
            heard_lately = (bool(spoke_within(SILENT_CALLER_SECONDS))
                            if callable(spoke_within) else True)
            if heard_lately or not _claims_not_engaging(reason):
                log.info("call %s: not ending the call: a request is in progress and the "
                         "caller has not said goodbye (the agent said %r; the caller's "
                         "last words were %r)", call.id, reason, turns_mod.judged(last_words))
                return REQUEST_PENDING
            log.info("call %s: ending the call with a request in progress: the caller "
                     "has said nothing for %.0f seconds", call.id, SILENT_CALLER_SECONDS)
        refused_before = int(pending.get("end_refusals") or 0)
        if refused_before < 1 and not _may_end(conversation, reason, session):
            pending["end_refusals"] = refused_before + 1
            if _claims_goodbye(reason):
                log.info("call %s: the agent said the caller said goodbye, but their last "
                         "words were %r; not ending the call yet", call.id,
                         turns_mod.judged(last_words))
                return NOT_GOODBYE
            log.info("call %s: not ending the call yet (%r); the caller's last words "
                     "were %r", call.id, reason,
                     turns_mod.judged(turns_mod.last_turn(conversation, "caller")))
            return NOT_YET
    log.info("call %s: the agent is ending the call (%s)", call.id, reason)
    hold = getattr(session, "hold_after_goodbye", None)
    if (callable(hold) and conversation
            and turns_mod.agent_said_closing(turns_mod.last_turn(conversation, "agent"))):
        # The goodbye is said. Anything more the model says before the caller
        # does is the goodbye again, and it is not heard.
        hold()
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


#: What the agent is told when it tries to end a call with a request half
#: taken, and when it says the caller said goodbye and they did not.
REQUEST_PENDING = (
    "Do not end the call: the caller's request is not recorded yet. If they "
    "have confirmed the details, call record_request now with confirmed true. "
    "If a required detail is missing, ask for it. End only after the reference "
    "is given, or if the caller says goodbye or asks to end the call."
)
NOT_GOODBYE = (
    "The caller has not said goodbye. Answer what they actually said. End the "
    "call only after they say goodbye, say there is nothing else, or say no "
    "when you ask whether there is anything else."
)

#: A caller who has said nothing for this long may be gone: the one case a
#: request in progress does not keep the line open.
SILENT_CALLER_SECONDS = 20.0

_GOODBYE_CLAIMS = ("goodbye", "good bye", "bye", "farewell", "hafiz", "said bye")
#: Words in the agent's turns that show a request is being taken, not a
#: message sent: the caller's number plus one of these is a request in
#: progress until a reference exists.
_REQUEST_FLOW_WORDS = turns_mod._phrases(
    "test drive", "test draiw", "request", "record", "book", "booking", "callback",
    "call back", "complaint", "shikayat", "darj", "cancellation",
)


def _claims_goodbye(reason: str) -> bool:
    """Whether the agent's reason for ending says the caller said goodbye."""
    said = reason.lower()
    return any(word in said for word in _GOODBYE_CLAIMS)


def _request_in_progress(call_id: str, conversation: list[tuple[str, str]],
                         pending: dict, tenant: Tenant) -> bool:
    """A request the caller started, with no reference for it yet.

    Known for certain once record_request has been called and has not yet
    returned a reference. Before that, from the call itself: the caller has
    given a mobile number that has not been used for a message or a record,
    while the agent has been taking a test drive, a booking, a complaint or
    a callback.
    """
    if pending.get("request") or pending.get("request_started"):
        return True
    if _recorded.get(call_id):
        return False
    if not any(turns_mod._has_any(t, _REQUEST_FLOW_WORDS) for w, t in conversation if w == "agent"):
        return False
    confirmed = pending.get("confirmed") or set()
    for who, text in conversation:
        if who != "caller":
            continue
        found = phones.find_number(text, _country(tenant))
        if found is not None and found[0] not in confirmed:
            return True
    return False


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

    And it does not hang up on somebody who asks something. A caller who
    speaks after the goodbye keeps the line only if what they said is a real
    new request ("wait, what about the SEAL?"): the model hears them and
    answers, and ends the call again when they are done. Noise, "ok", a
    thank-you or a goodbye back do not keep it; on a real call a
    mis-transcribed "Good night, sis" did. Only the caller's own voice
    counts, not the agent's echo (`CallSession.agent_may_be_echoing`), and
    every call still ends at `settings.max_call_seconds` whatever happens.
    """
    session = _sessions.get(call.id)
    asked_at = time.time()
    since = len(getattr(session, "transcript", []) or [])
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
        await _let_transcription_land(session)
        heard = _caller_said_since(session, since)
        if turns_mod.is_new_request(heard):
            log.info("call %s: the caller asked something after the goodbye (%r); "
                     "staying on the line", call.id, turns_mod.judged(heard))
            _release_hold(session)
            return
        log.info("call %s: heard %r after the goodbye, not a new request; ending",
                 call.id, turns_mod.judged(heard) or "nothing transcribed")
    await hang_up(tenant, call, reason)


def _release_hold(session) -> None:
    """The call goes on: what the model said after the goodbye is heard."""
    release = getattr(session, "release_hold", None)
    if callable(release):
        release()


def _caller_said_since(session, index: int) -> str:
    said = getattr(session, "caller_said_since", None)
    if callable(said):
        return said(index)
    transcript = getattr(session, "transcript", None) or []
    return " ".join(t.strip() for w, t in transcript[index:] if w == "caller" and t.strip())


#: Tasks started and left to run: a reference keeps the loop from dropping
#: them half way (asyncio only holds weak references to tasks).
_background: set[asyncio.Task] = set()


def _keep(task: asyncio.Task) -> asyncio.Task:
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def finish_later(tenant: Tenant, call_id: str, reason: str = "") -> asyncio.Task:
    """`finish`, in the background: for a caller who has to be answered now,
    such as the dashboard's End button."""
    return _keep(asyncio.create_task(finish(tenant, call_id, reason)))


#: After the agent's closing line, how long the caller gets to say something
#: new before the server ends the call itself.
CLOSING_END_SECONDS = 4.0
CLOSING_END_REASON = "the agent said its closing line and the caller was done"

#: Notes put in front of the model between turns. They are instructions to
#: act on, marked as such: the first version said "Say it is not recorded
#: yet", and the agent said exactly that to the caller, twice, in the middle
#: of a booking.
NOTE_PREFIX = ("[Instruction from the system, not for the caller: do not read this "
               "out or refer to it.] ")
NOT_RECORDED_CORRECTION = (
    NOTE_PREFIX
    + "No request has been recorded on this call: record_request has not "
    "returned a reference. Do not tell the caller anything is recorded, noted "
    "or booked. Carry on naturally: ask for any required detail still missing, "
    "read the details back once, and when the caller says yes call "
    "record_request with confirmed true."
)
RECORD_NOW_NOTE = (
    NOTE_PREFIX
    + "The caller has just confirmed the details you read back. Call "
    "record_request now with confirmed true and the same details. Do not read "
    "them back again, do not ask permission, do not add conditions."
)
MOBILE_LENGTH_NOTE = (
    NOTE_PREFIX
    + "The mobile number the caller just gave has {n} digits; a Pakistani mobile "
    "has 11. Say so and ask for it again; do not read it back as complete."
)
NOTHING_SENT_CORRECTION = (
    NOTE_PREFIX
    + "Nothing has been recorded or sent. Ask for any missing details (mobile "
    "first), read back, then call record_request."
)
CANCELLED_NOTE = (
    NOTE_PREFIX
    + "The caller said not to send the WhatsApp message. It is cancelled: nothing is "
    "queued and nothing has been sent. Confirm briefly that it will not be sent, and "
    "do not queue it again unless they ask again."
)
RECEIVED_NOTE = (
    NOTE_PREFIX
    + "No WhatsApp message has been sent on this call{state}. Say so truthfully: 'Is "
    "call mein koi message abhi bheja nahi gaya; details call ke baad bheji jayengi' "
    "(or that nothing is queued). Never deny a message that was really sent, and "
    "never ask the caller to read a message out."
)
AMOUNT_CORRECTION = (
    NOTE_PREFIX
    + "You just said {wrong}; the correct figure is {right}. Correct yourself briefly "
    "now: 'Maazrat, sahi raqam {right} hai.'"
)
#: How alike a wrong amount and a known one must look, digit for digit, for
#: the known one to be offered as the correction: 2010000 and 210000 do.
AMOUNT_LIKENESS = 0.6
#: How long after a caller's words arrive before the number in them is
#: counted, when the recogniser has not said the turn is finished: the rest
#: of a number said slowly may still be on its way. A finished turn is
#: counted at once.
MOBILE_NOTE_DELAY_SECONDS = 1.5

#: The agent saying a request is on its way to the team: "team ko bhej rahi
#: hoon", "forward kar diya", "team tak pohncha diya". With no reference on
#: the call, nothing is on its way anywhere.
_FORWARD_CLAIMS = turns_mod._phrases(
    "team ko bhej", "team ko forward", "team tak pohncha", "team tak pahuncha",
    "team tak pohancha", "team tak bhej", "team ko de di", "team ko de diya", "team ko pass",
    "team ko bata di", "team ko bata diya", "team ko send", "team ko pohncha", "aage bhej",
    "aage forward", "forward kar rahi", "forward kar raha", "forward kar diya",
    "forward kar di", "forward kar dia", "forwarded", "forwarding", "passed on", "passed it on",
    "sent to the team", "sending to the team", "sent it to the team", "escalate kar",
    "escalated", "escalating", "department ko bhej", "department ko forward",
    "concerned team ko", "team ko bheja", "team ko bheji",
)


def _claims_forwarded(agent_text: str) -> bool:
    """The agent said the request is being, or has been, sent on to the team."""
    for clause in _clauses(agent_text):
        if (turns_mod._has_any(clause, _FORWARD_CLAIMS)
                and not turns_mod._has_any(clause, _NOT_A_CLAIM)):
            return True
    return False

#: Only a claim that something *has been* recorded counts. "Record kar
#: loongi" (I will record) and "record karne ke liye" (in order to record)
#: were taken for claims on a real call, and the corrections that followed
#: derailed the booking. When in doubt, nothing is done.
_RECORDED_CLAIMS = turns_mod._phrases(
    "record kar li", "record kar liya", "record kar di", "record kar diya", "record kar lia",
    "record ho gayi", "record ho gai", "record ho gaya", "record ho chuki", "record ho chuka",
    "darj kar li", "darj kar liya", "darj kar di", "darj ho gayi", "darj ho gai", "darj ho gaya",
    "note kar li", "note kar liya", "note kar lia", "note ho gayi", "note ho gai",
    "book kar li", "book kar liya", "book kar lia", "book ho gayi", "book ho gai",
    "book ho gaya", "likh li hai", "likh liya hai", "register kar li", "register kar liya",
    "register ho gayi", "register ho gai", "submit kar di", "submit kar diya", "submit ho gayi",
    "submit ho gai", "has been recorded", "is recorded", "been recorded", "have recorded",
    "recorded it", "is booked", "has been booked", "have booked", "booked it", "is noted",
    "has been noted", "have noted", "noted it", "is submitted", "has been submitted",
    "have submitted", "submitted it", "recorded", "booked", "noted", "submitted",
    # Complaints, in the past tense: "shikayat darj karwayi thi", "darj hui
    # thi", "complaint register ho gayi".
    "darj karwayi", "darj karwai", "darj karvayi", "darj karvai", "darj karai",
    "darj karaai", "darj karwa di", "darj karwa li", "darj ki thi", "darj ki hai",
    "darj hui thi", "darj hui hai", "darj hui", "darj ho chuki", "darj ho chuka",
    "darj hai", "register ho gayi", "register ho gai", "register hui", "register kar di",
    "register karwa di", "registered", "logged", "lodged", "filed",
)
_RECORDED_CLAIMS_SCRIPT = ("ریکارڈ کر لی", "ریکارڈ کر لیا", "ریکارڈ ہو گئی", "ریکارڈ ہو گیا",
                           "درج کر لی", "درج ہو گئی", "نوٹ کر لی", "بک کر لی", "بک ہو گئی")
#: Words that make a sentence a plan, an offer, a condition or a denial
#: rather than a claim: "record kar loongi", "record kar sakti hoon",
#: "record kar li jayegi", "abhi record nahi hui", "once recorded".
_NOT_A_CLAIM = turns_mod._phrases(
    "sakti", "sakta", "sakte", "sakoon", "sakun", "sakon", "sakegi", "sakega", "sakenge",
    "karungi", "karunga", "karoongi", "karoonga", "karenge", "karun", "karoon", "karne",
    "karna", "karni", "loongi", "lungi", "loonga", "lunga", "lenge", "doongi", "dungi",
    "doonga", "dunga", "denge", "doon", "dun", "loon", "lun", "deti", "deta", "dete", "leti",
    "leta", "lete", "paungi", "paoongi", "paunga", "paoonga", "jaye", "jayegi", "jaayegi",
    "jaegi", "jayega", "jaayega", "jaega", "jayen", "jaen", "jaaye", "nahi", "nahin", "not",
    "yet", "abhi", "will", "can", "could", "shall", "should", "would", "karwana", "karwani",
    "karwane", "karvana", "karvani", "karvane", "karwaun", "karwaoon", "karwa doon",
    "agar", "chahte", "chahti", "chahta", "once", "jab", "baad", "tabhi", "tab", "to be",
    "going to", "about to", "need", "needs", "before", "after", "when", "if", "unless",
    "zaroori", "zaruri",
)
_NOT_A_CLAIM_SCRIPT = ("نہیں", "سکتی", "سکتا", "سکتے", "ابھی", "گی", "گا", "گے",
                       "جائے", "اگر", "جب")
_SENTENCES = re.compile(r"[^.!?؟۔।]+[.!?؟۔।]*")
#: Where one statement ends and another starts inside a sentence: "mujhe
#: maloom nahi ..., lekin main team ko bhej rahi hoon" is a denial and a
#: claim, and the claim is not excused by the denial's "nahi".
_CLAUSE_BREAK = re.compile(r"[,;:]|(?:lekin|magar|but|phir|aur|and|to|toh)", re.I)


def _clauses(agent_text: str):
    """Each statement in the agent's words, questions left out."""
    for sentence in _SENTENCES.findall(agent_text or ""):
        if sentence.rstrip().endswith(("?", "؟")):
            continue
        for clause in _CLAUSE_BREAK.split(sentence):
            if clause and clause.strip():
                yield clause

#: References record_request has handed out on each live call, so a claim
#: of "recorded" can be checked against what actually happened.
_recorded: dict[str, list[str]] = {}
#: Each live call's tool state (`_tool_handler`'s `pending`), so the
#: transcript watch can see a request in progress or a read-back pending.
_pending: dict[str, dict] = {}

_MESSAGING_WORDS = turns_mod._phrases(
    "whatsapp", "message", "msg", "sms", "text", "bhej", "bhejti", "bhejta", "bhejoon",
    "bhejun", "send", "sent", "bheja", "bheji",
)


def _claims_recorded(agent_text: str) -> bool:
    """The agent said a request *has been* recorded, noted, booked or
    submitted.

    Judged sentence by sentence: an offer ("record kar sakti hoon"), a plan
    ("record kar loongi"), a purpose ("record karne ke liye"), a question
    ("record kar loon?") or a denial ("abhi record nahi hui") is not a
    claim, however many of the words it shares with one.
    """
    for clause in _clauses(agent_text):
        if turns_mod._has_any(clause, _RECORDED_CLAIMS):
            if not turns_mod._has_any(clause, _NOT_A_CLAIM):
                return True
        elif turns_mod._has_script(clause, _RECORDED_CLAIMS_SCRIPT):
            if not turns_mod._has_script(clause, _NOT_A_CLAIM_SCRIPT):
                return True
    return False


def _number_read_back(agent_text: str, conversation: list[tuple[str, str]],
                      tenant: Tenant) -> bool:
    """The agent's turn read back a number the caller gave on this call."""
    found = phones.find_number(phones.spoken_digits(agent_text), _country(tenant))
    if found is None:
        return False
    return phones.said_on_call(turns_mod.caller_lines(conversation), found[1])


def _transcript_watch(tenant: Tenant, call: Call):
    """What happens as the words arrive, fragment by fragment.

    Three things a model cannot be trusted to do by itself:

    * end the call: once the agent has said its closing line, the server
      ends the call within `CLOSING_END_SECONDS` unless the caller asks
      something new, whether or not the model ever calls end_call, and
      never with a request half taken unless the caller said goodbye;
    * tell the truth about requests: "request record kar li hai" with no
      record_request behind it gets a correction in front of the model for
      its next turn;
    * record when the caller says yes: a yes to the agent's read-back of
      the details is followed by a note to call record_request now, not a
      second read-back.
    """
    state: dict = {"armed_at": -1, "ending": None, "corrected": -1, "nudged": -1,
                   "counted": set()}

    def watch(who: str, _text: str) -> None:
        session = _sessions.get(call.id)
        if session is None:
            return
        conversation = session.turns()
        if who == "caller:done":
            # The caller's turn is finished: a number in it is counted now,
            # and the model is told before it can read a short one back.
            if not conversation or conversation[-1][0] != "caller":
                return
            turn_index = len(conversation) - 1
            count = phones.wrong_length_mobile(conversation[-1][1], _country(tenant))
            if count is not None and turn_index not in state["counted"]:
                state["counted"].add(turn_index)
                log.info("call %s: the caller gave a mobile number with %d digits; the "
                         "agent is told to ask for it again", call.id, count)
                _keep(asyncio.create_task(session.note_to_agent(
                    MOBILE_LENGTH_NOTE.format(n=count), urgent=True)))
            return
        if not conversation or conversation[-1][0] != who:
            return
        turn_index = len(conversation) - 1
        latest = conversation[-1][1]
        if who == "caller":
            _watch_sending(tenant, call, session, conversation, turn_index, latest, state)
            if (turn_index not in state["counted"]
                    and phones.wrong_length_mobile(latest, _country(tenant)) is not None):
                _keep(asyncio.create_task(
                    _note_mobile_length(tenant, call, session, turn_index, state)))
            if state["nudged"] == turn_index or _recorded.get(call.id):
                return
            if not turns_mod.is_affirmative(latest) or turns_mod.is_new_request(latest):
                return
            agent_before = (conversation[turn_index - 1][1]
                            if turn_index >= 1 and conversation[turn_index - 1][0] == "agent"
                            else "")
            pending = _pending.get(call.id) or {}
            if pending.get("number") or turns_mod._has_any(agent_before, _MESSAGING_WORDS):
                return                           # a message read-back, not a request
            if not _number_read_back(agent_before, conversation, tenant):
                return
            state["nudged"] = turn_index
            log.info("call %s: the caller confirmed the read-back; the agent is told to "
                     "record the request now", call.id)
            _keep(asyncio.create_task(session.note_to_agent(RECORD_NOW_NOTE)))
            return
        if who != "agent":
            return
        _check_amounts(call, session, conversation, turn_index, latest, state)
        if turns_mod.agent_said_closing(latest):
            if state["armed_at"] != turn_index:
                state["armed_at"] = turn_index
                since = len(session.transcript)
                state["ending"] = _keep(asyncio.create_task(
                    _end_after_closing(tenant, call, session, since, state)))
        elif not _recorded.get(call.id) and (_claims_recorded(latest)
                                             or _claims_forwarded(latest)):
            if state["corrected"] != turn_index:
                state["corrected"] = turn_index
                forwarded = not _claims_recorded(latest)
                log.warning("call %s: the agent claimed a request was %s, and none was: %r",
                            call.id, "sent on" if forwarded else "recorded",
                            turns_mod.judged(latest))
                note = NOTHING_SENT_CORRECTION if forwarded else NOT_RECORDED_CORRECTION
                _keep(asyncio.create_task(session.note_to_agent(note)))

    return watch


async def _note_mobile_length(tenant: Tenant, call: Call, session: CallSession,
                              turn_index: int, state: dict) -> None:
    """Tell the model a mobile number was the wrong length, once the caller's
    turn has had time to finish arriving. The fallback for a recogniser that
    never says the turn is finished; only the count is logged."""
    try:
        await asyncio.sleep(MOBILE_NOTE_DELAY_SECONDS)
        if _sessions.get(call.id) is not session or session.closed:
            return
        if turn_index in state["counted"]:
            return                                   # counted when the turn finished
        conversation = session.turns()
        if turn_index >= len(conversation) or conversation[turn_index][0] != "caller":
            return
        count = phones.wrong_length_mobile(conversation[turn_index][1], _country(tenant))
        if count is None:
            return                                   # the rest of it arrived
        state["counted"].add(turn_index)
        log.info("call %s: the caller gave a mobile number with %d digits; the agent "
                 "is told to ask for it again", call.id, count)
        await session.note_to_agent(MOBILE_LENGTH_NOTE.format(n=count), urgent=True)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not pass the number length on", call.id)


def _watch_sending(tenant: Tenant, call: Call, session, conversation: list[tuple[str, str]],
                   turn_index: int, latest: str, state: dict) -> None:
    """The caller's say over a queued message, read from their whole turn.

    A no, said plainly ("nahin bhejein") or as the answer to the agent's
    offer, cancels what is queued, however the turn started: "ji haan.
    balki nahin bheje" is a no. And a caller who says a message has already
    arrived is answered with the truth about this call, not a denial.
    """
    pending = _pending.get(call.id) or {}
    agent_before = next((t for w, t in reversed(conversation[:turn_index]) if w == "agent"), "")
    verdict = turns_mod.last_word_on_sending(latest)
    plain_no = turns_mod.wants_not_sent(latest)
    about_sending = plain_no or turns_mod.offered_messaging(agent_before)
    if (verdict == "no" and about_sending and queued_messages(pending)
            and state.get("cancelled_at") != turn_index):
        state["cancelled_at"] = turn_index
        _cancel_queued(call, pending)
        for message in pending.get("queued") or []:
            if message.get("state") == "cancelled" and not message.get("noted"):
                message["noted"] = True
                _keep(asyncio.create_task(_note_on_call(
                    tenant, call, f"WhatsApp to …{phones.ending(message['to'])}: cancelled by "
                                  "the caller")))
        _keep(asyncio.create_task(session.note_to_agent(CANCELLED_NOTE)))
    if turns_mod.claims_message_received(latest) and state.get("received_at") != turn_index:
        state["received_at"] = turn_index
        waiting = len(queued_messages(pending))
        shape = ("; one message is queued to be sent after the call" if waiting == 1
                 else f"; {waiting} messages are queued to be sent after the call" if waiting
                 else "; nothing is queued")
        log.info("call %s: the caller says a message arrived; the agent is told nothing "
                 "has been sent on this call", call.id)
        _keep(asyncio.create_task(session.note_to_agent(RECEIVED_NOTE.format(state=shape))))


def _allowed_amounts(call_id: str, session, conversation: list[tuple[str, str]],
                     state: dict) -> set[int]:
    """The figures the agent may say: the material's, the calculator's, and
    the caller's own."""
    if "material_amounts" not in state:
        persona = getattr(session, "persona", None)
        state["material_amounts"] = amounts.material_values(
            getattr(persona, "instructions", "") or "")
    allowed: set[int] = set(state["material_amounts"])
    allowed.update(int(round(v)) for v in _calculated.get(call_id, []))
    for who, text in conversation:
        if who == "caller":
            allowed.update(int(round(v)) for v in amounts.values_in(text) if v >= 1_000)
    return allowed


def _check_amounts(call: Call, session, conversation: list[tuple[str, str]], turn_index: int,
                   latest: str, state: dict) -> None:
    """Every amount the agent says is checked against what it was given.

    On a real call it said "20 lakh 10 hazar" for a difference the calculator
    had given as 210000, and nobody knew until the recording was played. One
    not in the material, the calculator's figures or the caller's own words
    is logged (the number only) and, when a known figure looks like what was
    meant, corrected with a silent note before the agent's next turn.
    """
    judged: set = state.setdefault("amounts_judged", set())
    said = amounts.spoken_amounts(latest)
    if not said:
        return
    allowed = _allowed_amounts(call.id, session, conversation, state)
    for phrase, value in said:
        if latest.rstrip().endswith(phrase.strip()):
            continue                             # the rest of it may still be coming
        if value in allowed or (turn_index, phrase) in judged:
            continue
        judged.add((turn_index, phrase))
        right = _closest_amount(value, call.id, state)
        log.warning("call %s: the agent said an amount that is in neither the material nor "
                    "a calculation: %d%s", call.id, value,
                    f" (the nearest known figure is {right})" if right is not None else "")
        if right is None:
            continue
        note = AMOUNT_CORRECTION.format(wrong=f"{amounts.spoken_pkr(value)} rupay",
                                        right=f"{amounts.spoken_pkr(right)} rupay")
        _keep(asyncio.create_task(session.note_to_agent(note)))


def _closest_amount(value: int, call_id: str, state: dict) -> int | None:
    """The known figure that looks most like `value`, digit for digit: the
    calculator's latest results first, then the material's."""
    import difflib

    candidates: list[int] = [int(round(v)) for v in reversed(_calculated.get(call_id, []))
                             if abs(v) >= 1_000]
    candidates += sorted(state.get("material_amounts") or ())
    best, best_score = None, 0.0
    for candidate in candidates:
        if candidate == value:
            continue
        score = difflib.SequenceMatcher(None, str(value), str(candidate)).ratio()
        if score > best_score:
            best, best_score = candidate, score
    return best if best is not None and best_score >= AMOUNT_LIKENESS else None


async def _end_after_closing(tenant: Tenant, call: Call, session: CallSession,
                             since: int, state: dict) -> None:
    """End the call from the server after the closing line, unless the caller
    asks something new in the window, or a request is still in progress."""
    try:
        deadline = time.time() + 12
        while time.time() < deadline and session.speaking:
            await asyncio.sleep(0.2)
        await asyncio.sleep(CLOSING_END_SECONDS)
        if _sessions.get(call.id) is not session or session.closed:
            return                                   # ended already, or replaced
        await _let_transcription_land(session)
        heard = _caller_said_since(session, since)
        if turns_mod.is_new_request(heard):
            log.info("call %s: the caller asked something after the closing line (%r); "
                     "the call goes on", call.id, turns_mod.judged(heard))
            state["armed_at"] = -1
            _release_hold(session)
            return
        conversation = session.turns()
        _index, last_words = turns_mod.last_meaningful_caller_turn(conversation)
        pending = _pending.get(call.id) or {}
        if (not turns_mod.is_explicit_end(last_words)
                and _request_in_progress(call.id, conversation, pending, tenant)):
            log.info("call %s: not ending the call from the server: a request is in "
                     "progress and the caller has not said goodbye (their last words "
                     "were %r)", call.id, turns_mod.judged(last_words))
            state["armed_at"] = -1
            _release_hold(session)
            return
        log.info("call %s: ending the call from the server: the agent said its closing "
                 "line and the caller said %r", call.id,
                 turns_mod.judged(heard) or "nothing")
        await hang_up(tenant, call, CLOSING_END_REASON)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not end the call after the closing line", call.id)


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
        chosen, persona = await _prepared(tenant, call)
        session = CallSession(
            call.id,
            send_to_caller=_discard,
            live_settings=chosen,
            persona=persona,
            record=tenant.record_calls,
            keepalive=keepalive,
            on_transcript=_transcript_watch(tenant, call),
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
        connecting = time.monotonic()
        await session.start(paced=False)
        if not session.closed:
            log.info("call %s: the agent is connected (%.1fs) and waiting for the line",
                     call.id, time.monotonic() - connecting)
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
    if human:
        # A human call never starts the model, so it needs no persona.
        chosen = await live_settings_now(tenant)
        persona = AgentPersona(instructions="", greeting="")
    else:
        chosen, persona = await _prepared(tenant, call)
    session = CallSession(
        call.id,
        send_to_caller=send_to_caller,
        live_settings=chosen,
        persona=persona,
        record=tenant.record_calls,
        keepalive=keepalive,
        on_transcript=None if human else _transcript_watch(tenant, call),
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
    _recorded.pop(call_id, None)
    pending = _pending.pop(call_id, None) or {}
    _calculated.pop(call_id, None)
    session = _sessions.pop(call_id, None)
    if session is None:
        if queued_messages(pending):
            # A queue with no session to settle: still owed to the caller.
            call = await call_repo.get_call(tenant.phone_number_id, call_id)
            if call is not None:
                await flush_queued_messages(tenant, call, pending)
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

        # Ended, now. Storing the recording takes ten seconds and more
        # (transcode, upload), and until this save the dashboard showed the
        # call as still running for all of it.
        if audio:
            if call.recording_state in (RecordingState.NONE, RecordingState.PENDING):
                call.recording_state = RecordingState.PENDING
        elif call.recording_state is RecordingState.PENDING:
            call.recording_state = RecordingState.ABSENT
        await call_repo.save_call(call)

        # What the call promised in writing goes now: before the recording,
        # which can take ten seconds and more, and only once.
        if queued_messages(pending):
            await flush_queued_messages(tenant, call, pending)

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

        reports.schedule_sync(tenant)
        timing = getattr(session, "timing_summary", None)
        measured = timing() if callable(timing) else ""
        log.info(
            "call %s settled: %ds, %s, recording %s%s",
            call_id, call.duration_seconds, call.status.value,
            call.recording_state.value.lower(),
            f"; {measured}" if measured else "",
        )
        if script.needs_respelling(transcript):
            task = asyncio.create_task(_respell_in_roman_urdu(tenant, call_id, transcript))
            _respelling.add(task)
            task.add_done_callback(_respelling.discard)
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not settle the row", call_id)


#: Transcripts being respelled after their call, held so none is collected
#: half-way through.
_respelling: set[asyncio.Task] = set()


async def _respell_in_roman_urdu(tenant: Tenant, call_id: str, transcript: str) -> None:
    """Rewrite a finished call's Hindi-script and Urdu-script lines as Roman
    Urdu with the text model, which spells loanwords and Urdu words the way
    people write them.

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
        rewritten = "\n".join(p + t for p, t in zip(speakers, respelled))
        if rewritten == call.transcript:
            return                              # the model could not help
        call.transcript = rewritten
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
