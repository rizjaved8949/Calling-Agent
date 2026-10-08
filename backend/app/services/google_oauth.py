"""
The "Connect Google Drive" flow for a company.

Three steps: we hand the company a Google consent URL, Google sends the browser
back to us with a one-time code, and we exchange that code for a *refresh*
token which is sealed into the tenant row. From then on the company's
recordings are written to their own Drive.

The `state` parameter carries which tenant is connecting, signed so that a
third party cannot aim someone else's callback at their own tenant. It is the
only thing standing between this endpoint and an attacker attaching their Drive
to a company they do not own, so it is verified before the code is spent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from ..config import settings
from ..errors import AppError, UpstreamError
from .drive import SCOPES, TOKEN_URL

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_STATE_TTL_SECONDS = 600


def _state_key() -> bytes:
    """Signs the state parameter.

    Reuses CREDENTIALS_SECRET so there is one secret to manage, but through a
    separate HMAC — the state is public and must not be a signal about the key
    that protects stored tokens.
    """
    secret = settings.credentials_secret.strip() or settings.supabase_service_key.strip()
    if not secret:
        raise AppError(
            503,
            "CREDENTIALS_SECRET must be set before a company can connect Google Drive.",
            code="not_configured",
        )
    return hashlib.sha256(f"google-oauth-state::{secret}".encode()).digest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_state(phone_number_id: str, *, return_to: str = "") -> str:
    payload = json.dumps(
        {"t": phone_number_id, "r": return_to, "x": int(time.time()) + _STATE_TTL_SECONDS},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(_state_key(), payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(signature)}"


def read_state(state: str) -> dict[str, Any]:
    """Open a state parameter, or refuse it.

    Every failure is the same refusal: an expired state and a forged one are
    both "start again", and distinguishing them in the response would tell an
    attacker which half they got right.
    """
    try:
        payload_b64, signature_b64 = state.split(".", 1)
        payload = _unb64url(payload_b64)
        expected = hmac.new(_state_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64url(signature_b64)):
            raise ValueError("bad signature")
        body = json.loads(payload)
        if int(body.get("x", 0)) < time.time():
            raise ValueError("expired")
        if not body.get("t"):
            raise ValueError("no tenant")
        return body
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            400,
            "That Google sign-in link is no longer valid. Start the connection again.",
            code="bad_state",
        ) from exc


def redirect_uri() -> str:
    """Where Google sends the browser back.

    Must match an authorised redirect URI on the OAuth client exactly, down to
    the trailing slash — a mismatch is Google's `redirect_uri_mismatch`, which
    is the single most common reason this flow fails.
    """
    explicit = settings.google_oauth_redirect_url.strip()
    if explicit:
        return explicit
    base = settings.public_base_url.strip().rstrip("/")
    if not base:
        raise AppError(
            503,
            "Set PUBLIC_BASE_URL or GOOGLE_OAUTH_REDIRECT_URL so Google knows where "
            "to send the company back.",
            code="not_configured",
        )
    return f"{base}/api/google/callback"


def authorization_url(phone_number_id: str, *, return_to: str = "") -> str:
    if not settings.google_oauth_configured:
        raise AppError(
            503,
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set on the server.",
            code="not_configured",
        )
    query = {
        "client_id": settings.google_client_id.strip(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        # offline + consent is what actually returns a refresh token. Without
        # `prompt=consent` Google reissues only an access token for an account
        # that has approved before, and the connection silently lasts an hour.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": make_state(phone_number_id, return_to=return_to),
    }
    return f"{AUTH_URL}?{urlencode(query)}"


async def exchange_code(code: str) -> dict[str, Any]:
    """Trade the one-time code for tokens. Returns Google's response body."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id.strip(),
                "client_secret": settings.google_client_secret.strip(),
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        log.warning("Google token exchange failed: %s", response.text[:300])
        raise UpstreamError("Google refused the connection. Try connecting again.")
    body = response.json()
    if not body.get("refresh_token"):
        # Happens when the account has approved before and `prompt=consent` was
        # dropped. Without a refresh token the connection dies in an hour, so
        # it is refused rather than stored.
        raise UpstreamError(
            "Google did not return a refresh token. Remove this app from the "
            "account's third-party access and connect again."
        )
    return body
