"""
Saving a recording: download or accept the audio, check it is whole, transcode
it, store it, and record where it went.

The completeness check is the important part and the reason this is not three
lines of glue. A provider lists a composed recording before it has finished
writing it, so fetching the moment it appears can return a file that is still
growing. A real 4:18 call was once stored as 1:05 that way — and because the
recording then *looked* saved, nothing ever went back for the rest of it.
"""
from __future__ import annotations

import logging
from typing import Any

from ..models.call import Call, RecordingState
from ..models.tenant import Tenant
from ..repositories import calls as call_repo
from ..repositories import tenants as tenant_repo
from ..config import settings
from . import storage
from .audio import AUDIO_MIME, extension_for, to_mp3

log = logging.getLogger(__name__)


def is_complete(
    call_id: str, audio: bytes, file: dict[str, Any], expect_seconds: int
) -> bool:
    """Does this download actually contain the whole call?

    Two independent checks, because either piece of metadata can be missing:
    the byte count the provider claims, and how long the call is known to have
    lasted. A 2% tolerance on size absorbs container padding; the 10-second
    tolerance on duration absorbs the gap between answer and the recorder
    starting.
    """
    size = file.get("size")
    if isinstance(size, int) and size > 0 and len(audio) < size * 0.98:
        log.info(
            "recording for %s is still being written (%d of %d bytes)",
            call_id, len(audio), size,
        )
        return False
    length = file.get("duration")
    if isinstance(length, (int, float)) and expect_seconds and length < expect_seconds - 10:
        log.info(
            "recording for %s covers %.0fs of a %ds call", call_id, length, expect_seconds
        )
        return False
    return True


async def store_audio(
    tenant: Tenant,
    call: Call,
    audio: bytes,
    mime: str,
    *,
    transcode: bool | None = None,
    provider_file_id: str = "",
) -> Call:
    """Transcode, store, and update the call row. Returns the updated call.

    Raises nothing on a storage miss: `recording_state` becomes FAILED with a
    reason attached, which is visible in the dashboard. A recording that is
    silently absent is the failure mode this whole module exists to avoid.
    """
    if not audio:
        call.recording_state = RecordingState.FAILED
        call.recording_error = "The recording was empty."
        return await call_repo.save_call(call)

    should_transcode = (
        settings.transcode_recordings if transcode is None else transcode
    )
    if should_transcode and "mpeg" not in mime.lower() and "mp3" not in mime.lower():
        converted = await to_mp3(audio)
        if converted:
            log.info(
                "recording for %s: %.1f MB %s -> %.1f MB mp3",
                call.id, len(audio) / 1_048_576, mime, len(converted) / 1_048_576,
            )
            audio, mime = converted, "audio/mpeg"

    extension = extension_for(mime)
    stored = await storage.put(tenant, call.id, audio, mime, extension)

    if provider_file_id:
        # Kept even when our own copy succeeded: it is the fallback source for
        # a call too large for the bucket.
        call.recording_file_id = provider_file_id

    if stored is None:
        call.recording_state = (
            RecordingState.PENDING if call.recording_file_id else RecordingState.FAILED
        )
        call.recording_error = (
            "Too large to store here; still available from the provider."
            if call.recording_file_id
            else "The recording could not be stored."
        )
        log.warning("recording for %s was not stored", call.id)
        return await call_repo.save_call(call)

    call.recording_path = stored.reference
    call.recording_mime = stored.mime
    call.recording_bytes = stored.size
    call.recording_state = RecordingState.READY
    call.recording_error = ""
    saved = await call_repo.save_call(call)

    log.info(
        "saved recording for call %s (%.0f KB, %s)",
        call.id, stored.size / 1024, storage.describe(stored.reference),
    )

    # The first Drive upload for a company creates and caches the folder id.
    # Persisting it here is what stops every later upload re-running the
    # lookup, and stops a second folder appearing if the cache is lost.
    if stored.drive_file_id and tenant.google_drive.folder_id:
        await _remember_drive_folder(tenant)

    return saved


async def _remember_drive_folder(tenant: Tenant) -> None:
    stored = await tenant_repo.get(tenant.phone_number_id)
    if stored is not None and stored.google_drive.folder_id == tenant.google_drive.folder_id:
        return
    try:
        if stored is not None:
            stored.google_drive.folder_id = tenant.google_drive.folder_id
            stored.google_drive.folder_name = tenant.google_drive.folder_name
            await tenant_repo.save(stored)
    except Exception:  # noqa: BLE001 — the recording is already safe
        log.exception("could not remember the Drive folder for %s", tenant.phone_number_id)


async def fetch_audio(tenant: Tenant, call: Call) -> tuple[bytes, str] | None:
    """The call's audio and its mime type, or None if it cannot be reached."""
    if not call.recording_path:
        return None
    audio = await storage.get(tenant, call.recording_path)
    if not audio:
        return None
    return audio, call.recording_mime or AUDIO_MIME.get("MP3", "audio/mpeg")


async def forget(tenant: Tenant, call: Call) -> None:
    """Erase the stored audio and clear the call's recording fields."""
    if call.recording_path:
        await storage.delete(tenant, call.recording_path)
    call.recording_path = ""
    call.recording_mime = ""
    call.recording_bytes = 0
    call.recording_state = RecordingState.NONE
    call.recording_error = ""
    await call_repo.save_call(call)
