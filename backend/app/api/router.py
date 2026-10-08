"""
Everything under /api, assembled in one place.

Health sits outside the prefix as well as inside it: a load balancer is
configured once and should not have to know about the versioning of an API it
is only checking the pulse of.
"""
from __future__ import annotations

from fastapi import APIRouter

from .routes import (
    calls, companies, exports, google, health, knowledge, messaging, recordings, webhooks,
)

api_router = APIRouter(prefix="/api")

api_router.include_router(health.router)
api_router.include_router(companies.router)
api_router.include_router(calls.router)
# Mounted after calls so that /calls/{id}/recording is matched by the
# recording router rather than being swallowed by /calls/{call_id}.
api_router.include_router(recordings.router)
api_router.include_router(messaging.router)
api_router.include_router(knowledge.router)
api_router.include_router(google.router)
api_router.include_router(exports.router)
api_router.include_router(webhooks.router)
