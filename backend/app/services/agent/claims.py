"""
Which call a newly arrived carrier audio socket belongs to.

Infobip dials a leg into our websocket and tells us nothing about which call it
is for: no path parameter, no query string, and the text frames it sends name
the websocket child leg rather than the call. So the call is reserved *before*
the leg is dialled, and the next socket to arrive claims the oldest reservation.

That is a queue, with everything a queue implies: it is correct exactly as long
as sockets arrive in the order the legs were dialled. In practice they do,
because the leg is dialled from the webhook handler for one specific call and
the carrier connects within a second or so. Two calls arriving in the same
instant on the same number could in principle swap — the window is the time
between two `reserve` calls, and the cost is two callers hearing each other's
agent. It is the same trade the working implementations this was taken from
make, and the alternative (a static URL carrying a per-call secret) is not
something the carrier's saved-endpoint model allows.

Reservations expire, so a leg that never connects does not hand its call to
whoever rings next.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# How long a reservation is worth honouring. Comfortably longer than the
# carrier's connect timeout, short enough that a failed leg is forgotten before
# the next caller could claim it.
TTL_SECONDS = 90.0

_waiting: list[tuple[float, str, str]] = []  # (reserved_at, call_id, tenant_id)


def _sweep(now: float) -> None:
    stale = [w for w in _waiting if now - w[0] >= TTL_SECONDS]
    for entry in stale:
        log.warning("call %s: the carrier never connected its audio leg", entry[1])
    _waiting[:] = [w for w in _waiting if now - w[0] < TTL_SECONDS]


def reserve(call_id: str, tenant_id: str) -> None:
    """Say a socket is coming for this call.

    Called *before* the leg is dialled, not after: the carrier can open the
    socket while the dial request is still in flight, and a socket with nothing
    waiting for it is refused.
    """
    now = time.monotonic()
    _sweep(now)
    _waiting.append((now, call_id, tenant_id))
    log.info("call %s: waiting for the carrier's audio socket", call_id)


def claim() -> tuple[str, str] | None:
    """The call and company a socket that just arrived belongs to."""
    now = time.monotonic()
    _sweep(now)
    if not _waiting:
        return None
    _, call_id, tenant_id = _waiting.pop(0)
    return call_id, tenant_id


def cancel(call_id: str) -> None:
    """Forget a reservation whose call died before the leg connected."""
    before = len(_waiting)
    _waiting[:] = [w for w in _waiting if w[1] != call_id]
    if len(_waiting) != before:
        log.info("call %s: audio reservation cancelled", call_id)


def pending() -> int:
    _sweep(time.monotonic())
    return len(_waiting)
