
"""
The sockets that carry call audio.

Four ways in, one destination. Each of these does the same three things —
find the call, hand its audio to a `CallSession`, settle it when the socket
closes — and differ only in what the far end speaks:

* **Infobip media streaming** — raw 16 kHz PCM16, already the rate the session
  wants. The thinnest of them.
* **Twilio media streams** — JSON messages carrying 8 kHz μ-law, converted
  both ways by `services/agent/mulaw.py`.
* **An operator's browser** — the same PCM, from a person rather than a
  carrier, which takes the call over from the agent.
* **A test harness** — the same again, so the whole chain can be exercised
  without a phone. See `scripts/call_probe.py`.

WhatsApp calls do not appear here: they arrive as WebRTC and are terminated in
`services/agent/whatsapp_media.py`, which feeds the same session.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ...models.call import Call, CallStatus, Channel, Direction, RecordingState
from ...models.tenant import Tenant
from ...repositories import calls as call_repo
from ...repositories import tenants as tenant_repo
from ...security import playback
from ...services.agent import claims, live
from ...services.agent.audio_rate import FRAME_BYTES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])


async def _authorise(token: str, call_id: str) -> Tenant | None:
    """Who this socket speaks for.

    A websocket from a browser cannot set an Authorization header, and Infobip
    will not set one either — so the credential is in the URL. The same
    short-lived, single-call token the audio player uses: it names one call for
    one company and expires in minutes, so a leaked URL is worth nothing
    afterwards.
    """
    try:
        tenant_id = playback.verify(token, call_id)
    except Exception:  # noqa: BLE001 — a bad token is a closed socket, not a 500
        return None
    return await tenant_repo.get(tenant_id)


class _Outbound:
    """Frames queued from the pacer, written by the socket's own task.

    The pacer is synchronous and must not block; a websocket send is async.
    A queue between them means a slow network delays the socket, not the
    audio clock.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

    def __call__(self, frame: bytes) -> None:
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # The far end is not draining. Dropping the oldest keeps the
            # conversation current rather than playing out a backlog.
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(frame)


