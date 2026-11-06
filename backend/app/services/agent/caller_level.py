"""
The caller's voice, brought up to a level the model can hear.

Measured on real Twilio calls: the caller arrived around -28 to -36 dBFS,
about 15 dB under the agent and well under the -20 dBFS a phone line usually
carries speech at. Nothing on our side loses it (decode and resampling keep
level exactly; tests/test_mulaw.py), it is simply how quiet those calls are.
The model listens with its speech detector set to low sensitivity, so it does
not mistake the agent's own echo for the caller, and a caller that quiet sits
under it: the model talks over their "hello?" and decides they have gone.

So this is a slow, bounded gain:

* It only ever raises the level, by at most `MAX_GAIN` (+12 dB). A caller
  who is already loud enough passes through untouched.
* It learns the level from speech only. Frames under `SPEECH_FLOOR` are line
  noise and never push the gain up, so a quiet line with a hiss stays a
  quiet line with a hiss.
* It changes smoothly, sample by sample across each frame, so there is no
  step or click, and it never pushes a peak past `PEAK_CEILING`.

Only for what the model hears. The recording keeps the call as it arrived.
"""
from __future__ import annotations

import numpy as np

#: The speech level aimed for, as a fraction of full scale (about -20 dBFS).
TARGET_RMS = 0.1
#: Never more than this (+12 dB), so a near-silent line is not made loud.
MAX_GAIN = 4.0
#: A frame quieter than this (about -50 dBFS) is not speech.
SPEECH_FLOOR = 10 ** (-50 / 20)
#: A frame this loud (about -42 dBFS) is the caller talking, for deciding
#: whether they are still there. Higher than `SPEECH_FLOOR` so a noisy room
#: alone does not count; the quietest real caller measured was -36 dBFS.
VOICE_FLOOR = 10 ** (-42 / 20)
#: No sample is pushed past this (about -1 dBFS).
PEAK_CEILING = 0.9
#: How quickly the level estimate follows speech, per 20 ms frame.
_RISE = 0.2      # louder speech: within a few frames, so a shout is not boosted
_FALL = 0.02     # quieter speech: over about a second, so a pause does not pump


def rms(pcm16: bytes) -> float:
    """A frame's level as a fraction of full scale."""
    if len(pcm16) < 2:
        return 0.0
    samples = np.frombuffer(pcm16[: len(pcm16) - len(pcm16) % 2], dtype="<i2")
    return float(np.sqrt(np.mean((samples.astype(np.float32) / 32768.0) ** 2)))


class CallerLevel:
    """One call's gain on the caller's side. Feed it every 20 ms frame."""

    def __init__(self) -> None:
        self.speech_level = 0.0   # 0 until the caller has said something
        self.gain = 1.0

    def process(self, pcm16: bytes) -> bytes:
        usable = len(pcm16) - len(pcm16) % 2
        if not usable:
            return pcm16
        samples = np.frombuffer(pcm16[:usable], dtype="<i2").astype(np.float32) / 32768.0
        level = float(np.sqrt(np.mean(samples ** 2)))

        if level >= SPEECH_FLOOR:
            if self.speech_level == 0.0:
                self.speech_level = level
            else:
                step = _RISE if level > self.speech_level else _FALL
                self.speech_level += step * (level - self.speech_level)

        wanted = 1.0
        if self.speech_level > 0.0:
            wanted = min(MAX_GAIN, max(1.0, TARGET_RMS / self.speech_level))
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak > 0.0:
            wanted = max(1.0, min(wanted, PEAK_CEILING / peak))

        # Up gradually, so there is no step; down at once, so a sudden loud
        # word is never boosted by the gain meant for the quiet before it.
        start = self.gain if wanted > self.gain else wanted
        ramp = np.linspace(start, wanted, num=samples.size, dtype=np.float32)
        self.gain = wanted
        out = np.clip(samples * ramp, -1.0, 32767 / 32768.0)
        return (out * 32768.0).astype("<i2").tobytes() + pcm16[usable:]
