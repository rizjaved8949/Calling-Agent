"""
Everything under /api, assembled in one place.

Health sits outside the prefix as well as inside it: a load balancer is
configured once and should not have to know about the versioning of an API it
is only checking the pulse of.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..security.rate_limit import check_blanket, check_expensive
from .routes import (
    agents, ask, auth, calls, campaigns, companies, exports, gaps, google, health,
    help as help_routes, knowledge, media, messaging, numbers, platform, recordings,
    support, team, usage, webhooks,
)

api_router = APIRouter(prefix="/api")

# Health is pinged often (by a load balancer, by Render itself) and carries no
# cost or risk, so it is the one router left outside the blanket rate limit.
api_router.include_router(health.router)

# Everything else: one shared blanket limit per caller, on top of whatever
# stricter limit an individual route adds for itself. See
# `security/rate_limit.py` for why this is in-memory and what it is for.
_limited = [Depends(check_blanket)]
# Signup and login get the tighter limit too: Firebase itself guards against
# password guessing, but nothing stops a script from hammering the
# token-verification step itself once it has a valid token.
api_router.include_router(
    auth.router, dependencies=[Depends(check_blanket), Depends(check_expensive)]
)
api_router.include_router(
    platform.router, dependencies=[Depends(check_blanket), Depends(check_expensive)]
)
api_router.include_router(platform.data_router, dependencies=_limited)
api_router.include_router(
    team.router, dependencies=[Depends(check_blanket), Depends(check_expensive)]
)
api_router.include_router(companies.router, dependencies=_limited)
api_router.include_router(calls.router, dependencies=_limited)
# Mounted after calls so that /calls/{id}/recording is matched by the
# recording router rather than being swallowed by /calls/{call_id}.
api_router.include_router(recordings.router, dependencies=_limited)
api_router.include_router(recordings.bulk_router, dependencies=_limited)
api_router.include_router(messaging.router, dependencies=_limited)
api_router.include_router(knowledge.router, dependencies=_limited)
# Agents, knowledge bases and the routing between them. Mounted after
# knowledge so /knowledge-bases is not swallowed by /knowledge's own paths.
api_router.include_router(agents.router, dependencies=_limited)
api_router.include_router(ask.router, dependencies=_limited)
api_router.include_router(numbers.router, dependencies=_limited)
api_router.include_router(help_routes.router, dependencies=_limited)
api_router.include_router(support.router, dependencies=_limited)
api_router.include_router(support.platform_router, dependencies=_limited)
# The chat socket and the attachment fetch: both are reached by the browser
# itself, carry a signed ticket rather than a key, and cannot take a `Depends`
# that keys a rate limit off a `Request`.
api_router.include_router(support.socket_router)
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
