"""
Everything under /api, assembled in one place.

Health sits outside the prefix as well as inside it: a load balancer is
configured once and should not have to know about the versioning of an API it
is only checking the pulse of.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..security.rate_limit import check_blanket
from .routes import (
    calls, campaigns, companies, exports, gaps, google, health, knowledge, media,
    messaging, recordings, usage, webhooks,
)

api_router = APIRouter(prefix="/api")

# Health is pinged often (by a load balancer, by Render itself) and carries no
# cost or risk, so it is the one router left outside the blanket rate limit.
api_router.include_router(health.router)

# Everything else: one shared blanket limit per caller, on top of whatever
# stricter limit an individual route adds for itself. See
# `security/rate_limit.py` for why this is in-memory and what it is for.
_limited = [Depends(check_blanket)]
api_router.include_router(companies.router, dependencies=_limited)
api_router.include_router(calls.router, dependencies=_limited)
# Mounted after calls so that /calls/{id}/recording is matched by the
# recording router rather than being swallowed by /calls/{call_id}.
api_router.include_router(recordings.router, dependencies=_limited)
api_router.include_router(messaging.router, dependencies=_limited)
api_router.include_router(knowledge.router, dependencies=_limited)
# Not rate-limited: mostly websocket routes, and `Depends` needs a `Request`
# to key off, which a websocket handshake does not give it. Each socket is
# already gated by a call-specific token from `_authorise`, not a bare key.
api_router.include_router(media.router)
api_router.include_router(campaigns.router, dependencies=_limited)
api_router.include_router(usage.router, dependencies=_limited)
api_router.include_router(gaps.router, dependencies=_limited)
api_router.include_router(google.router, dependencies=_limited)
api_router.include_router(exports.router, dependencies=_limited)
# Webhooks are Meta/Infobip calling us, verified by signature rather than by
# key, and the one path a slow carrier retrying itself must never 429.
api_router.include_router(webhooks.router)
