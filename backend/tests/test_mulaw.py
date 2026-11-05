"""
Twilio's 8 kHz μ-law, converted to the agent's 16 kHz PCM and back.

Wrong here is not an error anywhere: it is a caller who hears static, or an
agent that hears nothing it can understand. So the codec is pinned against
G.711's own table and an independent implementation, and the rate steps by
what they do to a tone.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from app.services.agent import mulaw
from app.services.agent.audio_rate import FRAME_BYTES


def _tone(freq: float, rate: int, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)


def _level(pcm: bytes) -> float:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def _audioop():
    """Python's own G.711, removed in 3.13; the cross-check skips without it."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            import audioop
        except ImportError:
            pytest.skip("audioop is not available on this Python")
    return audioop


# ---------------------------------------------------------------------------
# The codec
# ---------------------------------------------------------------------------

def test_known_g711_values():
    assert mulaw.decode(bytes([0x00, 0x80, 0xFF, 0x7F])) == np.array(
        [-32124, 32124, 0, 0], dtype="<i2").tobytes()


def test_decoding_matches_pythons_own_table_for_every_byte():
    every_byte = bytes(range(256))
    assert mulaw.decode(every_byte) == _audioop().ulaw2lin(every_byte, 2)


def test_encoding_matches_pythons_own_encoder_for_every_sample():
    every_sample = np.arange(-32768, 32768, dtype="<i2").tobytes()
    assert mulaw.encode(every_sample) == _audioop().lin2ulaw(every_sample, 2)


def test_every_byte_survives_a_round_trip():
    """Decode then encode is the identity, apart from μ-law's two zeros
    (0x7F and 0xFF both mean silence), which encode to the one Twilio uses."""
    for code in range(256):
        again = mulaw.encode(mulaw.decode(bytes([code])))[0]
        assert again == (0xFF if code == 0x7F else code), hex(code)


def test_a_round_trip_loses_no_more_than_mu_law_allows():
    """μ-law keeps about 13 bits of precision near silence and fewer in loud
    passages: within 1/16 of the sample's own size, plus a little near zero."""
    samples = np.arange(-32768, 32768, 7, dtype=np.int32)
    back = np.frombuffer(mulaw.decode(mulaw.encode(samples.astype("<i2").tobytes())),
                         dtype="<i2").astype(np.int32)
    clipped = np.clip(samples, -32635, 32635)
    assert np.all(np.abs(back - clipped) <= np.abs(clipped) / 16 + 8)


def test_silence_is_the_silence_byte():
    assert mulaw.encode(b"\x00\x00" * 160) == bytes([mulaw.MULAW_SILENCE]) * 160


def test_half_a_sample_is_dropped_not_guessed():
    assert mulaw.encode(b"\x00") == b""
    assert len(mulaw.encode(b"\x00\x00\x00")) == 1


# ---------------------------------------------------------------------------
# A call's audio, both ways
# ---------------------------------------------------------------------------

def test_one_twilio_chunk_is_one_agent_frame():
    audio = mulaw.TwilioAudio()
    frames = audio.from_twilio(bytes([mulaw.MULAW_SILENCE]) * 160)
    assert [len(f) for f in frames] == [FRAME_BYTES]


def test_one_agent_frame_is_one_twilio_chunk():
    audio = mulaw.TwilioAudio()
    chunks = audio.to_twilio(b"\x00" * FRAME_BYTES)
    assert [len(c) for c in chunks] == [mulaw.TWILIO_CHUNK_BYTES]


def test_silence_stays_silent_both_ways():
    audio = mulaw.TwilioAudio()
    for _ in range(5):
        for frame in audio.from_twilio(bytes([mulaw.MULAW_SILENCE]) * 160):
            assert frame == b"\x00" * FRAME_BYTES
        for chunk in audio.to_twilio(b"\x00" * FRAME_BYTES):
            assert chunk == bytes([mulaw.MULAW_SILENCE]) * 160


