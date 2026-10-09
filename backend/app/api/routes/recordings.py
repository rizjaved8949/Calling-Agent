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
from pydantic import BaseModel, ConfigDict, Field

from ...errors import AppError, Conflict, NotFound
from ...http_headers import content_disposition
from ...models.call import Channel, RecordingState
from ...repositories import calls as call_repo
from ...services import recordings as recording_service
from ...services import reports, storage
from ...services.audio import extension_for, is_audio
from ...repositories import tenants as tenant_repo
from ...security import playback
from ...services.telephony import Infobip
from ..deps import CurrentMember, CurrentTenant, current_tenant, refuse_staff

log = logging.getLogger(__name__)

router = APIRouter(prefix="/calls/{call_id}/recording", tags=["recordings"])

# Not under /calls/{call_id}: this acts on many at once, and nesting it there
# would have "recordings" read as a call id.
bulk_router = APIRouter(prefix="/recordings", tags=["recordings"])


class BulkDelete(BaseModel):
    """Which recordings to erase.

    The filters mirror the ones on the screen, so what is deleted is what the
    person was looking at — a bulk delete that quietly uses a different set
    than the list in front of you is how a year of audio disappears.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # The rows the screen was showing. Preferred over the filters below,
    # because the screen filters on more than the API does — direction, which
    # number, whether it has audio — and a bulk delete that works from a
    # coarser filter than the list in front of you deletes more than you
    # agreed to. Ids leave no room for that.
    call_ids: list[str] | None = Field(default=None, alias="callIds", max_length=1000)
    channel: Channel | None = None
    counterparty: str | None = None
    # Also remove the call rows, not just their audio. Off by default: the
    # record of who rang and when is usually worth keeping after the voice
    # has to go.
    delete_calls: bool = Field(default=False, alias="deleteCalls")
    # A deliberate guard against a mis-click: the screen sends the number it
    # showed, and a mismatch is refused rather than taking "about that many".
    expected: int | None = None


@bulk_router.post("/delete")
async def delete_many(
    tenant: CurrentTenant, member: CurrentMember, payload: BulkDelete,
) -> dict:
    """Erase the audio for every call matching the filters.

    Done here rather than one request per call from the browser: a hundred
    deletes is a hundred round trips, and a tab closed halfway through leaves
    half of them gone with no way to tell which.
    """
    refuse_staff(member, "delete recordings")
    if payload.call_ids:
        found = [
            await call_repo.get_call(tenant.phone_number_id, call_id)
            for call_id in dict.fromkeys(payload.call_ids)
        ]
        # A call of another company's resolves to None and is simply absent.
        calls = [c for c in found if c is not None]
    else:
        calls = await call_repo.list_calls(
            tenant.phone_number_id,
            limit=1000,
            channel=payload.channel.value if payload.channel else None,
            counterparty=payload.counterparty or None,
        )
    matching = [c for c in calls if c.recording_path or payload.delete_calls]
    if payload.expected is not None and payload.expected != len(matching):
        raise Conflict(
            f"This would delete {len(matching)} recordings, not the "
            f"{payload.expected} on your screen. Refresh and try again."
        )

    erased = failed = 0
    for call in matching:
        try:
            if call.recording_path:
                await recording_service.forget(tenant, call)
            if payload.delete_calls:
                await call_repo.delete_call(tenant.phone_number_id, call.id)
            erased += 1
        except Exception:  # noqa: BLE001 — one stubborn file must not stop the rest
            failed += 1
            log.exception("could not delete the recording for call %s", call.id)

    reports.schedule_sync(tenant)
    log.info(
        "tenant %s bulk-deleted %d recording(s)%s (%d failed)",
        tenant.phone_number_id, erased,
        " and their calls" if payload.delete_calls else "", failed,
    )
    return {"deleted": erased, "failed": failed, "deletedCalls": payload.delete_calls}


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


@router.post("/link", status_code=status.HTTP_200_OK)
async def playback_link(tenant: CurrentTenant, call_id: str) -> dict:
    """A URL an <audio> element can actually load.

    A media element sends no Authorization header, so the credential has to be
    in the URL. This mints a short-lived token that names this one recording
    for this one company — see `security/playback.py`.
    """
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    if not call.recording_path:
        raise NotFound("Recording")
    token = playback.mint(tenant.phone_number_id, call_id)
    return {
        "url": f"/api/calls/{call_id}/recording?token={token}",
        "expiresInSeconds": 900,
    }


@router.get("")
async def get_recording(
    request: Request,
    call_id: str,
    download: bool = False,
    link: bool = False,
    token: str = "",
) -> Response:
    """The call audio.

    Reached two ways: with the company's bearer token, as every other route is,
    or with a playback token in the query string, which is how an `<audio>`
    element gets in.

    `?link=true` redirects to a signed storage URL where one exists, so the
    bytes go straight from storage to the browser instead of crossing the
    network twice. Drive-stored audio has no such link — a Drive URL needs the
    viewer signed in to the company's own Google account — so it streams here.
    """
    if token:
        tenant_id = playback.verify(token, call_id)
        tenant = await tenant_repo.require(tenant_id)
    else:
        tenant = await current_tenant(
            request,
            request.headers.get("authorization"),
            request.headers.get("x-admin-key"),
        )

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
        # Seeking in an <audio> element depends on the server advertising this
        # *and* honouring it below. Advertising it alone would be a lie the
        # player believes.
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
    }
    if download:
        name = f"call-{call_id}.{extension_for(mime)}"
        headers["Content-Disposition"] = content_disposition(name)

    span = _requested_range(request.headers.get("range"), len(audio))
    if span is None:
        headers["Content-Length"] = str(len(audio))
        return Response(content=audio, media_type=mime, headers=headers)

    start, end = span
    headers["Content-Range"] = f"bytes {start}-{end}/{len(audio)}"
    headers["Content-Length"] = str(end - start + 1)
    return Response(
        content=audio[start : end + 1],
        media_type=mime,
        headers=headers,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
    )


def _requested_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The byte span a player asked for, or None to send the whole file.

    Only the single-span `bytes=start-end` form is handled, which is the only
    form a media element sends. Anything malformed or unsatisfiable falls back
    to the whole file rather than a 416: a player that gets the audio works,
    and one that gets an error does not.
    """
    if not header or not header.startswith("bytes=") or size == 0:
        return None
    span = header[len("bytes="):].split(",")[0].strip()
    start_text, _, end_text = span.partition("-")
    try:
        if not start_text:
            # "bytes=-500" means the last 500 bytes.
            length = int(end_text)
            if length <= 0:
                return None
            start, end = max(0, size - length), size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_recording(
    tenant: CurrentTenant, member: CurrentMember, call_id: str,
) -> None:
    """Erase the audio but keep the call.

    Separate from deleting the call because they are different decisions: a
    company may need the record of who rang and when long after it has to
    stop keeping their voice.

    An employee may listen to their own calls but not erase them: a recording
    is the company's record of what was said on its behalf, and the person who
    said it is the last one who should be able to remove it.
    """
    refuse_staff(member, "delete a recording")
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        raise NotFound("Call")
    if not call.recording_path:
        raise NotFound("Recording")
    await recording_service.forget(tenant, call)
    reports.schedule_sync(tenant)
    log.info("recording for call %s deleted by the company", call_id)


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
