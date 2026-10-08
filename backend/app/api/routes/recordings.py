"""
Call audio: store it, play it, fetch it back from the carrier.

Audio is the default response of `GET /calls/{id}/recording` because that URL
is what a dashboard puts in an `<audio src>`; returning JSON there gives a
player that silently never plays. `?link=true` asks for a signed URL instead,
and `?download=true` sets a filename.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from ...config import settings
from ...errors import AppError, Conflict, NotFound
from ...http_headers import content_disposition
from ...models.call import Channel, RecordingState
from ...repositories import calls as call_repo
from ...services import recordings as recording_service
from ...services import reports, storage
from ...services.audio import extension_for, is_audio
from ...services.telephony import Infobip
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/calls/{call_id}/recording", tags=["recordings"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_recording(
    tenant: CurrentTenant, call_id: str, request: Request
) -> dict:
    """Accept a recording the browser made itself.

    A WebRTC leg taken in a browser is not recorded by any carrier — nothing on
    the server ever sees that audio. The browser does hold both halves, though:
    the microphone it is sending and the stream it is receiving. So it records
    the call and posts the result here.

    Deliberately narrow. Only the company that owns the call, only while the
    call is recent, and only something that claims to be audio.
    """
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")

    age = time.time() - (call.started_at or 0)
    if age > settings.recording_upload_window_seconds:
        raise Conflict("Too late to attach a recording to this call.")

    mime = (request.headers.get("content-type") or "audio/webm").split(";")[0].strip()
    if not is_audio(mime):
        raise AppError(415, "That is not audio.", code="unsupported_media")

    audio = await request.body()
    if not audio:
        raise AppError(400, "The recording was empty.", code="empty_body")
    if len(audio) > settings.max_upload_bytes:
        raise AppError(413, "That recording is too large.", code="too_large")

    call = await recording_service.store_audio(tenant, call, audio, mime)
    if call.recording_state != RecordingState.READY:
        raise AppError(
            502,
            call.recording_error or "The recording could not be stored.",
            code="storage_failed",
        )
    reports.schedule_sync(tenant)
    log.info(
        "browser uploaded its own recording for %s (%.0f KB)", call_id, len(audio) / 1024
    )
    return {
        "status": "stored",
        "bytes": call.recording_bytes,
        "storedIn": storage.describe(call.recording_path),
    }


@router.post("/problem", status_code=status.HTTP_202_ACCEPTED)
async def report_problem(tenant: CurrentTenant, call_id: str, request: Request) -> dict:
    """Let the browser say why it could not record, so the reason is visible.

    A failure that only reaches the operator's developer console is a failure
    nobody sees. The browser is the only place that knows whether the
    microphone was busy, blocked or absent, and each of those has a different
    fix — so it says so here, next to the call it belongs to.

    Success is reported too, and that is the point: three outcomes have to be
    distinguishable. A start line then no upload is a failure at the end of the
    call; a failure line names its own cause; and *neither* line means the
    browser is not running this code at all — a stale bundle or a deploy that
    never landed, which no amount of provider settings fixes.
    """
    body = await request.body()
    payload = {}
    if body:
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
    reason = str(payload.get("reason") or "unspecified")[:300]

    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")

    if payload.get("ok"):
        log.info("browser started recording call %s (%s)", call_id, reason)
        return {"status": "noted"}

    log.warning("browser could not record call %s: %s", call_id, reason)
    call.recording_state = RecordingState.FAILED
    call.recording_error = reason
    await call_repo.save_call(call)
    return {"status": "noted"}


@router.get("")
async def get_recording(
    tenant: CurrentTenant,
    call_id: str,
    download: bool = False,
    link: bool = False,
) -> Response:
    """The call audio.

    `?link=true` returns a signed URL the browser fetches directly from
    storage: playback starts on the first chunk and seeking works through range
    requests, instead of every byte crossing the network twice. Drive-stored
    audio has no such link — a Drive URL needs the viewer signed in to the
    company's own Google account — so it streams through here.
    """
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    if not call.recording_path:
        raise NotFound("Recording")

    if link:
        signed = await storage.signed_url(call.recording_path)
        if signed:
            return RedirectResponse(signed, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        # No signed link available: fall through and stream it.

    audio = await storage.get(tenant, call.recording_path)
    if not audio:
        raise NotFound("Recording")

    mime = call.recording_mime or "audio/mpeg"
    headers = {
        # Seeking in an <audio> element depends on the server advertising this.
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
    }
    if download:
        name = f"call-{call_id}.{extension_for(mime)}"
        headers["Content-Disposition"] = content_disposition(name)
    return Response(content=audio, media_type=mime, headers=headers)


@router.post("/fetch", status_code=status.HTTP_202_ACCEPTED)
async def fetch_from_carrier(tenant: CurrentTenant, call_id: str) -> dict:
    """Pull the recording the carrier made and keep our own copy.

    Carriers expire recordings, so a dashboard that only ever proxies to them
    eventually plays nothing. Copying the composed file into the company's own
    storage is what makes it replayable indefinitely.

    Refuses a partial file. A provider lists a composed recording before it has
    finished writing it, and a half-saved recording that nothing retries is
    worse than none — it marks the call as recorded and stops anyone looking
    for the rest.
    """
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    if call.channel != Channel.PHONE:
        raise Conflict("Only SIM calls have a carrier-side recording.")

    carrier = Infobip(tenant)
    files = []
    if call.provider_dialog_id:
        files = await carrier.dialog_recordings(call.provider_dialog_id)
    if not files and call.provider_call_id:
        files = await carrier.call_recordings(call.provider_call_id)
    if not files:
        call.recording_state = RecordingState.ABSENT
        await call_repo.save_call(call)
        return {"status": "absent", "detail": "The carrier has no recording for this call."}

    chosen = files[0]
    downloaded = await carrier.download_file(str(chosen.get("id") or ""))
    if downloaded is None:
        raise AppError(502, "The carrier would not hand over the file.", code="fetch_failed")
    audio, mime = downloaded

    if not recording_service.is_complete(call_id, audio, chosen, call.duration_seconds):
        call.recording_state = RecordingState.PENDING
        call.recording_file_id = str(chosen.get("id") or "")
        await call_repo.save_call(call)
        return {
            "status": "incomplete",
            "detail": "The carrier is still writing this recording. Try again shortly.",
        }

    call = await recording_service.store_audio(
        tenant, call, audio, mime, provider_file_id=str(chosen.get("id") or "")
    )
    reports.schedule_sync(tenant)
    return {
        "status": call.recording_state.value.lower(),
        "bytes": call.recording_bytes,
        "storedIn": storage.describe(call.recording_path),
    }
