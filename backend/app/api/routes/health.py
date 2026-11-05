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
from ...services import keepalive
from ...services.agent import live, whatsapp_media
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
            # True when rows are in a JSON file beside the app, not Postgres.
            "localStore": getattr(supabase, "is_local", False),
            "credentialEncryption": encryption_configured(),
            # Recording. ffmpeg missing is not fatal — audio is stored as it
            # arrived — but it means no seeking and much larger files.
            "transcoding": ffmpeg_available(),
            "objectStorage": settings.supabase_configured,
            "googleOAuth": settings.google_oauth_configured,
            # Admin surface. False means company onboarding is switched off.
            "adminApi": bool(settings.admin_api_key.strip()),
            "publicBaseUrl": bool(settings.public_base_url.strip()),
            # The voice engine has a key configured.
            "agentKey": settings.agent_configured,
            "agentEngine": settings.voice_engine,
            # Whether WhatsApp calls can be answered: aiortc terminates Meta's
            # WebRTC leg, and without it an inbound call is logged and
            # nothing more. Reported rather than assumed, because the wheel
            # not building on a host is otherwise only discovered by a caller.
            "agentMediaBridge": whatsapp_media.available(),
            # SIM calls and operator takeover need no WebRTC — a carrier
            # websocket carries PCM directly — so they are listed apart.
            "carrierMedia": True,
            # Platform Twilio credentials in the environment, which numbers
            # may fall back on when the operator allows it. A company's own
            # Twilio number works without them; this says nothing about those.
            "twilio": bool(settings.twilio_account_sid.strip()
                           and settings.twilio_auth_token.strip()),
            # Twilio refuses to stream audio to anything but wss://, so a
            # Twilio line needs the public address to be https.
            "twilioReachable": settings.public_base_url.strip().startswith("https://"),
            "callsInProgress": live.active(),
            # How many more this process will take before it starts refusing.
            # Visible here so headroom can be read off the health check during
            # a busy period, rather than inferred from a call that failed.
            "callCapacity": live.capacity(),
            "atCapacity": live.at_capacity(),
            # Whether this instance is keeping itself awake, which is only
            # meaningful on a plan that stops an idle service.
            "keepalive": keepalive.should_run(),
        },
    }
