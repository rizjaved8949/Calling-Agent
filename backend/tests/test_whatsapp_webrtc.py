"""
The WhatsApp leg, negotiated against a real WebRTC peer.

Meta is not available in a test, but WebRTC is: a second aiortc peer plays
Meta's part, sends an offer, and receives whatever our bridge sends back.
That exercises the parts that are easy to get wrong and impossible to check by
reading — SDP negotiation, ICE, DTLS, and whether audio actually flows in both
directions — without a phone or a Meta app.

What it cannot prove is that *Meta* accepts our answer. Meta's SDP validator
has its own opinions, which is why the payload-type profile below is pinned:
that one took the earlier service several rejections to find.
"""
from __future__ import annotations

import asyncio
import contextlib
from fractions import Fraction

import pytest

from app.models.tenant import Tenant

aiortc = pytest.importorskip("aiortc", reason="aiortc is not installed")
av = pytest.importorskip("av", reason="PyAV is not installed")

from app.services.agent import whatsapp_media as wm  # noqa: E402


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t1", wabaId="w", accessToken="tok")


# ---------------------------------------------------------------------------
# The codec profile Meta expects
# ---------------------------------------------------------------------------


def test_opus_is_advertised_the_way_meta_expects():
    """Payload type 111, not aiortc's default 96, and with the fmtp line.

    Set inside aiortc rather than by rewriting the SDP string afterwards, so
    the RTP sender and the negotiated answer cannot disagree.
    """
    wm._apply_meta_audio_profile()
    from aiortc.codecs import CODECS

    opus = next(c for c in CODECS["audio"] if "opus" in str(c.mimeType).lower())
    assert opus.payloadType == wm.META_OPUS_PAYLOAD_TYPE == 111
    assert opus.parameters.get("minptime") == 10
    assert opus.parameters.get("useinbandfec") == 1


def test_applying_the_profile_twice_is_harmless():
    wm._apply_meta_audio_profile()
    wm._apply_meta_audio_profile()
    from aiortc.codecs import CODECS

    opus = [c for c in CODECS["audio"] if "opus" in str(c.mimeType).lower()]
    assert len(opus) == 1, "the codec list was duplicated"


# ---------------------------------------------------------------------------
# The outgoing track
# ---------------------------------------------------------------------------


async def test_the_track_never_stops_producing_frames():
    """A track that stops stalls the RTP clock, and the far end calls that a
    broken stream rather than a pause in the conversation."""
    track = wm.AgentAudioTrack()
    first = await track.recv()                 # nothing queued at all
    assert first.samples == track.samples_per_frame
    assert bytes(first.planes[0]) == b"\x00" * track.bytes_per_frame


async def test_queued_audio_comes_back_out():
    track = wm.AgentAudioTrack()
    track.push(b"\x11\x22" * (track.bytes_per_frame // 2))
    frame = await track.recv()
    assert bytes(frame.planes[0])[:4] == b"\x11\x22\x11\x22"


async def test_timestamps_advance_by_one_frame():
    """A pts that repeats or jumps is heard as a stutter."""
    track = wm.AgentAudioTrack()
    a = await track.recv()
    b = await track.recv()
    assert b.pts - a.pts == track.samples_per_frame
    assert a.time_base == Fraction(1, track.sample_rate)


# ---------------------------------------------------------------------------
# A whole call against a real peer
# ---------------------------------------------------------------------------


class Tone:
    """A caller, as an audio track. Stands in for the person on the phone."""

    def __new__(cls, *_a, **_k):
        from aiortc.mediastreams import AudioStreamTrack

        class _Tone(AudioStreamTrack):
            kind = "audio"

            def __init__(self) -> None:
                super().__init__()
                self.pts = 0
                self.rate = 48_000
                self.samples = self.rate // 50

            async def recv(self):
                import numpy as np
                from av import AudioFrame

                await asyncio.sleep(0.02)
                t = (np.arange(self.samples) + self.pts) / self.rate
                wave = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
                frame = AudioFrame(format="s16", layout="mono", samples=self.samples)
                frame.planes[0].update(wave.tobytes())
                frame.sample_rate = self.rate
                frame.pts = self.pts
                frame.time_base = Fraction(1, self.rate)
                self.pts += self.samples
                return frame

        return _Tone()


class Sink:
    """Stands in for the `CallSession`: records what the bridge hands it."""

    def __init__(self) -> None:
        self.received = bytearray()

    def feed_caller(self, pcm: bytes) -> None:
        self.received.extend(pcm)


async def test_a_call_connects_and_audio_flows_both_ways():
    from aiortc import RTCPeerConnection, RTCSessionDescription

    bridge = wm.WhatsAppBridge("call-1", _tenant())
    sink = Sink()
    bridge.attach(sink)

    meta = RTCPeerConnection()
    meta.addTrack(Tone())
    heard_from_agent = bytearray()

    @meta.on("track")
    def _on_track(track):
        async def drain():
            with contextlib.suppress(Exception):
                while True:
                    frame = await track.recv()
                    heard_from_agent.extend(bytes(frame.planes[0]))
        asyncio.create_task(drain())

    try:
        offer = await meta.createOffer()
        await meta.setLocalDescription(offer)

        answer_sdp = await bridge.answer(meta.localDescription.sdp)
        assert "m=audio" in answer_sdp
        await meta.setRemoteDescription(
            RTCSessionDescription(sdp=answer_sdp, type="answer")
        )

        assert await bridge.wait_connected(timeout=30), "WebRTC never connected"

        # The agent speaks while the caller's tone plays.
        for _ in range(60):
            bridge.send_to_caller(b"\x20\x30" * 160)   # 20 ms at 16 kHz
            await asyncio.sleep(0.02)
        await asyncio.sleep(1.0)

        assert sink.received, "no caller audio reached the session"
        assert heard_from_agent, "the agent's audio never reached the far end"
    finally:
        await bridge.close()
        with contextlib.suppress(Exception):
            await meta.close()


async def test_caller_audio_is_resampled_to_the_session_rate():
    """Meta speaks 48 kHz; the session speaks 16 kHz. A third of the samples."""
    from app.services.agent.audio_rate import RateConverter

    at_48k = b"\x00\x10" * 4800          # 100 ms
    out = RateConverter(1, 3).process(at_48k)
    assert abs(len(out) // 2 - 1600) < 60, "not a third of the samples"


async def test_closing_twice_is_safe():
    bridge = wm.WhatsAppBridge("call-2", _tenant())
    await bridge.close()
    await bridge.close()
    assert bridge.closed
