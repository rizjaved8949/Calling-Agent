"""
Inbound webhooks.

These are the only unauthenticated routes in the service, and they are the ones
that matter most to get right: the endpoint is public, it triggers work on the
customer's account, and the only thing distinguishing a real delivery from an
attacker is the signature.

Routing is the other half. One deployment serves every customer, so the first
job of a Meta webhook is working out *which* company it belongs to — the
`phone_number_id` on the payload — and the second is verifying it against *that
company's* app secret. Verifying against the platform's would let any customer
forge another's traffic.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request, Response, status

from ...config import settings
from ...models.call import (
    Call,
    CallStatus,
    Channel,
    Direction,
    Message,
    MessageDirection,
    RecordingState,
)
from ...models.tenant import Tenant
from ...repositories import calls as call_repo
from ...repositories import knowledge
from ...repositories import numbers as number_repo
from ...repositories import tenants as tenant_repo
from ...security import playback
from ...services import reports
from ...services.agent import live, reply
from ...services.telephony import Infobip
from ...services.whatsapp import WhatsApp, mask, signature_ok

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Meta / WhatsApp
# ---------------------------------------------------------------------------


@router.get("/whatsapp")
async def verify_whatsapp(request: Request) -> Response:
    """Meta's one-time subscription challenge.

    Meta sends this once, when the callback URL is saved, and compares the
    echoed challenge. The token is ours, not Meta's: every tenant may have its
    own, so any configured one is accepted. That is weaker than checking the
    specific tenant's, but Meta does not tell us which number it is asking
    about — there is no phone_number_id on this request.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")

    if mode != "subscribe" or not token:
        return Response("Bad request", status_code=status.HTTP_400_BAD_REQUEST)

    accepted = {settings.whatsapp_webhook_verify_token.strip()}
    for tenant in await tenant_repo.list_all():
        if tenant.verify_token:
            accepted.add(tenant.verify_token.strip())
    for number in await number_repo.list_all():
        if number.verify_token:
            accepted.add(number.verify_token.strip())
    accepted.discard("")

    if token in accepted:
        log.info("WhatsApp webhook verified")
        return Response(challenge, media_type="text/plain")

    log.warning("WhatsApp webhook verification presented an unknown token")
    return Response("Forbidden", status_code=status.HTTP_403_FORBIDDEN)


def _phone_number_id(payload: dict[str, Any]) -> str:
    """Which number this delivery is about.

    Meta buries it identically on every field — messages, statuses and calls —
    at entry[].changes[].value.metadata.phone_number_id.
    """
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            found = metadata.get("phone_number_id")
            if found:
                return str(found)
    return ""


@router.post("/whatsapp")
async def whatsapp_events(request: Request) -> dict:
    """Messages, delivery statuses and call signalling from Meta.

    Always answers 200 once the signature is verified. Meta retries a non-2xx
    with growing backoff and eventually disables the subscription, so an
    exception while handling one message must not take the webhook down for
    every other customer — it is logged and swallowed.
    """
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        log.warning("WhatsApp webhook body was not JSON")
        return {"status": "ignored"}

    phone_number_id = _phone_number_id(payload)
    tenant = await _tenant_for_meta(phone_number_id)

    if not signature_ok(tenant, raw, request.headers.get("x-hub-signature-256")):
        # 403, not 401: there is no credential to supply, and Meta treats both
        # as a failure anyway. The body is not parsed further.
        log.warning("rejected an unverified WhatsApp webhook for %s", phone_number_id or "?")
        return Response(  # type: ignore[return-value]
            content='{"error":{"message":"Signature check failed","code":"bad_signature"}}',
            status_code=status.HTTP_403_FORBIDDEN,
            media_type="application/json",
        )

    if tenant is None:
        # Verified against the legacy secret but no tenant owns the number.
        log.warning("no company is registered on phone number id %s", phone_number_id)
        return {"status": "unknown-company"}

    try:
        await _handle_whatsapp(tenant, payload)
    except Exception:  # noqa: BLE001 — one bad event must not disable the webhook
        log.exception("failed while handling a WhatsApp event for %s", phone_number_id)
    return {"status": "received"}


