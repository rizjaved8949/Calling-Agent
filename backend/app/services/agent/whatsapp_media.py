"""
Terminating a WhatsApp call's WebRTC leg.

Meta does not hand a business a SIP trunk. It opens a WebRTC connection and
sends an SDP offer on the webhook, so answering a WhatsApp call means being a
WebRTC endpoint: answer the offer, complete ICE and DTLS, then decode Opus in
and encode Opus out for as long as the caller is there.

That is the whole difference from a SIM call, where the carrier hands us raw
PCM over a websocket. Either way the audio ends up in the same `CallSession`.

Ported from Whatsapp-Calling-and-Messaging-Automation, which proved this
against Meta on Render with STUN alone — no TURN server. The payload-type
detail below is the part that took that service several rejections to learn.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from fractions import Fraction

from ...config import settings
from ...models.tenant import Tenant
from .audio_rate import PHONE_RATE, RateConverter

log = logging.getLogger(__name__)

# What Meta's WhatsApp Calls profile uses. aiortc defaults Opus to dynamic
# payload type 96; Meta's published examples use 111. Changing it inside
# aiortc rather than rewriting the SDP string afterwards keeps the RTP sender
# and the later negotiation agreeing with each other.
META_OPUS_PAYLOAD_TYPE = 111
# WebRTC Opus is 48 kHz stereo on the wire.
WIRE_RATE = 48_000


def available() -> bool:
    try:
        import aiortc  # noqa: F401
        import av  # noqa: F401
    except ImportError:
        return False
    return True


_profile_applied = False


def _apply_meta_audio_profile() -> None:
    """Advertise Opus the way Meta expects. Idempotent."""
    global _profile_applied
    if _profile_applied:
        return
    from aiortc.rtcrtpsender import RTCRtpSender  # noqa: F401
    from aiortc.codecs import CODECS

    for codec in CODECS.get("audio", []):
        if str(getattr(codec, "mimeType", "")).lower() == "audio/opus":
            codec.payloadType = META_OPUS_PAYLOAD_TYPE
            params = dict(getattr(codec, "parameters", {}) or {})
            params.setdefault("minptime", 10)
            params.setdefault("useinbandfec", 1)
            codec.parameters = params
            break
    _profile_applied = True


def _ice_servers():
    """STUN only, which is what the earlier service ran on Render.

    A TURN relay is the fallback if media never connects behind strict NAT;
    the settings exist so it can be added without a code change, and were
    never needed in practice.
    """
    from aiortc import RTCIceServer

    servers = [RTCIceServer(urls=settings.whatsapp_stun_url or "stun:stun.l.google.com:19302")]
    if settings.whatsapp_turn_url.strip():
        servers.append(
            RTCIceServer(
                urls=settings.whatsapp_turn_url.strip(),
                username=settings.whatsapp_turn_username or None,
                credential=settings.whatsapp_turn_credential or None,
            )
        )
    return servers


_track_class = None


def _agent_track_class():
    """The outgoing track type, defined on first use.

    Built inside a function so that importing this module does not require
    aiortc — the rest of the service runs fine without it, and only a WhatsApp
    call needs it.
    """
    global _track_class
    if _track_class is not None:
        return _track_class

    from aiortc.mediastreams import AudioStreamTrack
    from av import AudioFrame

    class AgentAudioTrack(AudioStreamTrack):
        """The track Meta pulls the agent's voice from.

        aiortc asks for a frame every 20 ms and will not wait: whatever is in
        hand is what goes out. So the queue is drained into fixed frames and
        padded with silence when the agent is not speaking — a WebRTC audio
        track that stops producing frames stalls the RTP clock, and the far end
        treats that as a broken stream rather than a pause in the conversation.
        """

        kind = "audio"

        def __init__(self, sample_rate: int = WIRE_RATE) -> None:
            super().__init__()
            self.sample_rate = sample_rate
            self.samples_per_frame = max(1, sample_rate // 50)   # 20 ms
            self.bytes_per_frame = self.samples_per_frame * 2
            self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
            self._buffer = bytearray()
            self._pts = 0
            self._started = time.monotonic()

        def push(self, pcm: bytes) -> None:
            try:
                self._queue.put_nowait(pcm)
            except asyncio.QueueFull:
                # Keep the newest audio rather than a backlog: the far end is
                # behind, and playing out stale speech only widens the gap.
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    self._queue.put_nowait(pcm)

        def clear(self) -> None:
            self._buffer.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

        async def recv(self):
            # Paced against our own clock rather than however often aiortc asks.
            target = self._started + (self._pts / self.sample_rate)
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

            while len(self._buffer) < self.bytes_per_frame:
                try:
                    self._buffer.extend(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if len(self._buffer) >= self.bytes_per_frame:
                raw = bytes(self._buffer[: self.bytes_per_frame])
                del self._buffer[: self.bytes_per_frame]
            else:
                raw = bytes(self._buffer).ljust(self.bytes_per_frame, b"\x00")
                self._buffer.clear()

            frame = AudioFrame(format="s16", layout="mono",
                               samples=self.samples_per_frame)
            frame.planes[0].update(raw)
            frame.sample_rate = self.sample_rate
            frame.pts = self._pts
            frame.time_base = Fraction(1, self.sample_rate)
            self._pts += self.samples_per_frame
            return frame

    _track_class = AgentAudioTrack
    return _track_class


def AgentAudioTrack(sample_rate: int = WIRE_RATE):
    """An outgoing track. A function, not a class, because of the lazy import."""
    return _agent_track_class()(sample_rate)


def _resampled(resampler, frame) -> list:
    """PyAV's output, as a list.

    `resample()` returns a list on PyAV 9 and later and a single frame (or
    None) before that. Both shapes appear in the wild depending on which wheel
    the host resolved, and treating a bare frame as iterable silently drops
    every caller.
    """
    produced = resampler.resample(frame)
    if produced is None:
        return []
    return list(produced) if isinstance(produced, (list, tuple)) else [produced]


class WhatsAppBridge:
    """One WhatsApp call, terminated here and joined to a `CallSession`."""

    def __init__(self, call_id: str, tenant: Tenant) -> None:
        if not available():
            raise RuntimeError(
                "aiortc is not installed, so WhatsApp calls cannot be answered."
            )
        _apply_meta_audio_profile()
        from aiortc import RTCConfiguration, RTCPeerConnection

        self.call_id = call_id
        self.tenant = tenant
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=_ice_servers()))
        self.outgoing = AgentAudioTrack(WIRE_RATE)
        self.pc.addTrack(self.outgoing)
        self.connected = asyncio.Event()
        self.closed = False

        # Outgoing only. The inbound direction is resampled by PyAV, which is
        # the one thing that knows what Meta actually negotiated — see _drain.
        self._to_meta = RateConverter(3, 1)       # 16k -> 48k
        self._session = None
        self._consume: asyncio.Task | None = None

        @self.pc.on("track")
        def _on_track(track):
            if getattr(track, "kind", "") != "audio":
                return
            log.info("call %s: Meta audio track arrived", self.call_id)
            self._consume = asyncio.create_task(self._drain(track))

        @self.pc.on("connectionstatechange")
        async def _on_state():
            state = self.pc.connectionState
            log.info("call %s: WebRTC %s", self.call_id, state)
            if state == "connected":
                self.connected.set()
            elif state in {"failed", "closed"}:
                # Unblock anyone waiting rather than leaving them on a timeout.
                self.connected.set()

    # ---- signalling -------------------------------------------------------

    async def answer(self, sdp_offer: str) -> str:
        """Take Meta's offer and produce the answer to send back."""
        from aiortc import RTCSessionDescription

        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp_offer, type="offer")
        )
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        return self.pc.localDescription.sdp

    async def offer(self) -> str:
        """Our own offer, for a call the business places.

        Waits for ICE gathering before shaping the result: an offer sent while
        candidates are still arriving has none in it, and Meta accepts it and
        then nothing connects — a call that rings and carries silence, which is
        worse than one that is refused.
        """
        from . import meta_sdp

        await self.pc.setLocalDescription(await self.pc.createOffer())
        await self._wait_for_candidates()

        shaped = meta_sdp.prepare_outbound(self.pc.localDescription.sdp)
        checks = meta_sdp.validate_outbound(shaped)
        if not checks["ok"]:
            # Meta answers a malformed offer with "Parameter value is not
            # valid" and nothing else, so the diagnosis has to be ours.
            log.warning("call %s: outbound SDP looks wrong — %s",
                        self.call_id, meta_sdp.describe(checks))
        return shaped

    async def _wait_for_candidates(self, timeout: float = 8.0) -> None:
        """Give ICE a moment to gather, but do not wait on it forever.

        Where outbound UDP is slow or blocked, gathering never completes and
        waiting on it is a call that is never placed. Host candidates alone are
        enough to connect on most networks, so this proceeds with what it has.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while self.pc.iceGatheringState != "complete":
            if asyncio.get_running_loop().time() > deadline:
                log.info("call %s: ICE still gathering, sending what we have",
                         self.call_id)
                return
            await asyncio.sleep(0.2)

    async def accept_answer(self, sdp_answer: str) -> None:
        from aiortc import RTCSessionDescription

        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp_answer, type="answer")
        )

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.connected.wait(), timeout=timeout)
        return self.pc.connectionState == "connected"

    # ---- audio ------------------------------------------------------------

    def attach(self, session) -> None:
        """Join this leg to the call session, in both directions."""
        self._session = session

    def send_to_caller(self, pcm16: bytes) -> None:
        """A frame from the session, at 16 kHz, on its way to Meta."""
        if self.closed or not pcm16:
            return
        self.outgoing.push(self._to_meta.process(pcm16))

    async def _drain(self, track) -> None:
        """Caller audio from Meta, converted to what the session listens to.

        PyAV does the conversion rather than our own resampler, because this
        frame is whatever Opus produced and only PyAV knows its shape. Opus
        decodes to 48 kHz **stereo**, and `to_ndarray()` on a packed format
        returns one interleaved row — so reading it as mono and dividing the
        rate by three yields left and right samples alternating at twice the
        speed. That is not quiet or distorted, it is unrecognisable as speech:
        the agent greets the caller, hears noise for the rest of the call, and
        never speaks again.

        Resampling to mono at the session's own rate hands that to PyAV, which
        also copes with Meta negotiating anything other than 48 kHz without a
        branch of our own guessing at the ratio.
        """
        from av.audio.resampler import AudioResampler

        resampler = AudioResampler(format="s16", layout="mono", rate=PHONE_RATE)
        frames = 0
        while not self.closed:
            try:
                frame = await track.recv()
            except Exception:  # noqa: BLE001 — the track ended with the call
                break
            if self._session is None:
                continue
            try:
                for converted in _resampled(resampler, frame):
                    pcm = converted.to_ndarray().tobytes()
                    if pcm:
                        frames += 1
                        self._session.feed_caller(pcm)
            except Exception:  # noqa: BLE001 — one bad frame is not a dropped call
                log.debug("call %s: could not read a frame", self.call_id, exc_info=True)
        log.info("call %s: %d frames of caller audio reached the agent", self.call_id, frames)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._consume:
            self._consume.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._consume
        with contextlib.suppress(Exception):
            await self.pc.close()
        log.info("call %s: WebRTC closed", self.call_id)


# Live bridges, so a terminate webhook can find the one it belongs to.
_bridges: dict[str, WhatsAppBridge] = {}


def register(call_id: str, bridge: WhatsAppBridge) -> None:
    _bridges[call_id] = bridge


def get(call_id: str) -> WhatsAppBridge | None:
    return _bridges.get(call_id)


async def drop(call_id: str) -> None:
    bridge = _bridges.pop(call_id, None)
    if bridge is not None:
        await bridge.close()
