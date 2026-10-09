"""
Settings the platform operator owns, changeable without a redeploy.

Today that is the speech engine: which provider carries a conversation, which
model, and the key to reach it. Those lived in the server's environment, which
meant rotating a leaked key, or trying a newer model, needed someone with
access to the host — and in the meantime every call on the platform was
failing.

Stored beside the super admin's own row, in the same table and with the same
protection, because they are the same kind of secret: platform-wide, not a
customer's. The key is sealed before it is written and never returned — only
its last four characters, enough to tell which key is in place.

The environment stays the fallback. A deployment that has never opened this
screen behaves exactly as it did, so this is safe to add to a running system.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..db.supabase import supabase
from ..security.secret_box import SecretBoxError, decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)

TABLE = "voice_platform_admin"
ROW_ID = "engine"
_CACHE_TTL_SECONDS = 30.0
_cache: tuple[float, dict[str, Any]] | None = None

# What this build can actually carry a conversation on. Listing a provider the
# code cannot speak to would be a promise the first caller discovers is false,
# so anything not implemented is named here as such rather than offered.
ENGINES: list[dict[str, Any]] = [
    {
        "id": "gemini",
        "label": "Google Gemini Live",
        "supported": True,
        "defaultModel": "gemini-3.1-flash-live-preview",
        "keyHint": "A Google AI Studio key, beginning AIza…",
        "note": "The only engine this build speaks. Native speech to speech.",
    },
    {
        "id": "openai",
        "label": "OpenAI Realtime",
        "supported": False,
        "defaultModel": "gpt-realtime",
        "keyHint": "An OpenAI key, beginning sk-…",
        "note": (
            "Not wired up in this build. The key can be stored, but calls will "
            "keep using Gemini until the engine is implemented."
        ),
    },
]

SUPPORTED = {e["id"] for e in ENGINES if e["supported"]}


def _now() -> float:
    return time.monotonic()


def invalidate() -> None:
    global _cache
    _cache = None


async def _stored() -> dict[str, Any]:
    global _cache
    if _cache and _now() - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    row = None
    try:
        row = await supabase.select_one(TABLE, params={"id": f"eq.{ROW_ID}"})
    except Exception:  # noqa: BLE001 — a lookup failure must not fail a call
        log.exception("could not read the platform engine settings")
    data = (row or {}).get("data") if isinstance(row, dict) else None
    found = data if isinstance(data, dict) else {}
    _cache = (_now(), found)
    return found


async def current() -> dict[str, str]:
    """Engine, model and key in force, with the environment as the fallback."""
    stored = await _stored()
    engine = str(stored.get("engine") or "").strip() or settings.voice_engine.strip() or "gemini"
    model = str(stored.get("model") or "").strip() or settings.gemini_live_model.strip()
    key = ""
    sealed = stored.get("apiKey")
    if isinstance(sealed, str) and sealed:
        try:
            key = decrypt_secret(sealed)
        except SecretBoxError as exc:
            log.error("could not read the stored engine key — %s", exc)
    return {
        "engine": engine,
        "model": model,
        "apiKey": key or settings.gemini_api_key.strip(),
        # Which of the two is actually in use, so the screen can say so rather
        # than leaving someone to wonder why their key seems ignored.
        "source": "platform settings" if key else "the server environment",
    }


async def public() -> dict[str, Any]:
    """What the portal shows. Never the key itself."""
    live = await current()
    key = live["apiKey"]
    return {
        "engine": live["engine"],
        "model": live["model"],
        "keySet": bool(key),
        "keyHint": f"…{key[-4:]}" if len(key) >= 8 else ("…" if key else ""),
        "source": live["source"],
        "engines": ENGINES,
        "supported": live["engine"] in SUPPORTED,
    }


async def save(*, engine: str, model: str, api_key: str | None) -> dict[str, Any]:
    """Write the operator's choice.

    `api_key=None` means "not mentioned" and keeps the stored one — the screen
    never shows a key back, so a blank field cannot mean "clear it". An empty
    string does clear it, falling back to the environment.
    """
    stored = dict(await _stored())
    stored["engine"] = engine.strip().lower()
    stored["model"] = model.strip()
    if api_key is not None:
        stored["apiKey"] = encrypt_secret(api_key.strip()) if api_key.strip() else ""
    stored["updatedAt"] = time.time()

    await supabase.upsert(TABLE, {
        "id": ROW_ID,
        "tenant_id": None,
        "data": stored,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    invalidate()
    log.info("platform engine set to %s (%s)", stored["engine"], stored["model"])
    return await public()
