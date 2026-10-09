
"""
The sockets that carry call audio.

Three ways in, one destination. Each of these does the same three things —
find the call, hand its audio to a `CallSession`, settle it when the socket
closes — and differ only in what the far end speaks:

* **Infobip media streaming** — raw 16 kHz PCM16, already the rate the session
  wants. The thinnest of the three.
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

    session = await live.start(tenant, call, out, keepalive=True)
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
    from ...services import lines
    from ...services.agent import whatsapp_media
    from ...services.telephony import Infobip
    from ...services.whatsapp import WhatsApp

    call = await call_repo.get_call(tenant.phone_number_id, call_id)
    if call is None:
        return
    effective = await lines.tenant_for_call(tenant, call)
    await live.finish(effective, call_id, "the employee hung up")
    with contextlib.suppress(Exception):
        if call.provider_call_id and call.channel is Channel.PHONE:
            await Infobip(effective).hangup(call.provider_call_id)
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
