"""
Twilio's phone audio, turned into what the agent hears and back again.

Twilio's media stream carries the call as G.711 μ-law at 8 kHz: one byte per
sample, 160 bytes per 20 ms. The agent (`session.CallSession`) speaks 16 kHz
PCM16 in 640-byte frames, the same as an Infobip line. So each direction is a
codec step and a rate step:

    Twilio → agent   μ-law 8 kHz  → PCM16 8 kHz  → ×2 → PCM16 16 kHz, 640-byte frames
    agent → Twilio   PCM16 16 kHz → ÷2 → PCM16 8 kHz → μ-law 8 kHz, 160-byte chunks

μ-law is done here with NumPy rather than `audioop`, which did this in one
call but was removed from Python 3.13. G.711 is a fixed table, so both
directions are lookups into a table built once at import: 64 KiB to encode
every possible sample, 256 entries to decode every possible byte.

The rate steps use `audio_rate.RateConverter`, one per direction *per call*:
it carries filter state from frame to frame, and two calls sharing one would
leak a few samples of each other's audio into every frame.
"""
from __future__ import annotations

import numpy as np

from .audio_rate import FRAME_BYTES, RateConverter

#: The rate Twilio's media stream runs at.
TWILIO_RATE = 8_000
#: 20 ms of 8 kHz μ-law: one byte per sample. What Twilio sends in each
#: media message, and what we send back.
TWILIO_CHUNK_BYTES = TWILIO_RATE * 20 // 1000  # 160
#: The byte for silence. Not 0x00, which is the loudest negative sample.
MULAW_SILENCE = 0xFF

# G.711's constants (ITU-T G.711, as in Sun's reference g711.c).
_BIAS = 0x84
_CLIP14 = 8159  # the loudest magnitude, in the 14 bits the encoder works in


def _build_decode_table() -> np.ndarray:
    codes = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = codes & 0x80
    exponent = (codes >> 4) & 0x07
    mantissa = codes & 0x0F
    magnitude = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return np.where(sign != 0, -magnitude, magnitude).astype(np.int16)


def _build_encode_table() -> np.ndarray:
    """Sun's reference `linear2ulaw`, for every int16 at once.

    Its exact order matters at the last bit: the sample is cut to 14 bits by
    an arithmetic shift *before* its sign is taken, so a negative sample
    rounds away from zero. Doing it the other way round is a common variant
    that differs on about one negative sample in four, by under a step.
    Inaudible, but the reference is what `audioop` did and what the tests
    hold this to.
    """
    # Indexed by the sample's bits read as unsigned, so a whole int16 array
    # encodes with one `view` and one lookup.
    samples = np.arange(65536, dtype=np.uint16).view(np.int16).astype(np.int32) >> 2
    sign = np.where(samples < 0, 0x80, 0)
    magnitude = np.minimum(np.abs(samples), _CLIP14) + (_BIAS >> 2)
    # The reference's top segment ends at 0x1FFF; the one value above it
    # (the clip plus the bias) is encoded as the loudest code, which is what
    # capping it here produces.
    magnitude = np.minimum(magnitude, 0x1FFF)
    # The segment: where the top bit sits above bit 5. magnitude >> 5 is an
    # integer from 1 to 255, so this is 0..7.
    exponent = np.array([int(v).bit_length() - 1 for v in range(256)],
                        dtype=np.int32)[magnitude >> 5]
    mantissa = (magnitude >> (exponent + 1)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8)


_DECODE = _build_decode_table()
_ENCODE = _build_encode_table()


def decode(mulaw: bytes) -> bytes:
    """μ-law bytes to PCM16 (little-endian), one sample per byte."""
    if not mulaw:
        return b""
    return _DECODE[np.frombuffer(mulaw, dtype=np.uint8)].astype("<i2").tobytes()


def encode(pcm16: bytes) -> bytes:
    """PCM16 (little-endian) to μ-law, one byte per sample.

    A trailing odd byte, half a sample, is dropped rather than guessed at;
    `TwilioAudio` never hands one over.
    """
    usable = len(pcm16) - len(pcm16) % 2
    if not usable:
        return b""
    samples = np.frombuffer(pcm16[:usable], dtype="<i2").view(np.uint16)
    return _ENCODE[samples].tobytes()


class TwilioAudio:
    """One Twilio call's audio, in both directions.

    Buffers on both sides, because the two ends count differently. The agent
    must be fed exactly 640-byte frames: its recorder, its model input and
    operator passthrough all assume 20 ms. Twilio's chunks are usually 160
    bytes, which happens to become exactly 640, but nothing promises that,
    and a 159-byte chunk turned straight into a 636-byte frame would make the
    agent's clock drift a sample at a time. So what does not fill a frame
    waits for the next chunk. The same goes the other way, so Twilio always
    receives whole 20 ms chunks.
    """

    def __init__(self) -> None:
        self._up = RateConverter(2, 1)      # 8 kHz → 16 kHz, from the caller
        self._down = RateConverter(1, 2)    # 16 kHz → 8 kHz, to the caller
        self._to_agent = bytearray()
        self._to_twilio = bytearray()

    def from_twilio(self, mulaw: bytes) -> list[bytes]:
        """A media payload from Twilio, as whole 640-byte frames for the agent.

        Returns nothing until a frame's worth has arrived.
        """
        if mulaw:
            self._to_agent += self._up.process(decode(mulaw))
        return _whole(self._to_agent, FRAME_BYTES)

    def to_twilio(self, pcm16: bytes) -> list[bytes]:
        """A frame from the agent, as whole 160-byte μ-law chunks for Twilio."""
        if pcm16:
            self._to_twilio += encode(self._down.process(pcm16))
        return _whole(self._to_twilio, TWILIO_CHUNK_BYTES)


def _whole(buffer: bytearray, size: int) -> list[bytes]:
    """Take every complete `size`-byte piece off the front of `buffer`."""
    count = len(buffer) // size
    pieces = [bytes(buffer[i * size:(i + 1) * size]) for i in range(count)]
    del buffer[:count * size]
    return pieces
