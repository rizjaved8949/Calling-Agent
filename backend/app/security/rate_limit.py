"""
Request throttling, kept in memory.

There is one Render instance and no Redis anywhere in this stack, so an
in-memory counter is the honest choice rather than a dependency bought for
headroom nobody needs yet. If this ever runs on more than one instance, each
gets its own count, which loosens the limit by the instance count rather than
breaking it — a safe direction to be wrong in.

Two layers:

* A blanket limiter, applied to every request, keyed by whoever is asking
  (a tenant's API key, the admin key, or the caller's IP for anyone with
  neither). This is the backstop against a runaway script or a bug in a
  client, not a precise quota.
* A tighter limiter on specific routes that spend real money the moment they
  are called — placing a call, starting a campaign, sending a WhatsApp
  message — because those deserve a lower ceiling than "browsing your own
  call list" does.

Both read the same identity so a customer who paces their API calls but
leaves a runaway campaign script in a loop is still caught by the route it
actually hits.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from ..errors import AppError


class TooManyRequests(AppError):
    def __init__(self, message: str = "Too many requests. Slow down and try again shortly."):
        super().__init__(429, message, code="rate_limited")


class Window:
    """How many hits a key has made in the last `seconds`, kept in a deque.

    A deque per key rather than one shared structure: pruning one busy key
    must not pay for every other key's history, and a key that goes quiet is
    cheap to forget (its deque just stops growing — not pruned eagerly, but
    bounded by `limit` once it will not take new hits).
    """

    __slots__ = ("limit", "seconds", "_hits")

    def __init__(self, limit: int, seconds: float):
        self.limit = limit
        self.seconds = seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - self.seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            raise TooManyRequests()
        bucket.append(now)


def identity(request: Request) -> str:
    """Who to count against: the credential presented, else the caller's IP.

    Matches `api/deps.py`'s own reading of these headers, because the whole
    point is to count the same thing a request will be billed against — a
    tenant's own key, not the raw IP a shared NAT might put it behind.
    """
    auth = request.headers.get("authorization", "")
    if auth:
        parts = auth.split(None, 1)
        token = parts[1].strip() if len(parts) == 2 else auth.strip()
        if token:
            return f"key:{token}"
    admin = request.headers.get("x-admin-key", "")
    if admin:
        return f"admin:{admin}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


# Blanket limiter: generous, because it exists to catch a runaway loop, not
# to pace normal use of the dashboard.
_blanket = Window(limit=300, seconds=60.0)

# One shared tighter limiter for anything that places a call, starts a
# campaign, or sends a message — the actions with a real per-call cost and a
# carrier on the other end who can suspend an account for abuse.
_expensive = Window(limit=20, seconds=60.0)


def check_blanket(request: Request) -> None:
    _blanket.hit(identity(request))


def check_expensive(request: Request) -> None:
    """For a route-level `Depends`, on top of the blanket check above."""
    _expensive.hit(identity(request))
