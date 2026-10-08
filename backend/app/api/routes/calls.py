"""
The call log: list, read, start, end, delete.

Every route is scoped to the company the key belongs to. There is no path or
query parameter that selects a tenant, which is what makes cross-tenant access
impossible rather than merely unlikely.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query, Response, status

from ...errors import AppError, NotFound
from ...models.call import (
    Call,
    CallStatus,
    Channel,
    Direction,
    OutboundCallRequest,
    RecordingState,
)
from ...repositories import calls as call_repo
from ...services import recordings as recording_service
from ...services import reports
from ...services.telephony import Infobip
from ...services.whatsapp import WhatsApp, mask, to_e164
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("")
async def list_calls(
    tenant: CurrentTenant,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status_filter: CallStatus | None = Query(None, alias="status"),
    channel: Channel | None = None,
) -> dict:
    calls = await call_repo.list_calls(
        tenant.phone_number_id,
        limit=limit,
        offset=offset,
        status=status_filter,
        channel=channel.value if channel else None,
    )
    return {"calls": [c.public() for c in calls], "limit": limit, "offset": offset}


@router.get("/stats")
async def stats(tenant: CurrentTenant) -> dict:
    return await call_repo.call_stats(tenant.phone_number_id)


@router.get("/{call_id}")
async def get_call(tenant: CurrentTenant, call_id: str) -> dict:
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    return call.public()


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_call(tenant: CurrentTenant, payload: OutboundCallRequest) -> dict:
    """Place an outbound call and record the attempt.

    The row is written *before* the provider is asked, so a call that the
    carrier accepts and then fails to report still exists to be reconciled. A
    row created only on success is a call that silently never happened.
    """
    destination = to_e164(payload.to, tenant)
    call = Call(
        tenantId=tenant.phone_number_id,
        channel=payload.channel,
        direction=Direction.OUTBOUND,
        status=CallStatus.QUEUED,
        counterparty=destination,
        fromNumber=tenant.infobip_phone_number or tenant.display_phone_number,
        recordingState=(
            RecordingState.PENDING if tenant.record_calls else RecordingState.NONE
        ),
        metadata=payload.metadata,
    )
    await call_repo.save_call(call)

    try:
        if payload.channel == Channel.PHONE:
            result = await Infobip(tenant).place_call(destination)
            call.provider_call_id = str(result.get("id") or result.get("callId") or "")
            call.provider_dialog_id = str(result.get("dialogId") or "")
        elif payload.channel == Channel.WHATSAPP_CALL:
            # Placing a WhatsApp call needs an SDP offer from the media layer,
            # which is not part of this build. Refused explicitly rather than
            # left to fail somewhere less obvious.
            raise AppError(
                501,
                "Outbound WhatsApp calling needs the media bridge, which is not "
                "enabled on this deployment.",
                code="not_implemented",
            )
        else:
            raise AppError(
                400,
                f"{payload.channel.value} is not a channel you can place a call on.",
                code="bad_channel",
            )
    except AppError:
        call.status = CallStatus.FAILED
        call.ended_at = time.time()
        call.recording_state = RecordingState.NONE
        await call_repo.save_call(call)
        raise

    call.status = CallStatus.RINGING
    await call_repo.save_call(call)
    log.info("placed %s call %s to %s", payload.channel.value, call.id, mask(destination))
    return call.public()


@router.post("/{call_id}/end")
async def end_call(tenant: CurrentTenant, call_id: str) -> dict:
    """Hang up, and settle the row.

    Settling locally even when the provider refuses is deliberate: a call the
    carrier has already dropped returns an error for the hangup, and leaving
    the row IN_PROGRESS forever because of it would keep it in the live list.
    """
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")

    if call.provider_call_id and call.channel == Channel.PHONE:
        try:
            await Infobip(tenant).hangup(call.provider_call_id)
        except AppError as exc:
            log.info("hangup for %s was refused: %s", call_id, exc)
    elif call.provider_call_id and call.channel == Channel.WHATSAPP_CALL:
        try:
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
        except AppError as exc:
            log.info("WhatsApp terminate for %s was refused: %s", call_id, exc)

    now = time.time()
    if call.status not in {CallStatus.COMPLETED, CallStatus.FAILED}:
        call.status = CallStatus.COMPLETED
    call.ended_at = call.ended_at or now
    if call.answered_at and not call.duration_seconds:
        call.duration_seconds = max(0, round(call.ended_at - call.answered_at))
    await call_repo.save_call(call)
    reports.schedule_sync(tenant)
    return call.public()


@router.delete(
    "/{call_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    # A 204 carries no body, so FastAPI must not infer one from the
    # `-> None` annotation.
    response_model=None,
)
async def delete_call(tenant: CurrentTenant, call_id: str) -> None:
    """Delete the call and its audio.

    Unlike removing a company, this is explicitly a deletion of content, so the
    recording goes with it — a call record whose audio outlives it is a privacy
    problem dressed as a tidiness one.
    """
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    if call.recording_path:
        await recording_service.forget(tenant, call)
    await call_repo.delete_call(tenant.phone_number_id, call_id)
    reports.schedule_sync(tenant)


@router.get("/{call_id}/transcript")
async def get_transcript(tenant: CurrentTenant, call_id: str) -> dict:
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    return {
        "callId": call.id,
        "transcript": call.transcript or None,
        "summary": call.summary or None,
        "topic": call.topic or None,
        "language": call.language or None,
    }
