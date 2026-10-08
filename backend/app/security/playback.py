"""
Short-lived tokens that let an <audio> element fetch a recording.

A browser's media element sends no `Authorization` header. It is given a URL
and it fetches it, so a recording endpoint that only accepts a bearer token
cannot be played: the request arrives unauthenticated, returns 401, and the
player shows controls that silently do nothing — the exact failure the API was
built to avoid elsewhere.

The alternative, fetching the audio with a token and handing the element a
`blob:` URL, costs the whole file before the first second plays and throws away
range requests. So instead the URL itself carries proof, minted only for
someone who already authenticated and asked for *this* recording.

A token names one call, for one company, for a few minutes. It is not a
credential: it cannot list calls, cannot reach another recording, and expires
long before a shared link is useful to anyone.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..config import settings
from ..errors import AppError

_TTL_SECONDS = 900  # fifteen minutes: long enough to listen, short enough to leak harmlessly


def _key() -> bytes:
    secret = settings.credentials_secret.strip() or settings.admin_api_key.strip()
    if not secret:
        raise AppError(
            503,
            "CREDENTIALS_SECRET must be set before recordings can be played back.",
            code="not_configured",
        )
    # Separated from the credential-sealing key by domain, so a playback token
    # can never be mistaken for, or help forge, a stored secret.
    return hashlib.sha256(f"recording-playback::{secret}".encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint(tenant_id: str, call_id: str, ttl: int = _TTL_SECONDS) -> str:
    payload = json.dumps(
        {"t": tenant_id, "c": call_id, "x": int(time.time()) + ttl}, separators=(",", ":")
    ).encode()
    signature = hmac.new(_key(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify(token: str, call_id: str) -> str:
    """The tenant this token speaks for, or a refusal.

    The call id is checked against the one in the token rather than trusted
    from the path: without that, a token for one recording would play any
    recording belonging to that company.
    """
    refusal = AppError(403, "This playback link is no longer valid.", code="bad_token")
    try:
        payload_b64, signature_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature_b64)):
            raise refusal
        body = json.loads(payload)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise refusal from exc

    if int(body.get("x", 0)) < time.time():
        raise AppError(403, "This playback link has expired. Reload the page.", code="expired_token")
    if body.get("c") != call_id or not body.get("t"):
        raise refusal
    return str(body["t"])
