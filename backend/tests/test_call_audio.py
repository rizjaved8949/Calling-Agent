"""
Pacing and recording.

Both fail quietly. A pacer that sends too fast does not raise — the carrier
drops the leg twenty seconds in, and the logs show a call that simply ended. A
recorder that does not align its two tracks produces a file that plays, in
which the agent answers before the question. So the rules each of those
services learned from real calls are pinned here.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.agent.audio_rate import FRAME_BYTES, SILENT_FRAME
from app.services.agent.pacer import FramePacer


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _pacer(**kw):
    sent: list[bytes] = []
    clock = Clock()
    return FramePacer(sent.append, clock=clock, **kw), sent, clock


def _audio(frames: int) -> bytes:
    return b"\x01\x02" * (FRAME_BYTES // 2) * frames


def run_for(pacer, clock, seconds: float, step: float = 0.005) -> None:
    """Drive the pacer the way the session's loop does.

    Ticking once after jumping the clock is a different thing entirely: that is
    a stall, and the pacer deliberately resyncs rather than replaying the
    frames it missed. Testing the steady state means stepping like the loop.
    """
    for _ in range(int(seconds / step)):
        clock.advance(step)
        pacer.tick()


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


def test_nothing_is_sent_before_the_agent_has_spoken():
    """Streaming silence into a leg that never carried a word closes the socket."""
    pacer, sent, clock = _pacer()
    clock.advance(1.0)
    pacer.tick()
    assert sent == []


def test_audio_goes_out_at_fifty_frames_a_second():
    pacer, sent, clock = _pacer(preroll=1)
    pacer.offer(_audio(100))
    run_for(pacer, clock, 1.0)
    # One second of wall clock is 50 frames of 20 ms, give or take the first.
    assert 48 <= len(sent) <= 52, len(sent)


def test_a_burst_is_not_released_all_at_once():
    """The whole point: the model's sentence must not outrun the line."""
    pacer, sent, clock = _pacer(preroll=1)
    pacer.offer(_audio(200))          # four seconds of audio in one go
    run_for(pacer, clock, 0.2)
    assert len(sent) <= 12, f"{len(sent)} frames in 200ms is faster than real time"
    assert pacer.queued > 150


def test_preroll_holds_the_first_frames_back():
    """Playing the first burst immediately stutters at every burst boundary."""
    pacer, sent, clock = _pacer(preroll=15)
    pacer.offer(_audio(3))
    for _ in range(3):
        clock.advance(0.02)
        pacer.tick()
    assert sent == [], "released before enough audio was in hand"


def test_silence_keeps_the_leg_alive_between_turns():
    """A leg that goes quiet gets dropped; calls ended ten seconds in."""
    pacer, sent, clock = _pacer(preroll=1)
    pacer.offer(_audio(2))
    run_for(pacer, clock, 0.1)
    assert len(sent) >= 2
    sent.clear()

    run_for(pacer, clock, 0.5)         # the caller is talking; we have nothing
    assert sent, "the line went silent"
    assert all(frame == SILENT_FRAME for frame in sent)
    assert 20 <= len(sent) <= 30, f"{len(sent)} silent frames in 500ms"


def test_a_browser_leg_is_left_quiet():
    """Only a carrier needs the keepalive. A browser is happy with silence."""
    pacer, sent, clock = _pacer(preroll=1, keepalive=False)
    pacer.offer(_audio(1))
    run_for(pacer, clock, 0.1)
    sent.clear()
    run_for(pacer, clock, 0.5)
    assert sent == []


def test_a_stall_resyncs_instead_of_bursting():
    """Replaying missed frames floods a line that only drains in real time."""
    pacer, sent, clock = _pacer(preroll=1)
    pacer.offer(_audio(300))
    run_for(pacer, clock, 0.05)
    sent.clear()

    clock.advance(3.0)                 # the host stalled for three seconds
    pacer.tick()
    assert len(sent) <= 2, f"{len(sent)} frames released after a stall"