async def _tenant_for_meta(phone_number_id: str) -> Tenant | None:
    """The company, speaking as the number this delivery is about.

    A number connected on the Numbers page wins; a company whose row is keyed
    on the Meta id itself (the older one-number setup) is the fallback.
    """
    if not phone_number_id:
        return None
    line = await number_repo.by_meta_phone_number_id(phone_number_id)
    if line is not None:
        company = await tenant_repo.get(line.tenant_id)
        if company is not None:
            return number_repo.as_tenant(company, line)
    return await tenant_repo.get(phone_number_id)


async def _inbound_allowed(tenant: Tenant) -> bool:
    """Whether this number answers calls at all.

    An outbound-only number, or one nobody has given an inbound agent, is not
    answered by a stranger's agent — the call is declined and logged instead.
    """
    if not tenant.line_id:
        return True
    line = await number_repo.get(tenant.phone_number_id, tenant.line_id)
    return bool(line and line.verified and line.takes_inbound and line.inbound_agent_id)


async def _handle_whatsapp(tenant: Tenant, payload: dict[str, Any]) -> None:
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            field = change.get("field")
            value = change.get("value") or {}
            if field == "messages":
                await _on_messages(tenant, value)
            elif field == "calls":
                await _on_calls(tenant, value)
            else:
                log.debug("ignoring WhatsApp field %r", field)


async def _on_messages(tenant: Tenant, value: dict[str, Any]) -> None:
    # Inbound messages.
    for raw in value.get("messages") or []:
        kind = raw.get("type", "text")
        body = ""
        if kind == "text":
            body = (raw.get("text") or {}).get("body", "")
        elif kind == "button":
            body = (raw.get("button") or {}).get("text", "")
        elif kind == "interactive":
            interactive = raw.get("interactive") or {}
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            body = reply.get("title", "")

        message = Message(
            tenantId=tenant.phone_number_id,
            providerMessageId=str(raw.get("id") or ""),
            direction=MessageDirection.INBOUND,
            counterparty=f"+{raw.get('from')}" if raw.get("from") else "",
            kind=kind,
            body=body,
            status="received",
            createdAt=float(raw.get("timestamp") or time.time()),
            lineId=tenant.line_id,
        )
        await call_repo.save_message(message)
        log.info(
            "inbound WhatsApp %s from %s for %s",
            kind, mask(message.counterparty), tenant.phone_number_id,
        )

        # Answered in the background on purpose. Meta expects a 200 within
        # seconds and retries anything slower, so composing a reply inline
        # means the same question answered two or three times while the model
        # is still thinking.
        if tenant.auto_reply and body and kind in {"text", "button", "interactive"}:
            asyncio.create_task(_auto_reply(tenant, message))

    # Delivery receipts for messages we sent.
    for raw in value.get("statuses") or []:
        provider_id = str(raw.get("id") or "")
        existing = await call_repo.find_message_by_provider_id(
            tenant.phone_number_id, provider_id
        )
        if existing is None:
            continue
        existing.status = str(raw.get("status") or existing.status)
        errors = raw.get("errors") or []
        if errors:
            # Meta's own sentence; far more use than "failed".
            existing.error = str(errors[0].get("title") or errors[0].get("message") or "")[:300]
        await call_repo.save_message(existing)


