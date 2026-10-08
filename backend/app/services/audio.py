"""
Audio handling: transcoding, and naming a file so it opens.

Ported from University-Calling-Agent's Backend/app.py, where the reasoning was
worked out against real calls. The comments explaining *why* are kept, because
each one is a bug that was paid for once already.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from ..config import settings

log = logging.getLogger(__name__)

# Provider file formats -> what a browser needs in order to play them.
AUDIO_MIME = {"WAV": "audio/wav", "MP3": "audio/mpeg", "OGG": "audio/ogg"}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def to_mp3(audio: bytes) -> bytes | None:
    """Re-encode a recording as MP3, or None if that is not possible.

    MediaRecorder produces a *streaming* webm: no duration in the header and no
    seek index, because it is designed to be written while the data is still
    arriving. Such a file plays from the beginning and nothing else — no
    scrubbing, no skip forward, no rewind — in our own player and in whatever
    the operator opens the download with. Re-encoding writes a container with
    the length and frame index that seeking needs, and MP3 in particular plays
    on the machines staff actually use.

    Provider recordings get the same treatment for a different reason: a
    seven-minute call is 6.3 MB as WAV and under a megabyte as 24k mono MP3,
    and every one of those bytes crosses the network before a player can start.

    Input arrives on stdin, but the **output must be a real file**. The Xing
    header that carries the duration and the seek index is written at the start
    of the stream and can only be filled in once encoding has finished, so
    ffmpeg has to seek back to it. Over `pipe:1` it cannot, and `-write_xing`
    is silently ignored: the result decodes, but players estimate its length
    from the bitrate and scrubbing lands in the wrong place. Measured on a
    two-second tone, the piped output reports 2.064s and the file output
    reports 2.000s — the piped one has no Xing header at all.

    The temporary file is deleted in a `finally`, so the audio is on disk only
    for the length of the encode.
    """
    if not audio:
        return None

    target = Path(tempfile.gettempdir()) / f"transcode-{uuid.uuid4().hex}.mp3"
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-i", "pipe:0",
                "-vn",
                "-ac", "1",            # one channel: it is a phone call, not music
                "-ar", "24000",
                "-b:a", "48k",         # ample for speech, and small to store
                "-write_xing", "1",    # the header that makes the file seekable
                "-f", "mp3",
                "-y",
                str(target),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError, NotImplementedError):
            # No ffmpeg in this image, or a loop that cannot spawn subprocesses.
            # Keep the original rather than lose the call.
            log.warning("ffmpeg unavailable — storing the recording as it arrived")
            return None

        try:
            _, errors = await asyncio.wait_for(
                process.communicate(audio), timeout=settings.transcode_timeout_seconds
            )
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            log.warning("transcode timed out — storing the recording as it arrived")
            return None

        if process.returncode != 0:
            log.warning(
                "transcode failed (%s): %s",
                process.returncode,
                (errors or b"")[:200].decode("utf-8", "replace"),
            )
            return None

        converted = target.read_bytes() if target.exists() else b""
        if not converted:
            log.warning("transcode produced nothing — storing the recording as it arrived")
            return None
        return converted
    finally:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)


def extension_for(mime: str) -> str:
    """A filename extension that matches the bytes.

    Provider recordings are WAV; browser-made ones are webm/opus. Naming a webm
    file `.wav` gives the operator a download their media player refuses to
    open.
    """
    mime = (mime or "").lower()
    for needle, extension in (
        ("webm", "webm"),
        ("ogg", "ogg"),
        ("mp4", "mp4"),
        ("mpeg", "mp3"),
        ("mp3", "mp3"),
    ):
        if needle in mime:
            return extension
    return "wav"


def is_audio(mime: str) -> bool:
    mime = (mime or "").lower()
    # video/webm is what MediaRecorder labels an audio-only webm as, so it is
    # accepted: refusing it would reject every browser recording.
    return mime.startswith("audio/") or mime.startswith("video/")
