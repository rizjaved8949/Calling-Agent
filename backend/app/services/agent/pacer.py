"""
Sending the agent's voice down a line at the speed a line runs.

The model produces audio in bursts — a whole sentence arrives in a few hundred
milliseconds. A phone line drains in real time, one 20 ms frame per 20 ms, and
every rule below is something a carrier taught the earlier services by dropping
the call:

* **Never send faster than real time.** A reply pushed out as fast as the model
  made it sits in the carrier's buffer, where a barge-in can no longer stop it.
  The caller keeps hearing an agent that has already been told to be quiet.
* **Never go silent once started.** A leg that carries nothing gets dropped.
  Calls were ending ten to twenty seconds in, and the ones that survived
  longest were the ones where the agent talked most.
* **Never send silence first.** Streaming into a leg that has not yet carried a
  word of real audio is what got the socket closed before anything was said.
* **Resync, do not catch up.** When the host stalls for a second, replaying the
  missed frames puts a burst of real-time audio on a line that drains in real
  time. Late audio is dropped instead: a lost fiftieth of a second is
  inaudible, and the leg survives.

The clock is injectable so this is testable. A pacer that can only be driven by
the wall clock can only be tested by waiting, and nobody writes those tests.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable

from .audio_rate import FRAME_BYTES, FRAME_MS, SILENT_FRAME

log = logging.getLogger(__name__)

# How much of a reply to hold before playing it. The model's audio arrives in
# bursts and the line wants it evenly; without a little in hand, every burst
# boundary is a gap the caller hears as a stutter.
PREROLL_FRAMES = 15


class FramePacer:
    """Holds the agent's audio and releases it one frame per 20 ms."""

    def __init__(
        self,
        send: Callable[[bytes], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        preroll: int = PREROLL_FRAMES,
        keepalive: bool = True,
    ) -> None:
        self.send = send
        self.clock = clock
        self.preroll = preroll
        # Whether to fill gaps with silence. True for a carrier leg that would
        # otherwise be dropped; false for a browser, which is happy with quiet.
        self.keepalive = keepalive

        self._queue: deque[bytes] = deque()
        self._pending = b""
        self._next_at: float | None = None
        self._buffering = True
        self._waited = 0
        self._spoken = False
        self.frames_sent = 0
        self.frames_dropped = 0

    # ---- input ------------------------------------------------------------

    def offer(self, pcm16: bytes) -> None:
        """Agent audio at the line's rate, cut into frames the line accepts."""
        if not pcm16:
            return
        buffer = self._pending + pcm16
        while len(buffer) >= FRAME_BYTES:
            self._queue.append(buffer[:FRAME_BYTES])
            buffer = buffer[FRAME_BYTES:]
        self._pending = buffer

    def flush(self) -> int:
        """Drop everything queued. The caller cut in and the agent must stop.

        Returns how many frames were dropped, which is worth logging: a large
        number means the agent was a long way into a sentence nobody heard the
        end of.
        """
        dropped = len(self._queue)
        self._queue.clear()
        self._pending = b""
        self._buffering = True
        self._waited = 0
        self.frames_dropped += dropped
        return dropped

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def speaking(self) -> bool:
        return bool(self._queue)

    # ---- output -----------------------------------------------------------

    def tick(self) -> int:
        """Release whatever is due by now. Returns how many frames went out.

        Called in a loop; the loop decides how often. Doing the work here
        rather than in a sleep loop is what makes the timing testable.
        """
        now = self.clock()
        if self._next_at is None:
            self._next_at = now

        # A stall longer than two frames is a stall, not a backlog.
        if now - self._next_at > FRAME_MS * 2 / 1000:
            self._next_at = now

        sent = 0
        while self._next_at <= now:
            self._next_at += FRAME_MS / 1000
            frame = self._take()
            if frame is None:
                continue
            self.send(frame)
            self.frames_sent += 1
            sent += 1
        return sent

    def _take(self) -> bytes | None:
        if self._buffering and self._queue:
            self._waited += 1
            if len(self._queue) >= self.preroll or self._waited > self.preroll:
                self._buffering = False
                self._waited = 0

        if not self._buffering:
            if self._queue:
                self._spoken = True
                return self._queue.popleft()
            # Ran dry mid-reply: buffer again rather than stutter.
            self._buffering = True
            self._waited = 0

        # Nothing of ours to send. Keep the leg alive with silence, but only
        # after something real has gone out.
        if self.keepalive and self._spoken:
            return SILENT_FRAME
        return None