def test_an_interruption_drops_what_was_queued():
    """Otherwise the agent talks over the caller who just cut in."""
    pacer, sent, clock = _pacer(preroll=1)
    pacer.offer(_audio(100))
    dropped = pacer.flush()
    assert dropped == 100
    assert pacer.queued == 0

    run_for(pacer, clock, 0.1)
    assert all(frame == SILENT_FRAME for frame in sent)


def test_partial_frames_are_held_until_whole():
    """A short write must not go out as a runt frame the carrier rejects."""
    pacer, _, _ = _pacer()
    pacer.offer(b"\x01" * (FRAME_BYTES - 2))
    assert pacer.queued == 0
    pacer.offer(b"\x01\x02")
    assert pacer.queued == 1


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def _read_wav(data: bytes):
    import io
    import wave

    with wave.open(io.BytesIO(data)) as handle:
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype=np.int16), rate


def test_both_sides_land_where_the_clock_says():
    """The failure this prevents: the agent answering before the question."""
    from app.services.agent.recorder import TwoWayRecorder

    clock = Clock()
    rec = TwoWayRecorder("c1", rate=1000, clock=clock)

    # The caller speaks for the first second.
    rec.feed("caller", np.full(1000, 5000, dtype=np.int16).tobytes())
    clock.advance(1.0)
    # Then the agent replies during the second second.
    rec.feed("agent", np.full(1000, 7000, dtype=np.int16).tobytes())
    clock.advance(1.0)

    samples, rate = _read_wav(rec.finish())
    assert rate == 1000
    assert len(samples) == 2000
    # First second: only the caller. Second second: only the agent.
    assert samples[:1000].max() == 5000 and samples[:1000].min() == 5000
    assert samples[1000:].max() == 7000 and samples[1000:].min() == 7000


def test_a_side_that_was_quiet_is_padded_not_shifted():
    from app.services.agent.recorder import TwoWayRecorder

    clock = Clock()
    rec = TwoWayRecorder("c2", rate=1000, clock=clock)
    clock.advance(2.0)                      # two seconds of nobody speaking
    rec.feed("caller", np.full(500, 4000, dtype=np.int16).tobytes())

    samples, _ = _read_wav(rec.finish())
    assert len(samples) == 2500
    assert samples[:2000].max() == 0, "audio was pulled back to the start"
    assert samples[2000:].max() == 4000


def test_overlapping_speech_is_summed_without_overflowing():
    """Two int16 tracks at full level wrap to a loud crack if summed naively."""
    from app.services.agent.recorder import TwoWayRecorder

    clock = Clock()
    rec = TwoWayRecorder("c3", rate=1000, clock=clock)
    rec.feed("caller", np.full(1000, 30000, dtype=np.int16).tobytes())
    rec.feed("agent", np.full(1000, 30000, dtype=np.int16).tobytes())

    samples, _ = _read_wav(rec.finish())
    assert samples.max() == 32767, "clipped to the ceiling, not wrapped"
    assert samples.min() >= 0, "a wrap would show as a large negative value"


def test_a_call_with_no_audio_produces_nothing():
    from app.services.agent.recorder import TwoWayRecorder

    assert TwoWayRecorder("c4").finish() is None


def test_the_temporary_tracks_are_removed():
    """Call audio must not be left on the container's disk."""
    from app.services.agent.recorder import TwoWayRecorder

    rec = TwoWayRecorder("c5", rate=1000, clock=Clock())
    rec.feed("caller", b"\x00\x01" * 100)
    directory = rec._dir
    assert directory.exists()
    rec.finish()
    assert not directory.exists()


@pytest.mark.parametrize("side", ["caller", "agent"])
def test_feeding_after_the_end_is_ignored(side):
    from app.services.agent.recorder import TwoWayRecorder

    rec = TwoWayRecorder("c6", rate=1000, clock=Clock())
    rec.finish()
    rec.feed(side, b"\x01\x02" * 50)        # must not raise or resurrect files
    assert rec.closed