def test_uneven_chunks_still_make_whole_frames():
    """Twilio's chunks are usually 160 bytes, but nothing promises it; the
    agent must still only ever see exact 640-byte frames, with nothing lost."""
    audio = mulaw.TwilioAudio()
    stream = mulaw.encode(_tone(440, 8000, 1.0).tobytes())   # 8000 bytes
    frames: list[bytes] = []
    for size in (37, 159, 1, 300, 80, 160):
        while stream:
            piece, stream = stream[:size], stream[size:]
            frames += audio.from_twilio(piece)
            if len(stream) < 2000:
                break
    frames += audio.from_twilio(stream)
    assert all(len(f) == FRAME_BYTES for f in frames)
    assert len(frames) == 8000 // 160


def test_uneven_agent_frames_still_make_whole_chunks():
    audio = mulaw.TwilioAudio()
    pcm = _tone(440, 16000, 0.5).tobytes()                   # 16000 bytes
    chunks: list[bytes] = []
    for start in range(0, len(pcm), 1000):                   # not a multiple of 640
        chunks += audio.to_twilio(pcm[start:start + 1000])
    assert all(len(c) == mulaw.TWILIO_CHUNK_BYTES for c in chunks)
    assert len(chunks) == 25


def test_a_voice_tone_arrives_at_the_same_pitch_and_loudness():
    """Upsampling 8 → 16 kHz doubles the samples, not the pitch, and keeps
    the level; a mistake in either is a chipmunk or a whisper."""
    tone = _tone(440, 8000, 1.0)
    audio = mulaw.TwilioAudio()
    pcm = b"".join(audio.from_twilio(mulaw.encode(tone.tobytes())))
    samples = np.frombuffer(pcm, dtype="<i2")[1600:]          # past the filter's start-up
    spectrum = np.abs(np.fft.rfft(samples))
    peak_hz = np.argmax(spectrum) * 16000 / len(samples)
    assert abs(peak_hz - 440) < 5
    assert _level(samples.tobytes()) == pytest.approx(_level(tone.tobytes()), rel=0.08)


def test_the_agents_voice_reaches_the_caller_at_the_same_pitch_and_loudness():
    tone = _tone(1000, 16000, 1.0)
    audio = mulaw.TwilioAudio()
    chunks = []
    for start in range(0, len(tone) * 2, FRAME_BYTES):
        chunks += audio.to_twilio(tone.tobytes()[start:start + FRAME_BYTES])
    samples = np.frombuffer(mulaw.decode(b"".join(chunks)), dtype="<i2")[800:]
    spectrum = np.abs(np.fft.rfft(samples))
    peak_hz = np.argmax(spectrum) * 8000 / len(samples)
    assert abs(peak_hz - 1000) < 5
    assert _level(samples.tobytes()) == pytest.approx(_level(tone.tobytes()), rel=0.08)


def test_sound_above_what_a_phone_carries_does_not_fold_back_as_noise():
    """A 6 kHz hiss in the agent's audio cannot exist at 8 kHz. Unfiltered,
    halving the rate folds it to 2 kHz, right in the middle of speech."""
    hiss = _tone(6000, 16000, 1.0)
    audio = mulaw.TwilioAudio()
    chunks = []
    for start in range(0, len(hiss) * 2, FRAME_BYTES):
        chunks += audio.to_twilio(hiss.tobytes()[start:start + FRAME_BYTES])
    folded = np.frombuffer(mulaw.decode(b"".join(chunks)), dtype="<i2")[800:]
    assert _level(folded.tobytes()) < _level(hiss.tobytes()) * 0.05


def test_two_calls_do_not_share_filter_state():
    """Each call gets its own converters: a loud call must not bleed into
    the first frames of a quiet one."""
    loud, quiet = mulaw.TwilioAudio(), mulaw.TwilioAudio()
    loud.from_twilio(mulaw.encode(_tone(440, 8000, 0.1, amplitude=0.9).tobytes()))
    frames = quiet.from_twilio(bytes([mulaw.MULAW_SILENCE]) * 160)
    assert frames == [b"\x00" * FRAME_BYTES]
