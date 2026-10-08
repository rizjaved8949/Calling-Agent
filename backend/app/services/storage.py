"""
Where call audio lives.

Three backends, chosen in this order:

1. **the company's Google Drive**, when they have connected one. Their audio,
   their account, their quota.
2. **Supabase Storage**, as the fallback — so a Drive outage during a call does
   not cost that call its recording.
3. **the local disk**, which is the wrong answer anywhere the filesystem is
   replaced on deploy (every free-tier host) but exactly right on a laptop.

`Call.recording_path` holds whichever reference was written, tagged by scheme,
and a read against the wrong backend returns nothing rather than raising —
which sends the caller down the re-fetch path instead of to an error page.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import httpx

from ..config import BASE_DIR, settings
from ..db.supabase import supabase
from ..models.tenant import Tenant
from .drive import DriveError, TenantDrive

log = logging.getLogger(__name__)

# Object keys written to Supabase carry this marker, so a row saved by the
# deployed service is still recognisable on a dev machine reading the same
# database — and a local path stored by that dev machine is not mistaken for
# an object key in production.
REMOTE_PREFIX = "sb://"
# And references to files in a company's Google Drive, by Drive file id.
DRIVE_PREFIX = "gd://"


class StoredRecording:
    """What a successful save produced."""

    def __init__(self, reference: str, mime: str, size: int, *, drive_file_id: str = ""):
        self.reference = reference
        self.mime = mime
        self.size = size
        self.drive_file_id = drive_file_id


def _object_key(tenant: Tenant, call_id: str, extension: str) -> str:
    # Foldered by tenant so one customer's bucket listing is not a directory of
    # everyone else's calls.
    return f"tenants/{tenant.phone_number_id}/calls/{call_id}.{extension}"


def _local_path(tenant: Tenant, call_id: str, extension: str) -> Path:
    return settings.recordings_path / tenant.phone_number_id / f"{call_id}.{extension}"


async def put(
    tenant: Tenant, call_id: str, data: bytes, mime: str, extension: str
) -> StoredRecording | None:
    """Store the audio and return the reference to record, or None.

    None is not necessarily a failure: a recording too large for the bucket is
    deliberately left with the provider, whose file id the call row still
    carries, so playback streams from them instead.
    """
    drive = TenantDrive(tenant)
    if drive.configured:
        try:
            uploaded = await drive.upload(f"call-{call_id}.{extension}", data, mime)
            return StoredRecording(
                f"{DRIVE_PREFIX}{uploaded['id']}", mime, len(data),
                drive_file_id=uploaded["id"],
            )
        except (DriveError, httpx.HTTPError) as exc:
            log.warning("Drive upload for call %s failed: %s", call_id, exc)
            # Fall through to Supabase rather than lose the recording.

    if settings.supabase_configured:
        if len(data) > settings.supabase_max_upload_bytes:
            log.warning(
                "recording for %s is %.0f MB — over the bucket limit, leaving it "
                "with the provider",
                call_id,
                len(data) / 1_048_576,
            )
            return None
        key = _object_key(tenant, call_id, extension)
        try:
            response = await supabase.storage().post(
                f"/object/{settings.supabase_bucket}/{key}",
                content=data,
                # The same call id overwrites rather than 409s, so re-saving a
                # recording is repeatable instead of a one-time operation.
                headers={"content-type": mime, "x-upsert": "true"},
            )
        except httpx.HTTPError as exc:
            log.warning("recording upload for %s failed: %s", call_id, exc)
            return None
        if response.status_code >= 400:
            log.warning(
                "recording upload for %s returned %s: %s",
                call_id, response.status_code, response.text[:200],
            )
            return None
        return StoredRecording(f"{REMOTE_PREFIX}{key}", mime, len(data))

    target = _local_path(tenant, call_id, extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    stored: Path = target
    with contextlib.suppress(ValueError):
        stored = target.relative_to(BASE_DIR)
    return StoredRecording(stored.as_posix(), mime, len(data))


async def signed_url(reference: str, seconds: int | None = None) -> str | None:
    """A temporary direct link to the audio, or None if there is no object.

    Serving audio through the API means every byte crosses the network twice —
    storage to server, server to browser — and the player cannot start until
    the last byte of that arrives. A signed link lets the browser fetch from
    storage itself: playback begins on the first chunk, seeking works through
    range requests, and the server carries none of it.

    The bucket stays private. The link is signed, time-limited, and minted only
    for someone who already asked this API for this call.

    Drive files get no link: a Drive URL needs the viewer signed in to the
    account that owns the folder, which the company's staff are not. Those are
    streamed through the API instead.
    """
    if not reference.startswith(REMOTE_PREFIX) or not settings.supabase_configured:
        return None
    key = reference[len(REMOTE_PREFIX):]
    ttl = seconds or settings.supabase_signed_url_ttl_seconds
    try:
        response = await supabase.storage().post(
            f"/object/sign/{settings.supabase_bucket}/{key}", json={"expiresIn": ttl}
        )
    except httpx.HTTPError as exc:
        log.warning("could not sign %s: %s", key, exc)
        return None
    if response.status_code >= 400:
        return None
    signed = (response.json() or {}).get("signedURL")
    return f"{settings.supabase_url.rstrip('/')}/storage/v1{signed}" if signed else None


async def get(tenant: Tenant, reference: str) -> bytes | None:
    """The stored audio, or None if this backend cannot see it."""
    if not reference:
        return None

    if reference.startswith(DRIVE_PREFIX):
        drive = TenantDrive(tenant)
        if not drive.configured:
            return None
        return await drive.download(reference[len(DRIVE_PREFIX):])

    if reference.startswith(REMOTE_PREFIX):
        if not settings.supabase_configured:
            return None
        key = reference[len(REMOTE_PREFIX):]
        try:
            response = await supabase.storage().get(
                f"/object/{settings.supabase_bucket}/{key}"
            )
        except httpx.HTTPError as exc:
            log.warning("recording fetch failed: %s", exc)
            return None
        return response.content if response.status_code < 400 and response.content else None

    file = Path(reference) if Path(reference).is_absolute() else BASE_DIR / reference
    if file.exists() and file.stat().st_size > 0:
        return file.read_bytes()
    return None


async def delete(tenant: Tenant, reference: str) -> None:
    """Erase the audio. Best effort: a deleted call must still delete."""
    if not reference:
        return

    if reference.startswith(DRIVE_PREFIX):
        drive = TenantDrive(tenant)
        if drive.configured:
            await drive.delete(reference[len(DRIVE_PREFIX):])
        return

    if reference.startswith(REMOTE_PREFIX):
        if settings.supabase_configured:
            key = reference[len(REMOTE_PREFIX):]
            with contextlib.suppress(httpx.HTTPError):
                await supabase.storage().delete(
                    f"/object/{settings.supabase_bucket}/{key}"
                )
        return

    file = Path(reference) if Path(reference).is_absolute() else BASE_DIR / reference
    with contextlib.suppress(OSError):
        file.unlink()


def describe(reference: str) -> str:
    """Where a reference points, for the dashboard and the report."""
    if reference.startswith(DRIVE_PREFIX):
        return "google-drive"
    if reference.startswith(REMOTE_PREFIX):
        return "supabase"
    return "local-disk" if reference else "none"
