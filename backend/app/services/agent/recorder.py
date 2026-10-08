"""
Recording both sides of a call, aligned to one timeline.

The two halves of a conversation do not arrive evenly: the caller's audio comes
continuously from the carrier, the agent's in bursts from the model, and
neither sends anything while the other is talking. Appending each to its own
buffer and mixing them at the end produces a recording where the agent answers
before the question — both sides compressed to the length of their own speech,
with the silences removed.

So each side is padded to where the wall clock says it should be before its
audio is written. The gaps are what make the timeline real.

Written to temporary files rather than memory: a ten-minute call is around
57 MB of PCM across both tracks, and a container with several calls in flight
should not be holding that. The files are mixed at the end and deleted in a
`finally`.
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .audio_rate import pcm_to_wav

log = logging.getLogger(__name__)

# Both sides are stored at the model's rate: the caller is upsampled once on
# the way in rather than the agent downsampled on every burst, and 24 kHz is
# what the recording keeps.
RECORD_RATE = 24_000


class TwoWayRecorder:
    """Caller and agent, mixed into one timeline-aligned track."""

    def __init__(self, call_id: str, *, rate: int = RECORD_RATE,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.call_id = call_id
        self.rate = rate
        self.clock = clock
        self._started = clock()
        self._dir = Path(tempfile.mkdtemp(prefix="call-"))
        self._paths = {
            "caller": self._dir / "caller.pcm",
            "agent": self._dir / "agent.pcm",
        }
        self._files = {k: open(v, "wb", buffering=0) for k, v in self._paths.items()}
        self._samples = {"caller": 0, "agent": 0}
        self.closed = False

    def feed(self, side: str, pcm16: bytes) -> None:
        """Audio from one side, at `rate`, placed where the clock says it goes."""
        if self.closed or not pcm16:
            return
        side = "caller" if side == "caller" else "agent"
        handle = self._files[side]

        # Pad to now, so a side that has been quiet does not have its next
        # words land where its last ones ended.
        due = max(0, int((self.clock() - self._started) * self.rate))
        have = self._samples[side]
        if due > have:
            handle.write(b"\x00\x00" * (due - have))
            have = due
        handle.write(pcm16)
        self._samples[side] = have + len(pcm16) // 2

    @property
    def seconds(self) -> float:
        return max(self._samples.values(), default=0) / self.rate

    def finish(self) -> bytes | None:
        """Mix both sides and return a WAV, or None if nothing was captured."""
        if self.closed:
            return None
        self.closed = True
        for handle in self._files.values():
            with contextlib.suppress(OSError):
                handle.close()
        try:
            caller = np.fromfile(self._paths["caller"], dtype=np.int16)
            agent = np.fromfile(self._paths["agent"], dtype=np.int16)
            if not caller.size and not agent.size:
                return None

            length = max(caller.size, agent.size)
            caller = np.pad(caller, (0, length - caller.size))
            agent = np.pad(agent, (0, length - agent.size))

            # Summed in int32 first: two int16 tracks at full level overflow,
            # and an overflow in audio is not a quiet artefact — it is a loud
            # crack on every loud moment. Clipped once, at the end.
            mixed = np.clip(
                caller.astype(np.int32) + agent.astype(np.int32), -32768, 32767
            ).astype(np.int16)
            return pcm_to_wav(mixed.tobytes(), self.rate)
        except (OSError, ValueError):
            log.exception("call %s: could not mix the recording", self.call_id)
            return None
        finally:
            self.discard()

    def discard(self) -> None:
        """Remove the temporary tracks. Call audio does not linger on disk."""
        self.closed = True
        for handle in self._files.values():
            with contextlib.suppress(OSError):
                handle.close()
        for path in self._paths.values():
            with contextlib.suppress(OSError):
                os.unlink(path)
        with contextlib.suppress(OSError):
            os.rmdir(self._dir)
