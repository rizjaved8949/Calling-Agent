"""
Resampling between the rates the model and the phone each insist on.

Gemini Live listens at 16 kHz and speaks at 24 kHz. A phone line carries
16 kHz. So the caller's audio needs nothing and the reply needs 24 → 16, which
is a 2:3 ratio.

Ported from University-Calling-Agent's `gemini_agent.py`, where the ordering
below was learned the hard way.
"""
from __future__ import annotations

import numpy as np

# What each side of the bridge speaks.
PHONE_RATE = 16_000
GEMINI_IN_RATE = 16_000
GEMINI_OUT_RATE = 24_000

FRAME_MS = 20
# 20 ms of 16 kHz PCM16 — the only frame size a carrier socket accepts.
FRAME_BYTES = int(PHONE_RATE * FRAME_MS / 1000) * 2  # 640
SILENT_FRAME = b"\x00" * FRAME_BYTES


def _low_pass_taps(cutoff: float, length: int = 121) -> np.ndarray:
    """A windowed-sinc low pass, as a symmetric odd-length kernel."""
    n = length if length % 2 else length + 1
    k = np.arange(n) - (n - 1) / 2
    with np.errstate(invalid="ignore"):
        sinc = np.where(k == 0, 2 * cutoff, np.sin(2 * np.pi * cutoff * k) / (np.pi * k))
    taps = sinc * np.hamming(n)
    return taps / taps.sum()


class RateConverter:
    """Resample by up:down, keeping filter state across frames.

    Upsample, filter, then decimate — in that order, because a filter applied
    *after* decimation cannot remove aliasing the decimation has already folded
    in. The state carries over so joining 20 ms frames does not click, and the
    phase is remembered so the pattern resumes on the right sample rather than
    shifting pitch on every frame boundary.
    """

    def __init__(self, up: int, down: int, keep: float = 0.9) -> None:
        self.up = up
        self.down = down
        self.taps = _low_pass_taps(0.5 * keep / max(up, down))
        self.tail = np.zeros(len(self.taps) - 1, dtype=np.float32)
        self.phase = 0

    def process(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stuffed = np.zeros(len(samples) * self.up, dtype=np.float32)
        stuffed[:: self.up] = samples * self.up

        joined = np.concatenate([self.tail, stuffed])
        filtered = np.convolve(joined, self.taps, mode="valid")
        self.tail = joined[-(len(self.taps) - 1):] if len(self.taps) > 1 else joined[:0]

        out = filtered[self.phase :: self.down]
        used = self.phase + self.down * len(out)
        self.phase = used - len(filtered)
        return (np.clip(out, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def gemini_to_phone() -> RateConverter:
    """24 kHz from the model down to the 16 kHz a line carries."""
    return RateConverter(2, 3)


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    """Wrap raw PCM16 mono so it can be played or inspected."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()
