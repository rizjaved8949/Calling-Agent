"""
The caller's voice on a WhatsApp call.

Opus decodes to 48 kHz *stereo*, and a packed frame's `to_ndarray()` is one
interleaved row. Reading that as mono and dividing the rate by three gives left
and right alternating at twice the speed — noise, not speech. The agent still
greets the caller (its own track is fine), then never answers again, which is
exactly what a real call did before this was fixed.
"""
from __future__ import annotations

import asyncio
import math

import pytest

av = pytest.importorskip("av")

from app.services.agent.audio_rate import PHONE_RATE
from app.services.agent.whatsapp_media import _resampled


def _stereo_frame(samples: int = 960, rate: int = 48_000, hz: float = 440.0):
    """20 ms of a tone, as Opus hands it over: 48 kHz, stereo, packed s16."""
    import numpy as np

    frame = av.AudioFrame(format="s16", layout="stereo", samples=samples)
    tone = [int(12000 * math.sin(2 * math.pi * hz * i / rate)) for i in range(samples)]
    interleaved = np.empty(samples * 2, dtype=np.int16)
    interleaved[0::2] = tone          # left
    interleaved[1::2] = tone          # right
    frame.planes[0].update(interleaved.tobytes())
    frame.sample_rate = rate
    frame.pts = 0
    frame.time_base = __import__("fractions").Fraction(1, rate)
    return frame


def test_stereo_48k_becomes_mono_16k_not_double_speed():
    """The sample count is the whole test.

    Five 20 ms frames are 100 ms, which is 1600 mono samples at 16 kHz. The old
    path produced twice that — left and right interleaved and read as one
    channel. The tolerance is for the resampler priming its filter, which holds
    back a few samples on the first frame and never loses them.
    """
    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=PHONE_RATE,
    )
    pcm = b""
    for index in range(5):
        frame = _stereo_frame()
        frame.pts = index * 960
        pcm += b"".join(c.to_ndarray().tobytes() for c in _resampled(resampler, frame))

    samples = len(pcm) // 2
    assert 1550 <= samples <= 1600, f"expected about 1600 mono samples, got {samples}"
    assert samples < 2000, "left and right are still interleaved — double speed"


def test_the_tone_survives_the_conversion():
    """Not just the right length — still a 440 Hz tone, not noise."""
    import numpy as np

    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=PHONE_RATE,
    )
    pcm = b""
    for index in range(10):                      # 200 ms, enough to measure
        frame = _stereo_frame()
        frame.pts = index * 960
        pcm += b"".join(
            c.to_ndarray().tobytes() for c in _resampled(resampler, frame)
        )

    samples = np.frombuffer(pcm, dtype=np.int16).astype(float)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    peak_hz = float(np.fft.rfftfreq(len(samples), 1 / PHONE_RATE)[spectrum.argmax()])
    assert abs(peak_hz - 440.0) < 25, f"the tone came out at {peak_hz:.0f} Hz, not 440"


def test_a_bare_frame_from_older_pyav_is_not_dropped():
    """`resample()` returns a list on PyAV 9+, a single frame before that.
    Treating a bare frame as iterable silently drops every caller."""
    frame = _stereo_frame()

    class Old:
        def resample(self, _frame):
            return frame

    class Nothing:
        def resample(self, _frame):
            return None

    assert _resampled(Old(), frame) == [frame]
    assert _resampled(Nothing(), frame) == []


@pytest.mark.asyncio
async def test_the_drain_loop_feeds_the_session():
    """End to end through the bridge's own loop, with a fake track."""
    from app.services.agent import whatsapp_media

    bridge = whatsapp_media.WhatsAppBridge.__new__(whatsapp_media.WhatsAppBridge)
    bridge.call_id = "c-1"
    bridge.closed = False

    heard: list[bytes] = []

    class Session:
        def feed_caller(self, pcm):
            heard.append(pcm)

    bridge._session = Session()

    sent = [_stereo_frame(), _stereo_frame(), _stereo_frame()]

    class Track:
        def __init__(self):
            self.left = list(sent)

        async def recv(self):
            if not self.left:
                raise RuntimeError("track ended")
            frame = self.left.pop(0)
            frame.pts = (len(sent) - len(self.left) - 1) * 960
            return frame

    await whatsapp_media.WhatsAppBridge._drain(bridge, Track())

    assert heard, "no caller audio reached the session"
    total = sum(len(chunk) for chunk in heard) // 2
    # Three 20 ms frames: 960 mono samples at 16 kHz, give or take the
    # resampler's own priming.
    assert 600 <= total <= 960, f"{total} samples from 60 ms of audio"
