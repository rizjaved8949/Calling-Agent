"""
Liveness, and a readable account of what this process can actually do.

The second part matters more than it sounds. Every one of these subsystems is
optional and fails quietly when unconfigured — "WhatsApp is connected but Drive
is not" is the single most common support question, and this endpoint answers
it without anyone reading the environment over someone's shoulder. It names no
secret: only whether each one is present.
"""
from __future__ import annotations

import time

from fastapi import APIRouter

from ...config import settings
from ...db.supabase import supabase
from ...security.secret_box import encryption_configured
from ...services.audio import ffmpeg_available

router = APIRouter(tags=["health"])
_started = time.time()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "uptimeSeconds": round(time.time() - _started),
        "capabilities": {
            # Persistence. Without this the service answers webhooks and
            # forgets them, so it is the first thing to check.
            "database": supabase.configured,
            "credentialEncryption": encryption_configured(),
            # Recording. ffmpeg missing is not fatal — audio is stored as it
            # arrived — but it means no seeking and much larger files.
            "transcoding": ffmpeg_available(),
            "objectStorage": supabase.configured,
            "googleOAuth": settings.google_oauth_configured,
            # Admin surface. False means company onboarding is switched off.
            "adminApi": bool(settings.admin_api_key.strip()),
            "publicBaseUrl": bool(settings.public_base_url.strip()),
        },
    }