async def _auto_reply(tenant: Tenant, incoming: Message) -> None:
    """Compose an answer and send it. Never raises — this is a loose task.

    An exception here would be an unretrieved exception on a background task
    and nothing else: the webhook has already answered Meta, and the person is
    simply left without a reply. So every failure is logged and swallowed.
    """
    try:
        # A WhatsApp number can have its own agent and its own knowledge, same
        # as a phone line — a message arriving on the support number should be
        # answered from the support handbook, not from everything the company
        # has ever uploaded. Same chain a call walks; see services/routing.py.
        from ...models.agent import Direction as SetupDirection
        from ...services import routing

        resolved = await routing.resolve(
            tenant, channel=Channel.WHATSAPP_MESSAGE, direction=SetupDirection.INBOUND
        )
        context = await routing.context_for(tenant, resolved)
        history = await _recent_exchange(tenant.phone_number_id, incoming.counterparty)
        answer = await reply.compose(tenant, incoming.body, context, history)
        if not answer:
            log.info("tenant %s: nothing to say to %s",
                     tenant.phone_number_id, mask(incoming.counterparty))
            return
        # A free-text reply is legal because their message just opened the
        # 24-hour window; outside it this would be accepted and never
        # delivered, which is why nothing else here sends plain text.
        sent = await WhatsApp(tenant).send_text(incoming.counterparty, answer)
        log.info("tenant %s: replied to %s (%d chars, id %s)",
                 tenant.phone_number_id, mask(incoming.counterparty),
                 len(answer), sent.provider_message_id[:24])
    except Exception:  # noqa: BLE001
        log.exception("tenant %s: auto-reply failed", tenant.phone_number_id)


async def _recent_exchange(tenant_id: str, counterparty: str) -> list[tuple[str, str]]:
    """The last few turns with this person, oldest first.

    Without it every message is answered as a first contact: ask "and the
    evening one?" after a question about opening hours and the agent has no
    idea what "one" refers to.
    """
    rows = await call_repo.list_messages(tenant_id, counterparty=counterparty, limit=10)
    turns = [
        ("them" if m.direction is MessageDirection.INBOUND else "us", m.body)
        for m in reversed(rows)
        if m.body
    ]
    return turns[:-1] if turns else []   # the newest is the question itself


async def _on_calls(tenant: Tenant, value: dict[str, Any]) -> None:
    """WhatsApp Business Calling signalling.

    This build keeps the *record* of the call — who rang, when, how it ended —
    without terminating the media. An inbound call therefore logs as RINGING
    and settles when the terminate event arrives; it is not answered, because
    answering means having somewhere to send the audio.
    """
    for raw in value.get("calls") or []:
        provider_call_id = str(raw.get("id") or "")
        event = str(raw.get("event") or raw.get("status") or "").lower()
        counterparty = raw.get("from") or ""
        existing = await call_repo.find_by_provider_id(
            tenant.phone_number_id, provider_call_id
        )

        if existing is None:
            existing = Call(
                tenantId=tenant.phone_number_id,
                channel=Channel.WHATSAPP_CALL,
                direction=(
                    Direction.INBOUND
                    if str(raw.get("direction", "")).upper() != "BUSINESS_INITIATED"
                    else Direction.OUTBOUND
                ),
                status=CallStatus.RINGING,
                counterparty=f"+{counterparty}" if counterparty else "",
                providerCallId=provider_call_id,
                lineId=tenant.line_id,
                startedAt=float(raw.get("timestamp") or time.time()),
                recordingState=(
                    RecordingState.PENDING if tenant.record_calls else RecordingState.NONE
                ),
            )
            log.info(
                "inbound WhatsApp call %s from %s for %s",
                provider_call_id, mask(existing.counterparty), tenant.phone_number_id,
            )

        # The offer is the call. Meta sends an SDP offer with the `connect`
        # event and waits for an answer; everything else here is bookkeeping.
        offer = ""
        session_info = raw.get("session") or {}
        if isinstance(session_info, dict) and session_info.get("sdp_type") == "offer":
            offer = str(session_info.get("sdp") or "")

        # On an outbound call this is the other half of our own offer: the
        # person picked up and Meta is handing back their answer.
        answer = ""
        if isinstance(session_info, dict) and session_info.get("sdp_type") == "answer":
            answer = str(session_info.get("sdp") or "")

        if event in {"connect", "accepted", "answered"}:
            existing.status = CallStatus.IN_PROGRESS
            existing.answered_at = existing.answered_at or time.time()
            if offer and existing.direction is Direction.INBOUND:
                await call_repo.save_call(existing)
                # Answered in the background: terminating WebRTC takes seconds
                # and Meta retries a webhook that does not return promptly.
                asyncio.create_task(_answer_whatsapp(tenant, existing, offer))
                continue
            if answer and existing.direction is Direction.OUTBOUND:
                await call_repo.save_call(existing)
                asyncio.create_task(_connect_outbound(tenant, existing, answer))
                continue
        elif event in {"terminate", "ended", "completed"}:
            existing.status = CallStatus.COMPLETED
            existing.ended_at = time.time()
            if existing.answered_at:
                existing.duration_seconds = max(
                    0, round(existing.ended_at - existing.answered_at)
                )
            else:
                existing.status = CallStatus.NO_ANSWER
            # The agent was on this call: settle it properly, which stores the
            # recording and the transcript, then close the WebRTC leg.
            from ...services.agent import whatsapp_media

            if live.get(existing.id) is not None:
                asyncio.create_task(live.finish(tenant, existing.id, "the caller hung up"))
            elif existing.recording_state == RecordingState.PENDING:
                existing.recording_state = RecordingState.ABSENT
            asyncio.create_task(whatsapp_media.drop(existing.id))
        elif event in {"reject", "rejected", "failed"}:
            existing.status = CallStatus.FAILED
            existing.ended_at = time.time()
            existing.recording_state = RecordingState.NONE

        await call_repo.save_call(existing)
        if existing.status in {CallStatus.COMPLETED, CallStatus.NO_ANSWER}:
            reports.schedule_sync(tenant)


