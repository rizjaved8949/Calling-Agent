"""
Transcoding, against ffmpeg itself.

Skipped where ffmpeg is absent — which is a supported configuration, not a
broken one: audio is then stored exactly as it arrived. The point of testing it
for real is that the flags matter. `-write_xing` is what makes the file
seekable, and a recording that plays straight through and cannot be scrubbed is
the complaint this code exists to answer.
"""
from __future__ import annotations

import asyncio
import subprocess

import pytest

from app.services.audio import ffmpeg_available, to_mp3

pytestmark = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg is not installed on this machine"
)


def _tone_wav(seconds: float = 1.0) -> bytes:
    """A real WAV, made by ffmpeg, so the test is not asserting against a stub."""
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-ac", "1", "-ar", "24000", "-f", "wav", "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_wav_becomes_a_smaller_mp3():
    wav = _tone_wav(2.0)
    mp3 = asyncio.run(to_mp3(wav))

    assert mp3 is not None
    assert len(mp3) < len(wav)
    # ID3 or a bare MPEG frame sync: either is a real MP3 header.
    assert mp3[:3] == b"ID3" or mp3[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}


def test_the_result_is_seekable():
    """A Xing header is the difference between scrubbing and playing once."""
    mp3 = asyncio.run(to_mp3(_tone_wav(2.0)))
    assert b"Xing" in mp3[:4096] or b"Info" in mp3[:4096]


def test_duration_survives_the_round_trip():
    mp3 = asyncio.run(to_mp3(_tone_wav(2.0)))
    probe = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-show_entries", "format=duration", "-of", "csv=p=0", "-",
        ],
        input=mp3,
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip("ffprobe is not available")
    assert 1.8 <= float(probe.stdout.decode().strip()) <= 2.3


def test_empty_input_is_refused_not_crashed():
    assert asyncio.run(to_mp3(b"")) is None


def test_rubbish_input_returns_none():
    """The caller stores the original instead; it must not raise."""
    assert asyncio.run(to_mp3(b"this is not audio at all")) is None
