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
        # Suggestions, not a limit: the field takes any name the provider
        # accepts, and Test says whether it does.
        # Each one asked for and confirmed against the API, because a
        # suggestion that fails is worse than no suggestion.
        "models": [
            "gemini-3.1-flash-live-preview",
            "gemini-2.5-flash-native-audio-preview-09-2025",
        ],
        "keyHint": "A Google AI Studio key, beginning AIza…",
        # Where to go and get one, and where to pay for it. Two links rather
        # than one because they are two different errands and the second is
        # the one people are sent back for: a key with no credit behind it
        # passes every check that costs nothing and fails on the first call.
        "keyUrl": "https://aistudio.google.com/apikey",
        "billingUrl": "https://ai.studio/projects",
        "note": "The only engine this build speaks. Native speech to speech.",
    },
    {
        "id": "openai",
        "label": "OpenAI Realtime",
        "supported": True,
        "defaultModel": "gpt-realtime",
        "models": ["gpt-realtime", "gpt-4o-realtime-preview"],
        "keyHint": "An OpenAI key, beginning sk-…",
        "keyUrl": "https://platform.openai.com/api-keys",
        "billingUrl": "https://platform.openai.com/settings/organization/billing",
        "note": (
            "Speech to speech over the Realtime API. Built and tested against "
            "the protocol, but no call has been placed on it from this "
            "deployment — press Test, then make one call and listen before "
            "moving your customers onto it."
        ),
        # Said separately from `note` so a screen can mark it without having
        # to read English. It is not "unsupported": the code is here and the
        # key test is real. It is untried on a live call, which is a different
        # and smaller thing, and the difference matters to whoever switches.
        "unproven": True,
    },
]

SUPPORTED = {e["id"] for e in ENGINES if e["supported"]}


# The last time a real call or reply could not reach the engine, and why.
#
# Kept because the operator found out the engine was dead from a customer,
# not from this product: a key with no credit answers every settings screen
# cheerfully and fails only on the call itself. Held in memory rather than
# written to the database — one process serves these, a restart clearing it is
# harmless, and the next failure records it again within seconds.
_last_failure: dict[str, Any] | None = None


def note_failure(reason: str) -> None:
    """A call or a reply could not reach the engine. Never raises."""
    global _last_failure
    _last_failure = {"at": time.time(), "reason": (reason or "unknown")[:200]}
    log.error("speech engine unavailable: %s", reason)


def note_success() -> None:
    """The engine answered, so whatever was wrong is over."""
    global _last_failure
    _last_failure = None


def last_failure() -> dict[str, Any] | None:
    return dict(_last_failure) if _last_failure else None


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
        # What customers are actually hitting, which a key that merely exists
        # tells nobody.
        "lastFailure": last_failure(),
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


async def check(*, engine: str, model: str, api_key: str = "") -> dict[str, Any]:
    """Ask the provider whether this key and model actually work.

    Saving a key proves nothing — a typo, a revoked key or a model name the
    provider has retired all store perfectly well and are only discovered by
    the first caller, who hears silence. This is the difference between
    "saved" and "working", and it is worth one round trip to know.

    `api_key` empty means "whatever is in force", so the operator can test
    what is already stored without typing it again.
    """
    engine = (engine or "").strip().lower()
    if engine not in SUPPORTED:
        return {
            "ok": False,
            "detail": f"{engine or 'That engine'} is not implemented in this build, "
                      "so there is nothing to test against.",
        }

    live = await current()
    key = (api_key or "").strip() or live["apiKey"]
    name = (model or "").strip() or live["model"]
    if not key:
        return {"ok": False, "detail": "No API key is set, here or in the environment."}

    if engine == "openai":
        return await _check_openai(key, name)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        # Deliberately a real generation, not a model lookup. Describing a
        # model costs nothing and therefore proves nothing: a key whose
        # account has run out of credit describes models perfectly happily and
        # then fails on the first word of the first call. That is exactly how
        # a depleted project passed this check and went to production, where
        # callers got silence. One token is cheap; being wrong about this is
        # not.
        async def one_token(model: str):
            return await client.aio.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
                config=types.GenerateContentConfig(
                    max_output_tokens=8,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )

        if _is_streaming_only(name):
            # A live model answers only over a bidirectional socket, and
            # opening one bills for a session and leaves it running — far too
            # much to spend on "is this key any good?". So: confirm the model
            # exists, then prove the account can actually pay for a token by
            # spending one on the text model, which shares the same billing.
            await client.aio.models.get(model=name)
            answer = await one_token(settings.gemini_text_model)
        else:
            answer = await one_token(name)
    except Exception as exc:  # noqa: BLE001 — the provider's refusal is the answer
        detail = str(exc)
        lowered = detail.lower()
        if any(w in lowered for w in ("depleted", "402", "billing", "insufficient")):
            said = ("This key is valid but its Google account has no credit "
                    "left, so every call would connect to silence. Top up the "
                    "project at ai.studio/projects.")
        elif any(w in lowered for w in ("quota", "resource_exhausted", "429")):
            said = "This key is over its quota right now. Try again shortly."
        elif any(w in lowered for w in ("api key", "api_key", "unauthenticated",
                                        "permission_denied", "401", "403")):
            said = "The provider rejected this API key."
        elif "not found" in lowered or "404" in lowered:
            said = f"The provider does not know a model called {name!r}."
        else:
            said = detail[:200]
        log.warning("engine check failed (%s): %s", type(exc).__name__, detail[:200])
        note_failure(said)
        return {"ok": False, "detail": said}

    spoke = (getattr(answer, "text", "") or "").strip()
    note_success()
    return {
        "ok": True,
        "detail": f"The key works, {name} answered, and the account has credit.",
        "model": name,
        "said": spoke[:40],
    }


def _is_streaming_only(model: str) -> bool:
    """Whether this model answers only over a live bidirectional socket.

    `generate_content` on one of these fails with a 400 that says nothing
    about the key, which would report a perfectly good key as broken.
    """
    lowered = (model or "").lower()
    return "live" in lowered or "realtime" in lowered


async def _check_openai(key: str, model: str) -> dict[str, Any]:
    """Ask OpenAI whether this key works and this model exists.

    The model list rather than a realtime handshake: opening a realtime socket
    bills for a session and leaves one running, which is a lot to spend on
    answering "is this key any good?".
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.HTTPError as exc:
        log.warning("openai check failed: %s", exc)
        return {"ok": False, "detail": "OpenAI could not be reached."}

    if response.status_code in (401, 403):
        return {"ok": False, "detail": "OpenAI rejected this API key."}
    if response.status_code >= 400:
        return {
            "ok": False,
            "detail": f"OpenAI answered {response.status_code}: "
                      f"{response.text[:140]}",
        }

    try:
        names = {m.get("id") for m in (response.json().get("data") or [])}
    except Exception:  # noqa: BLE001
        names = set()

    if names and model not in names:
        return {
            "ok": False,
            "detail": f"The key works, but this account cannot see a model called "
                      f"{model!r}. Check the name, or that your account has "
                      "Realtime access.",
        }
    return {
        "ok": True,
        "detail": f"The key works and {model} is available. No call has been "
                  "placed on this engine yet — make one and listen before "
                  "moving customers onto it.",
        "model": model,
    }