# ---------------------------------------------------------------------------
# Infobip
# ---------------------------------------------------------------------------


@router.post("/infobip/line/{number_id}")
async def infobip_line_events(number_id: str, request: Request) -> dict:
    """Call events for one connected SIM number.

    The URL shown on the Numbers page. It names the number, so the right
    company and the right carrier credentials are known before the payload is
    read. The id is a random 128-bit value and is the only secret Infobip
    offers us, the same arrangement as the per-company URL below.
    """
    line = await number_repo.get_any(number_id)
    company = await tenant_repo.get(line.tenant_id) if line else None
    if line is None or company is None:
        log.warning("Infobip event for unknown number %s", number_id)
        return {"status": "unknown-number"}
    try:
        payload = await request.json()
    except ValueError:
        return {"status": "ignored"}
    try:
        await _handle_infobip(number_repo.as_tenant(company, line), payload)
    except Exception:  # noqa: BLE001
        log.exception("failed while handling an Infobip event for number %s", number_id)
    return {"status": "received"}


@router.post("/infobip/{phone_number_id}")
async def infobip_events(phone_number_id: str, request: Request) -> dict:
    """Call progress from the carrier.

    Infobip has no per-message signature, so the tenant is named in the path
    and the URL itself is the secret — it is configured once in their console
    and never appears in a browser. That is weaker than Meta's HMAC, and the
    reason nothing here acts on the payload beyond updating a row it already
    has: an event for an unknown call id is dropped rather than creating one.
    """
    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        log.warning("Infobip event for unknown company %s", phone_number_id)
        return {"status": "unknown-company"}

    try:
        payload = await request.json()
    except ValueError:
        return {"status": "ignored"}

    try:
        await _handle_infobip(tenant, payload)
    except Exception:  # noqa: BLE001
        log.exception("failed while handling an Infobip event for %s", phone_number_id)
    return {"status": "received"}


