"""
The platform's one super admin: an email and password instead of a pasted key.

There is exactly one, and it can be created exactly once — the first person to
open `/ops-login` on a fresh deployment sets it up, and from then on the setup
endpoint refuses. Creating it hands back a **recovery code**, shown once, which
is what "forgot password" asks for (no email delivery is configured on this
deployment, so a reset link has nowhere to go). The server's ADMIN_API_KEY is
accepted in its place as a last resort, since whoever holds that already has
full platform access.

A sign-in produces a session token that is accepted wherever `X-Admin-Key` is.
It carries the password's version, so resetting the password signs out every
existing session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..db.supabase import supabase
from ..errors import AppError

TABLE = "voice_platform_admin"
ROW_ID = "superadmin"
TOKEN_PREFIX = "sa1."
SESSION_SECONDS = 12 * 3600

_cache: tuple[float, dict[str, Any] | None] | None = None


def _hash(secret: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _check(secret: str, stored: str) -> bool:
    try:
        _, salt, _digest = stored.split("$")
        return hmac.compare_digest(_hash(secret, base64.b64decode(salt)), stored)
    except (ValueError, TypeError):
        return False


def _signing_key() -> bytes:
    key = settings.credentials_secret.strip() or settings.admin_api_key.strip()
    if not key:
        raise AppError(
            503, "Set CREDENTIALS_SECRET on the server before creating the super admin.",
            code="no_signing_secret",
        )
    return hashlib.sha256(b"superadmin-session:" + key.encode()).digest()


async def _row() -> dict[str, Any] | None:
    global _cache
    if _cache and time.monotonic() - _cache[0] < 10:
        return _cache[1]
    row = await supabase.select_one(TABLE, params={"id": f"eq.{ROW_ID}"})
    data = row.get("data") if row else None
    _cache = (time.monotonic(), data if isinstance(data, dict) else None)
    return _cache[1]


async def _write(data: dict[str, Any]) -> None:
    global _cache
    await supabase.upsert(TABLE, {
        "id": ROW_ID, "tenant_id": None, "data": data,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _cache = None


async def exists() -> bool:
    return await _row() is not None


def _new_recovery_code() -> str:
    raw = secrets.token_hex(10).upper()
    return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def _validate_password(password: str) -> None:
    if len(password) < 10:
        raise AppError(422, "Use a password of at least 10 characters.", code="weak_password")


def _mint(version: int) -> str:
    expires = int(time.time()) + SESSION_SECONDS
    body = f"{expires}.{version}"
    sig = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{TOKEN_PREFIX}{body}.{sig}"


async def create(email: str, password: str, name: str) -> dict[str, str]:
    """The one-time setup. Refused for good once an account exists."""
    if await exists():
        raise AppError(409, "The super admin has already been created. Sign in instead.",
                       code="superadmin_exists")
    email = email.strip().lower()
    if "@" not in email:
        raise AppError(422, "Enter a valid email address.", code="bad_email")
    _validate_password(password)
    recovery = _new_recovery_code()
    await _write({
        "email": email, "name": name.strip() or "Super admin",
        "passwordHash": _hash(password), "recoveryHash": _hash(recovery),
        "version": 1, "createdAt": time.time(),
    })
    return {"token": _mint(1), "recoveryCode": recovery, "email": email}


async def login(email: str, password: str) -> dict[str, str]:
    row = await _row()
    # One message for every failure, so the form does not confirm the email.
    if (
        row is None
        or row.get("email") != email.strip().lower()
        or not _check(password, str(row.get("passwordHash", "")))
    ):
        raise AppError(401, "Email or password is incorrect.", code="bad_credentials")
    return {"token": _mint(int(row.get("version", 1))), "email": row["email"],
            "name": row.get("name", "")}


async def reset(email: str, recovery_code: str, new_password: str) -> dict[str, str]:
    """Forgot password: the recovery code (or the server's ADMIN_API_KEY)."""
    row = await _row()
    code = recovery_code.strip()
    admin_key = settings.admin_api_key.strip()
    proven = row is not None and row.get("email") == email.strip().lower() and (
        _check(code.upper(), str(row.get("recoveryHash", "")))
        or (bool(admin_key) and secrets.compare_digest(code, admin_key))
    )
    if not proven:
        raise AppError(401, "That email and recovery code do not match.", code="bad_recovery")
    _validate_password(new_password)
    recovery = _new_recovery_code()
    version = int(row.get("version", 1)) + 1
    await _write({**row, "passwordHash": _hash(new_password),
                  "recoveryHash": _hash(recovery), "version": version})
    # The old code is spent; a new one replaces it, shown once.
    return {"token": _mint(version), "recoveryCode": recovery, "email": row["email"]}


async def change_password(current: str, new_password: str) -> dict[str, str]:
    row = await _row()
    if row is None or not _check(current, str(row.get("passwordHash", ""))):
        raise AppError(401, "The current password is incorrect.", code="bad_credentials")
    _validate_password(new_password)
    version = int(row.get("version", 1)) + 1
    await _write({**row, "passwordHash": _hash(new_password), "version": version})
    return {"token": _mint(version)}


async def token_valid(token: str) -> bool:
    if not token.startswith(TOKEN_PREFIX):
        return False
    try:
        expires, version, sig = token[len(TOKEN_PREFIX):].split(".")
        good = hmac.new(_signing_key(), f"{expires}.{version}".encode(), hashlib.sha256).hexdigest()
    except (ValueError, AppError):
        return False
    if not hmac.compare_digest(sig, good) or int(expires) < time.time():
        return False
    row = await _row()
    return row is not None and int(row.get("version", 1)) == int(version)
