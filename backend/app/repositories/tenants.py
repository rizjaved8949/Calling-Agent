"""
The tenant registry: which customer a webhook belongs to, and their credentials.

Resolution order, highest precedence first:

1. the ``voice_tenants`` table — what a business entered in the app. This wins,
   so an operator correcting their credentials in the UI is not silently
   overridden by a stale environment variable.
2. the legacy single-number env values, kept as a synthetic ``default`` tenant
   so a pre-SaaS deployment keeps answering while its number is migrated.

Nothing here reaches the telephony network. It only answers *which customer*
a call belongs to and *what their credentials are*.

Rows are cached briefly. A webhook arrives on the hot path of an incoming call
and a database round trip per webhook is latency the caller hears; a few
seconds of staleness after an edit is not.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from ..config import settings
from ..db.supabase import supabase
from ..models.tenant import (
    NESTED_SECRET_FIELDS,
    SECRET_FIELDS,
    GoogleDriveLink,
    Tenant,
)
from ..security.secret_box import SecretBoxError, decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)

TABLE = "voice_tenants"
_CACHE_TTL_SECONDS = 10.0

_cache: dict[str, tuple[float, Tenant]] = {}


def _now() -> float:
    return time.monotonic()


def invalidate(phone_number_id: str | None = None) -> None:
    """Drop cached rows after a write, so the next read sees the change."""
    if phone_number_id:
        _cache.pop(phone_number_id, None)
    else:
        _cache.clear()


# ---------------------------------------------------------------------------
# Sealing
# ---------------------------------------------------------------------------


def _seal(data: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(data)
    for field in SECRET_FIELDS:
        value = sealed.get(field)
        if isinstance(value, str) and value:
            sealed[field] = encrypt_secret(value)
    for container, field in NESTED_SECRET_FIELDS:
        nested = sealed.get(container)
        if isinstance(nested, dict):
            value = nested.get(field)
            if isinstance(value, str) and value:
                sealed[container] = {**nested, field: encrypt_secret(value)}
    return sealed


def _open(data: dict[str, Any], phone_number_id: str) -> dict[str, Any]:
    opened = dict(data)
    for field in SECRET_FIELDS:
        value = opened.get(field)
        if isinstance(value, str) and value:
            opened[field] = _open_one(value, phone_number_id, field)
    for container, field in NESTED_SECRET_FIELDS:
        nested = opened.get(container)
        if isinstance(nested, dict):
            value = nested.get(field)
            if isinstance(value, str) and value:
                opened[container] = {
                    **nested,
                    field: _open_one(value, phone_number_id, f"{container}.{field}"),
                }
    return opened


def _open_one(value: str, phone_number_id: str, field: str) -> str:
    """Unseal one credential, or blank it.

    A credential that cannot be opened is dropped rather than passed through:
    sending ciphertext to Meta as if it were a token produces a baffling 401 at
    the far end, whereas an empty one surfaces here as "not configured", which
    is the truth.
    """
    try:
        return decrypt_secret(value)
    except SecretBoxError as exc:
        log.error("tenant %s: could not read %s — %s", phone_number_id, field, exc)
        return ""


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _from_row(row: dict[str, Any]) -> Tenant | None:
    phone_number_id = str(row.get("id") or "").strip()
    data = row.get("data")
    if not phone_number_id or not isinstance(data, dict):
        return None
    opened = _open(data, phone_number_id)
    opened["phoneNumberId"] = phone_number_id
    if row.get("organization_id") and not opened.get("organizationId"):
        opened["organizationId"] = row["organization_id"]
    # A row written before Drive support has no googleDrive object at all.
    if not isinstance(opened.get("googleDrive"), dict):
        opened["googleDrive"] = {}
    try:
        return Tenant.model_validate(opened)
    except Exception:  # noqa: BLE001 — one malformed row must not break routing
        log.exception("tenant %s has a row this build cannot read", phone_number_id)
        return None


def _legacy_tenant() -> Tenant | None:
    """The pre-SaaS single-number deployment, as a synthetic tenant."""
    phone_number_id = settings.whatsapp_phone_number_id.strip()
    if not phone_number_id:
        return None
    return Tenant(
        phoneNumberId=phone_number_id,
        wabaId=settings.whatsapp_business_account_id.strip(),
        name="Default",
        accessToken=settings.whatsapp_access_token.strip(),
        appSecret=settings.meta_app_secret.strip(),
        verifyToken=settings.whatsapp_webhook_verify_token.strip(),
        graphApiVersion=settings.meta_graph_api_version.strip(),
        infobipApiKey=settings.infobip_api_key.strip(),
        infobipBaseUrl=settings.infobip_base_url.strip(),
        defaultCountryCode=settings.default_country_code.strip(),
        googleDrive=GoogleDriveLink(),
    )


async def get(phone_number_id: str) -> Tenant | None:
    """The tenant for a Meta phone_number_id, or None."""
    key = (phone_number_id or "").strip()
    if not key:
        return None

    cached = _cache.get(key)
    if cached and _now() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    tenant: Tenant | None = None
    if supabase.configured:
        row = await supabase.select_one(TABLE, params={"id": f"eq.{key}"})
        if row:
            tenant = _from_row(row)

    if tenant is None:
        legacy = _legacy_tenant()
        if legacy and legacy.phone_number_id == key:
            tenant = legacy

    if tenant is not None:
        _cache[key] = (_now(), tenant)
    return tenant


async def require(phone_number_id: str) -> Tenant:
    from ..errors import NotFound

    tenant = await get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")
    return tenant


async def list_all(organization_id: str | None = None) -> list[Tenant]:
    if not supabase.configured:
        legacy = _legacy_tenant()
        return [legacy] if legacy else []
    params: dict[str, Any] = {"order": "updated_at.desc"}
    if organization_id:
        params["organization_id"] = f"eq.{organization_id}"
    rows = await supabase.select(TABLE, params=params)
    return [t for t in (_from_row(row) for row in rows) if t is not None]


async def by_api_key(api_key: str) -> Tenant | None:
    """Find the company presenting this key.

    Keys are sealed in the row, so this cannot be a database lookup — the
    ciphertext differs every time the same key is written. The list is small
    (one row per customer number) and cached, so a scan is the right shape;
    if it ever stops being, the answer is a separate hash index, not storing
    the key in the clear.
    """
    candidate = (api_key or "").strip()
    if not candidate:
        return None
    for tenant in await list_all():
        if tenant.api_key and secrets.compare_digest(tenant.api_key, candidate):
            return tenant
    return None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def save(tenant: Tenant) -> Tenant:
    """Write the tenant, sealing its credentials on the way."""
    if tenant.line_id:
        # An overlay carries one number's credentials in the company's own
        # fields. Saving it would copy them onto the company row.
        raise RuntimeError("refusing to save a per-number tenant overlay")
    data = tenant.model_dump(
        by_alias=True, exclude={"phone_number_id", "line_id", "meta_phone_number_id"}
    )
    row = {
        "id": tenant.phone_number_id,
        "organization_id": tenant.organization_id or None,
        "data": _seal(data),
        "updated_at": _timestamp(),
    }
    await supabase.upsert(TABLE, row)
    invalidate(tenant.phone_number_id)
    log.info("saved tenant %s (%s)", tenant.phone_number_id, tenant.name)
    return tenant


async def delete(phone_number_id: str) -> None:
    await supabase.delete(TABLE, params={"id": f"eq.{phone_number_id}"})
    invalidate(phone_number_id)


def new_api_key() -> str:
    """A key the customer presents to read their own calls."""
    return f"ca_{secrets.token_urlsafe(32)}"


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
