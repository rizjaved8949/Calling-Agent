"""
Short-lived tokens for things a browser fetches without a header.

`playback.py` does this for one recording. The same problem turns up wherever
the browser itself does the fetching rather than our own code: a websocket
cannot set `Authorization`, and nor can an `<img>`, `<video>` or `<audio>`
element. So the proof travels in the URL.

The difference from `playback` is the audience. A ticket names what it is for
("support-socket", "support-file") and is signed under a key derived from that
name, so a ticket minted to let someone watch a chat cannot be replayed to
download an attachment, and neither can stand in for a recording link.

A ticket is not a credential. It names one subject, for one audience, for
minutes, and grants nothing else.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..config import settings
from ..errors import AppError

DEFAULT_TTL_SECONDS = 900


def _key(audience: str) -> bytes:
    secret = settings.credentials_secret.strip() or settings.admin_api_key.strip()
    if not secret:
        raise AppError(
            503,
            "CREDENTIALS_SECRET must be set before this feature can be used.",
            code="not_configured",
        )
    # Domain-separated per audience, so a signature is only ever valid for the
    # one thing it was minted for.
    return hashlib.sha256(f"ticket::{audience}::{secret}".encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint(
    audience: str,
    subject: str,
    *,
    ttl: int = DEFAULT_TTL_SECONDS,
    extra: dict | None = None,
) -> str:
    """A signed ticket naming `subject`, valid for `audience` only."""
    payload: dict = {"s": subject, "x": int(time.time()) + ttl}
    if extra:
        payload.update(extra)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(_key(audience), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(signature)}"


def verify(token: str, audience: str) -> dict:
    """The ticket's payload, or a refusal. `s` is the subject it names."""
    refusal = AppError(403, "This link is no longer valid.", code="bad_ticket")
    try:
        payload_b64, signature_b64 = token.split(".", 1)
        raw = _unb64(payload_b64)
        expected = hmac.new(_key(audience), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature_b64)):
            raise refusal
        body = json.loads(raw)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — a malformed ticket is a refusal, not a 500
        raise refusal from exc

    if not isinstance(body, dict) or not body.get("s"):
        raise refusal
    if int(body.get("x", 0)) < time.time():
        raise AppError(403, "This link has expired. Reload the page.", code="expired_ticket")
    return body