async def _answer_whatsapp(tenant: Tenant, call: Call, sdp_offer: str) -> None:
    """Answer an inbound WhatsApp call and put the agent on it.

    Four things in order, and the order matters: terminate the WebRTC leg
    locally, tell Meta we accept with our answer, wait for media to actually
    connect, and only then start the agent. Starting it earlier means its
    greeting plays into a connection that does not exist yet, and the caller
    hears the middle of a sentence.

    Never raises. The webhook has already returned, and a failure here settles
    the call rather than leaving a row that says a call is still in progress.
    """
    from ...services.agent import whatsapp_media

    if not whatsapp_media.available():
        log.error("call %s: aiortc is not installed, cannot answer", call.id)
        await _fail_call(tenant, call, "the media stack is not installed")
        return

    if not await _inbound_allowed(tenant):
        log.info("call %s: this number takes no inbound calls, declining", call.id)
        with contextlib.suppress(Exception):
            await WhatsApp(tenant).reject_call(call.provider_call_id)
        await _fail_call(tenant, call, "this number is not set up to answer calls")
        return

    bridge = None
    try:
        bridge = whatsapp_media.WhatsAppBridge(call.id, tenant)
        whatsapp_media.register(call.id, bridge)

        answer_sdp = await bridge.answer(sdp_offer)
        await WhatsApp(tenant).answer_call(call.provider_call_id, answer_sdp)
        log.info("call %s: accepted with Meta, waiting for media", call.id)

        if not await bridge.wait_connected(timeout=30):
            raise RuntimeError(
                "Meta accepted the call but WebRTC media never connected "
                "(check UDP/NAT, or set WHATSAPP_TURN_URL)"
            )

        session = await live.start(tenant, call, bridge.send_to_caller, keepalive=False)
        bridge.attach(session)
        log.info("call %s: the agent is on the line", call.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("call %s: could not answer", call.id)
        if bridge is not None:
            await whatsapp_media.drop(call.id)
        with contextlib.suppress(Exception):
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
        await _fail_call(tenant, call, str(exc)[:200])


async def _connect_outbound(tenant: Tenant, call: Call, sdp_answer: str) -> None:
    """The person answered a call we placed. Finish the handshake and talk.

    The bridge already exists — it made the offer before Meta was asked — so
    this is only the second half: take their answer, wait for media, and put
    the agent on.
    """
    from ...services.agent import whatsapp_media

    bridge = whatsapp_media.get(call.id)
    if bridge is None:
        # The offer was made by a process that has since restarted, so the
        # WebRTC state it belongs to is gone and cannot be recovered.
        log.error("call %s: answered, but the bridge that offered is gone", call.id)
        with contextlib.suppress(Exception):
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
        await _fail_call(tenant, call, "the call was answered after a restart")
        return

    try:
        await bridge.accept_answer(sdp_answer)
        if not await bridge.wait_connected(timeout=30):
            raise RuntimeError("the call was answered but media never connected")
        session = await live.start(tenant, call, bridge.send_to_caller, keepalive=False)
        bridge.attach(session)
        log.info("call %s: outbound connected, the agent is on the line", call.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("call %s: outbound call failed after answer", call.id)
        await whatsapp_media.drop(call.id)
        with contextlib.suppress(Exception):
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
        await _fail_call(tenant, call, str(exc)[:200])


async def _fail_call(tenant: Tenant, call: Call, reason: str) -> None:
    call.status = CallStatus.FAILED
    call.ended_at = time.time()
    call.error = reason
    call.recording_state = RecordingState.NONE
    with contextlib.suppress(Exception):
        await call_repo.save_call(call)


def name_of(event: dict[str, Any]) -> str:
    return str(event.get("name") or event.get("type") or "").upper()


async def _attach_media(tenant: Tenant, call: Call) -> None:
    """Put the agent on a SIM call that has just connected.

    Detached from the webhook for the usual reason — the carrier wants a quick
    200 and retries anything slower — and silent on failure beyond the log,
    because there is nowhere to report to and the call row already says what
    happened.
    """
    base = settings.public_base_url.strip().rstrip("/")
    if not base:
        log.error("call %s: PUBLIC_BASE_URL is not set, so no media socket exists", call.id)
        return
    url = (
        base.replace("https://", "wss://").replace("http://", "ws://")
        + f"/api/media/infobip/{call.id}?token="
        + playback.mint(tenant.phone_number_id, call.id, ttl=4 * 3600)
    )
    try:
        await Infobip(tenant).start_media_stream(call.provider_call_id, url)
        log.info("call %s: carrier media stream attached", call.id)
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not attach the media stream", call.id)


async def _answer_inbound(tenant: Tenant, call: Call) -> None:
    """Answer a SIM call the carrier is offering."""
    if not await _inbound_allowed(tenant):
        log.info("call %s: this number takes no inbound calls, hanging up", call.id)
        with contextlib.suppress(Exception):
            await Infobip(tenant).hangup(call.provider_call_id)
        await _fail_call(tenant, call, "this number is not set up to answer calls")
        return
    try:
        await Infobip(tenant).answer(call.provider_call_id)
        log.info("call %s: answered on the carrier", call.id)
    except Exception:  # noqa: BLE001
        log.exception("call %s: could not answer", call.id)


async def _handle_infobip(tenant: Tenant, payload: dict[str, Any]) -> None:
    results = payload.get("results") if isinstance(payload, dict) else None
    events = results if isinstance(results, list) else [payload]

    for event in events:
        if not isinstance(event, dict):
            continue
        properties = event.get("properties") or {}
        call_payload = properties.get("call") or event.get("call") or {}
        provider_call_id = str(call_payload.get("id") or event.get("callId") or "")
        if not provider_call_id:
            continue

        call = await call_repo.find_by_provider_id(tenant.phone_number_id, provider_call_id)
        if call is None:
            # An inbound call is the one event that legitimately arrives for a
            # call we have never seen: the caller dialled, so nothing on our
            # side created a row first.
            if name_of(event) not in {"CALL_RECEIVED", "CALL_RINGING"}:
                log.debug("Infobip event for a call we do not have: %s", provider_call_id)
                continue
            caller = str(call_payload.get("from") or event.get("from") or "")
            call = Call(
                tenantId=tenant.phone_number_id,
                channel=Channel.PHONE,
                direction=Direction.INBOUND,
                status=CallStatus.RINGING,
                counterparty=caller,
                providerCallId=provider_call_id,
                lineId=tenant.line_id,
                fromNumber=tenant.infobip_phone_number,
                recordingState=(
                    RecordingState.PENDING if tenant.record_calls else RecordingState.NONE
                ),
            )
            await call_repo.save_call(call)
            log.info("inbound SIM call %s from %s", call.id, mask(caller))
            asyncio.create_task(_answer_inbound(tenant, call))

        name = str(event.get("name") or event.get("type") or "").upper()
        if name in {"CALL_ESTABLISHED", "CALL_ANSWERED"}:
            # Established means audio can flow. Attaching the stream here
            # rather than on RECEIVED is deliberate: a stream started before
            # the line is up is refused, and the retry lands after the caller
            # has already heard silence and gone.
            asyncio.create_task(_attach_media(tenant, call))
            call.status = CallStatus.IN_PROGRESS
            call.answered_at = call.answered_at or time.time()
            if call_payload.get("dialogId"):
                call.provider_dialog_id = str(call_payload["dialogId"])
        elif name in {"CALL_FINISHED", "CALL_FAILED", "CALL_HANGUP"}:
            call.ended_at = time.time()
            duration = call_payload.get("duration")
            if isinstance(duration, (int, float)) and duration > 0:
                call.duration_seconds = round(duration)
            elif call.answered_at:
                call.duration_seconds = max(0, round(call.ended_at - call.answered_at))
            call.status = (
                CallStatus.COMPLETED
                if call.answered_at
                else (CallStatus.FAILED if name == "CALL_FAILED" else CallStatus.NO_ANSWER)
            )
            reports.schedule_sync(tenant)

        await call_repo.save_call(call)