async def _pump(socket: WebSocket, out: _Outbound, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            frame = await asyncio.wait_for(out.queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        try:
            await socket.send_bytes(frame)
        except Exception:  # noqa: BLE001 — the far end went away
            stop.set()
            return


# ---------------------------------------------------------------------------
# The carrier
# ---------------------------------------------------------------------------


@router.websocket("/infobip")
async def infobip_media(socket: WebSocket):
    """The leg the carrier dials for call audio.

    One fixed address, because that is what Infobip's saved endpoint
    configuration holds — it has no way to carry a per-call URL or a token. The
    call this socket belongs to is therefore claimed from the reservation made
    just before the leg was dialled; see `services/agent/claims.py`.

    Frames in are 20 ms of 16 kHz PCM16 from the caller, and frames out must be
    the same shape **and binary only**. A text frame written back makes the
    carrier hang up — which is how a call that sounded perfectly fine ends two
    seconds in.
    """
    await socket.accept()
    reserved = claims.claim()
    if reserved is None:
        log.warning("a carrier audio socket arrived with no call waiting for it")
        await socket.close(code=4404)
        return

    call_id, tenant_id = reserved
    tenant = await tenant_repo.get(tenant_id)
    call = await call_repo.get_call(tenant_id, call_id) if tenant else None
    if tenant is None or call is None:
        log.error("call %s: claimed a socket but the call is gone", call_id)
        await socket.close(code=4404)
        return

    # The credentials this call is actually running on, so the agent is built
    # from the right number's settings.
    from ...services import lines

    tenant = await lines.tenant_for_call(tenant, call)

    out = _Outbound()
    stop = asyncio.Event()
    log.info("call %s: carrier audio socket attached", call_id)

    try:
        session = await live.start(tenant, call, out, keepalive=True)
    except live.AtCapacity as exc:
        # Closed deliberately rather than left to raise: an exception out of a
        # websocket handler drops the socket with no code, and the carrier
        # retries it. A close says "not now" once.
        log.warning("call %s: %s", call_id, exc)
        await socket.close(code=4503)
        await live.finish(tenant, call_id, str(exc))
        return
    pump = asyncio.create_task(_pump(socket, out, stop))
    reason = "the carrier closed the audio socket"
    frames = 0
    try:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                frames += 1
                session.feed_caller(data)
            # Text frames are the carrier's own control messages. They name the
            # websocket child leg rather than the call, so there is nothing in
            # them worth reading — and nothing may be written back.
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("call %s: carrier audio socket failed", call_id)
        reason = "the media socket failed"
    finally:
        stop.set()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        await live.finish(tenant, call_id, reason)
        with contextlib.suppress(Exception):
            await socket.close()
        log.info("call %s: carrier socket closed, %d frames from the caller", call_id, frames)


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------
#
# Twilio's media stream is JSON text frames, not binary: `connected`, then
# `start` (naming the stream and carrying the token we put in the TwiML), then
# `media` every 20 ms with base64 μ-law at 8 kHz, and `stop` at the end. We
# answer with `media` frames of our own, and `clear` to cut short whatever
# Twilio still has queued to play.
#
# Unlike Infobip, the call id is in the path and the token in the `start`
# message, so no reservation queue (`claims`) is needed: the socket says which
# call it is, and the token proves it.

#: How long to wait for Twilio's `start` before giving up on the socket.
TWILIO_START_TIMEOUT_SECONDS = 10.0

# Calls with a Twilio audio socket attached in this process. One per call: a
# second socket for the same call (a replayed token, or Twilio retrying while
# the first is still open) would put two caller streams into one agent.
_twilio_attached: set[str] = set()


class _TwilioOutbound(_Outbound):
    """The agent's frames, plus `clear` requests, in the order they happen.

    Both go through one queue so a `clear` can never overtake audio that was
    meant to come after it, or be overtaken by audio meant to be cleared.
    """

    CLEAR = b""  # never a real frame: the pacer only sends whole ones

    def clear(self) -> None:
        """Drop what has not left yet and tell Twilio to drop what has."""
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(self.CLEAR)


async def _twilio_pump(socket: WebSocket, out: _TwilioOutbound, stream_sid: str,
                       stop: asyncio.Event) -> None:
    """Agent frames to Twilio as μ-law `media` messages, and `clear` as asked."""
    import base64

    from ...services.agent.mulaw import TwilioAudio

    audio = TwilioAudio()
    while not stop.is_set():
        try:
            frame = await asyncio.wait_for(out.queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        try:
            if frame == out.CLEAR:
                await socket.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
                continue
            for chunk in audio.to_twilio(frame):
                await socket.send_text(json.dumps({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": base64.b64encode(chunk).decode("ascii")},
                }))
        except Exception:  # noqa: BLE001 — the far end went away
            stop.set()
            return


async def _twilio_start(socket: WebSocket) -> dict | None:
    """Read up to Twilio's `start` message and return its body.

    `connected` comes first and says nothing we need. Anything else before
    `start`, a socket that closes, or one that stays silent past the timeout
    is not a Twilio stream we can use.
    """
    async def read() -> dict | None:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                return None
            text = message.get("text")
            if not text:
                continue
            try:
                event = json.loads(text)
            except ValueError:
                return None
            if not isinstance(event, dict):
                return None
            if event.get("event") == "connected":
                continue
            if event.get("event") == "start" and isinstance(event.get("start"), dict):
                return event["start"]
            return None

    try:
        return await asyncio.wait_for(read(), timeout=TWILIO_START_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return None


async def _twilio_call(call_id: str, start: dict) -> tuple[Tenant, Call] | str:
    """The call this socket may carry, with the line's tenant, or why not.

    Every check is against something Twilio cannot be talked into: the token
    is ours and signed, names this call and its company, and expires in two
    minutes; the call must be on a Twilio line of that company, still live,
    and (once we know its CallSid) the very call Twilio says this stream is.
    """
    from ...models.number import NumberKind
    from ...repositories import numbers as number_repo
    from ...security import tickets
    from ...models.call import TERMINAL_STATUSES
    from ...services.twilio_client import MEDIA_AUDIENCE

    token = str((start.get("customParameters") or {}).get("token") or "")
    try:
        ticket = tickets.verify(token, MEDIA_AUDIENCE)
    except Exception:  # noqa: BLE001 — bad, expired or missing: all a refusal
        return "the token is missing, wrong or expired"
    if ticket.get("s") != call_id:
        return "the token names a different call"
    company_id = str(ticket.get("t") or "")
    company = await tenant_repo.get(company_id) if company_id else None
    call = await call_repo.get_call(company_id, call_id) if company else None
    if company is None or call is None:
        return "the call does not exist"
    if call.status in TERMINAL_STATUSES:
        return "the call has already ended"
    line = await number_repo.get(company_id, call.line_id) if call.line_id else None
    if line is None or line.kind is not NumberKind.TWILIO:
        return "the call is not on a Twilio number"
    call_sid = str(start.get("callSid") or "")
    if call.provider_call_id and call_sid != call.provider_call_id:
        return "the stream belongs to a different Twilio call"
    return number_repo.as_tenant(company, line), call


@router.websocket("/twilio/{call_id}")
async def twilio_media(socket: WebSocket, call_id: str):
    """The audio of one Twilio call, both ways.

    Twilio opens this when it reads the `<Connect><Stream>` TwiML from
    `webhooks.twilio_voice` or `twilio_outbound_answered`. Nothing is sent to
    it, and the agent's clock does not start, until `start` has been read and
    checked: before that there is no `streamSid` to address audio to, and no
    proof the socket is Twilio's.

    Closing this socket ends the call: there is no TwiML after `<Connect>`, so
    Twilio hangs up once the stream is gone. That is how a refusal here, or
    the agent ending the call, reaches the caller.
    """
    await socket.accept()
    start = await _twilio_start(socket)
    if start is None:
        log.warning("call %s: Twilio audio socket sent no usable start message", call_id)
        await socket.close(code=4400)
        return

    found = await _twilio_call(call_id, start)
    if isinstance(found, str):
        log.warning("call %s: Twilio audio socket refused: %s", call_id, found)
        await socket.close(code=4401)
        return
    tenant, call = found
    if call_id in _twilio_attached:
        log.warning("call %s: a second Twilio audio socket was refused", call_id)
        await socket.close(code=4409)
        return
    stream_sid = str(start.get("streamSid") or "")
    if not stream_sid:
        log.warning("call %s: Twilio start message has no streamSid", call_id)
        await socket.close(code=4400)
        return
    if not call.provider_call_id and start.get("callSid"):
        call.provider_call_id = str(start["callSid"])

    _twilio_attached.add(call_id)
    out = _TwilioOutbound()
    stop = asyncio.Event()
    log.info("call %s: Twilio audio socket attached", call_id)
    try:
        try:
            session = await live.start(tenant, call, out, keepalive=True, on_clear=out.clear)
        except live.AtCapacity as exc:
            log.warning("call %s: %s", call_id, exc)
            await socket.close(code=4503)
            await live.finish(tenant, call_id, str(exc))
            return
        await _twilio_stream(socket, session, out, stream_sid, stop, tenant, call_id)
    finally:
        _twilio_attached.discard(call_id)


async def _twilio_stream(socket: WebSocket, session, out: _TwilioOutbound, stream_sid: str,
                         stop: asyncio.Event, tenant: Tenant, call_id: str) -> None:
    """Move audio until either side stops, then settle the call."""
    import base64
    import binascii

    from ...services.agent.mulaw import TwilioAudio

    audio = TwilioAudio()
    pump = asyncio.create_task(_twilio_pump(socket, out, stream_sid, stop))
    reason = "Twilio closed the audio socket"
    frames = 0
    try:
        while not stop.is_set():
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if not text:
                continue
            try:
                event = json.loads(text)
            except ValueError:
                continue
            kind = event.get("event") if isinstance(event, dict) else None
            if kind == "media":
                media = event.get("media") or {}
                # Only the caller's side; Twilio does not echo ours back on a
                # bidirectional stream, but a track label costs nothing to check.
                if media.get("track", "inbound") not in {"inbound", "inbound_track"}:
                    continue
                try:
                    payload = base64.b64decode(media.get("payload") or "", validate=True)
                except (binascii.Error, ValueError):
                    continue
                for frame in audio.from_twilio(payload):
                    frames += 1
                    session.feed_caller(frame)
            elif kind == "stop":
                # Either side may have hung up; the status callback says which.
                reason = "the call ended"
                break
            # `mark` and `dtmf` need nothing from us yet.
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("call %s: Twilio audio socket failed", call_id)
        reason = "the media socket failed"
    finally:
        stop.set()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        await live.finish(tenant, call_id, reason)
        with contextlib.suppress(Exception):
            await socket.close()
        log.info("call %s: Twilio socket closed, %d frames from the caller", call_id, frames)


# ---------------------------------------------------------------------------
# A person taking the call
# ---------------------------------------------------------------------------


@router.websocket("/operator/{call_id}")
async def operator_media(socket: WebSocket, call_id: str, token: str = Query(default="")):
    """A human takes over a call that is already running.

    The agent stops the moment this connects — mid-sentence if necessary,
    because an agent finishing its thought over the person who just joined is
    worse than a clipped word.

    Audio is 16 kHz PCM16 in both directions, in whatever sized chunks the
    browser produces. Transcript lines are sent as JSON text frames so the
    operator can see what was said before they arrived.
    """
    tenant = await _authorise(token, call_id)
    if tenant is None:
        await socket.close(code=4401)
        return
    session = live.get(call_id)
    if session is None:
        await socket.close(code=4404)
        return

    await socket.accept()
    out = _Outbound()
    stop = asyncio.Event()
    pump = asyncio.create_task(_pump(socket, out, stop))

    await session.hand_over(out)
    with contextlib.suppress(Exception):
        await socket.send_text(json.dumps({
            "type": "handover",
            "transcript": session.transcript_text(),
        }))
    log.info("call %s: an operator joined", call_id)

    try:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                session.feed_operator(data)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("call %s: operator socket failed", call_id)
    finally:
        stop.set()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        if session.human:
            # A dialer call is the employee's own: hanging up the browser
            # hangs up the call.
            await _hang_up_human(tenant, call_id)
            log.info("call %s: the employee hung up", call_id)
        else:
            # The operator leaving does not end the call. The caller is still
            # there, so the agent picks it back up rather than the line going dead.
            with contextlib.suppress(Exception):
                await session.hand_back()
            log.info("call %s: the operator left, agent resumed", call_id)


async def _hang_up_human(tenant: Tenant, call_id: str) -> None:
    from ...services import carriers, lines
    from ...services.agent import whatsapp_media
    from ...services.whatsapp import WhatsApp

    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        return
    effective = await lines.tenant_for_call(tenant, call)
    await live.finish(effective, call_id, "the employee hung up")
    with contextlib.suppress(Exception):
        if call.provider_call_id and call.channel is Channel.PHONE:
            await carriers.hang_up(effective, call.provider_call_id)
        elif call.provider_call_id and call.channel is Channel.WHATSAPP_CALL:
            await WhatsApp(effective).terminate_call(call.provider_call_id)
            await whatsapp_media.drop(call_id)


# ---------------------------------------------------------------------------
# Testing without a phone
# ---------------------------------------------------------------------------


@router.websocket("/test/{call_id}")
async def test_media(socket: WebSocket, call_id: str, token: str = Query(default="")):
    """A caller that is not a carrier.

    The same contract as the Infobip socket — 16 kHz PCM16 both ways — so the
    whole chain can be driven from a script with a WAV file. This is how the
    agent, the pacing, the recording and the handover are verified before any
    real call is placed.
    """
    tenant = await _authorise(token, call_id)
    if tenant is None:
        await socket.close(code=4401)
        return
    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        await socket.close(code=4404)
        return

    await socket.accept()
    out = _Outbound()
    stop = asyncio.Event()
    # No keepalive: a test harness is not a carrier and does not drop the leg
    # over silence, so filling the gaps would only pad the recording.
    session = await live.start(tenant, call, out, keepalive=False)
    pump = asyncio.create_task(_pump(socket, out, stop))
    try:
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes"):
                session.feed_caller(message["bytes"])
            elif message.get("text") == "hangup":
                break
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        await live.finish(tenant, call_id, "the test harness hung up")
        with contextlib.suppress(Exception):
            await socket.close()


# ---------------------------------------------------------------------------
# Starting a call that a socket will then attach to
# ---------------------------------------------------------------------------


@router.post("/sessions")
async def open_session(
    tenant_id: str = Query(alias="companyId"),
    channel: Channel = Channel.PHONE,
    counterparty: str = "",
    direction: Direction = Direction.INBOUND,
) -> dict:
    """Create a call row and a token a socket can connect with.

    Deliberately not authenticated by a company key: the carrier calls this
    through its own configuration and has no key. It creates nothing but an
    empty call row, and the token it returns is scoped to that one call.
    """
    tenant = await tenant_repo.get(tenant_id)
    if tenant is None:
        return {"error": "unknown company"}
    call = Call(
        tenantId=tenant.phone_number_id,
        channel=channel,
        direction=direction,
        status=CallStatus.RINGING,
        counterparty=counterparty,
        startedAt=time.time(),
        recordingState=RecordingState.PENDING if tenant.record_calls else RecordingState.NONE,
    )
    await call_repo.save_call(call)
    return {
        "callId": call.id,
        "token": playback.mint(tenant.phone_number_id, call.id, ttl=3600),
        "frameBytes": FRAME_BYTES,
        "sampleRate": 16000,
    }
