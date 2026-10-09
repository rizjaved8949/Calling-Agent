"""
Storing a company's numbers, and turning one into something the engine runs on.

`as_tenant` is the bridge. Everything that places or answers a call —
`services/whatsapp.py`, `services/telephony.py`, the live session — was built
around one `Tenant` holding one set of credentials. Rather than thread a number
through all of it, a number is laid over its company: the result is the same
company (same id, same Drive, same knowledge) speaking with this number's
credentials. The overlay is marked, and `tenants.save` refuses to store one.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..db.supabase import supabase
from ..models.number import SECRET_FIELDS, NumberKind, PhoneNumber
from ..models.tenant import Tenant
from ..security.secret_box import SecretBoxError, decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)

TABLE = "voice_numbers"
_CACHE_TTL_SECONDS = 10.0
_by_meta_cache: dict[str, tuple[float, PhoneNumber | None]] = {}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seal(data: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(data)
    for field in SECRET_FIELDS:
        value = sealed.get(field)
        if isinstance(value, str) and value:
            sealed[field] = encrypt_secret(value)
    return sealed


def _open(data: dict[str, Any], number_id: str) -> dict[str, Any]:
    opened = dict(data)
    for field in SECRET_FIELDS:
        value = opened.get(field)
        if isinstance(value, str) and value:
            try:
                opened[field] = decrypt_secret(value)
            except SecretBoxError as exc:
                log.error("number %s: could not read %s — %s", number_id, field, exc)
                opened[field] = ""
    return opened


def _parse(row: dict[str, Any]) -> PhoneNumber | None:
    data = row.get("data")
    number_id = str(row.get("id") or "")
    if not isinstance(data, dict) or not number_id:
        return None
    payload = {**_open(data, number_id), "id": number_id}
    if row.get("tenant_id") and not payload.get("tenantId"):
        payload["tenantId"] = row["tenant_id"]
    try:
        return PhoneNumber.model_validate(payload)
    except Exception:  # noqa: BLE001 — one bad row must not break routing
        log.exception("number %s has a row this build cannot read", number_id)
        return None


async def save(number: PhoneNumber) -> PhoneNumber:
    data = number.model_dump(by_alias=True, mode="json", exclude={"id"})
    await supabase.upsert(
        TABLE,
        {
            "id": number.id,
            "tenant_id": number.tenant_id,
            "data": _seal(data),
            "updated_at": _timestamp(),
        },
    )
    _by_meta_cache.clear()
    return number


async def get(tenant_id: str, number_id: str) -> PhoneNumber | None:
    row = await supabase.select_one(
        TABLE, params={"id": f"eq.{number_id}", "tenant_id": f"eq.{tenant_id}"}
    )
    return _parse(row) if row else None


async def get_any(number_id: str) -> PhoneNumber | None:
    """By id alone — for a carrier webhook, whose URL names only the number."""
    row = await supabase.select_one(TABLE, params={"id": f"eq.{number_id}"})
    return _parse(row) if row else None


async def list_for(tenant_id: str) -> list[PhoneNumber]:
    rows = await supabase.select(
        TABLE, params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc"}
    )
    found = [n for n in (_parse(r) for r in rows) if n is not None]
    found.sort(key=lambda n: n.created_at)
    return found


async def list_all() -> list[PhoneNumber]:
    rows = await supabase.select(TABLE, params={"order": "updated_at.desc"})
    return [n for n in (_parse(r) for r in rows) if n is not None]


async def list_all_safe() -> list[PhoneNumber]:
    """Every number, or none if the table cannot be read.

    For the paths an inbound webhook or a ringing call sits on. A deployment
    whose migration has not been applied — or a database having a bad minute —
    must not turn into a 502 that makes Meta retry and eventually disable the
    subscription. Falling back to "this company has no numbers" degrades to the
    behaviour from before numbers existed, which is survivable; failing the
    webhook is not.
    """
    try:
        return await list_all()
    except Exception:  # noqa: BLE001 — see above
        log.exception("could not read %s; treating it as empty", TABLE)
        return []


async def get_safe(tenant_id: str, number_id: str) -> PhoneNumber | None:
    try:
        return await get(tenant_id, number_id)
    except Exception:  # noqa: BLE001
        log.exception("could not read number %s; treating it as missing", number_id)
        return None


async def delete(tenant_id: str, number_id: str) -> None:
    await supabase.delete(TABLE, params={"id": f"eq.{number_id}", "tenant_id": f"eq.{tenant_id}"})
    _by_meta_cache.clear()


async def by_meta_phone_number_id(meta_id: str) -> PhoneNumber | None:
    """The WhatsApp number a Meta webhook is about.

    Cached briefly: this sits on the path of every inbound message and call.
    """
    key = (meta_id or "").strip()
    if not key:
        return None
    cached = _by_meta_cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    found: PhoneNumber | None = None
    for number in await list_all_safe():
        if number.kind is NumberKind.WHATSAPP and _effective_meta_id(number) == key:
            found = number
            break
    _by_meta_cache[key] = (time.monotonic(), found)
    return found


async def by_phone(tenant_id: str, phone: str) -> PhoneNumber | None:
    digits = "".join(ch for ch in phone if ch.isdigit())
    for number in await list_for(tenant_id):
        if "".join(ch for ch in number.phone_number if ch.isdigit()) == digits:
            return number
    return None


# ---------------------------------------------------------------------------
# The overlay
# ---------------------------------------------------------------------------


def _effective_meta_id(number: PhoneNumber) -> str:
    if number.use_platform_credentials and not number.meta_phone_number_id:
        return settings.whatsapp_phone_number_id.strip()
    return number.meta_phone_number_id.strip()


def as_tenant(tenant: Tenant, number: PhoneNumber) -> Tenant:
    """This company, speaking through this number."""
    platform = number.use_platform_credentials and tenant.allow_platform_credentials

    def pick(own: str, env: str) -> str:
        own = (own or "").strip()
        return own or (env.strip() if platform else "")

    update: dict[str, Any] = {"line_id": number.id}
    if number.kind is NumberKind.WHATSAPP:
        update.update(
            meta_phone_number_id=pick(number.meta_phone_number_id, settings.whatsapp_phone_number_id),
            waba_id=pick(number.waba_id, settings.whatsapp_business_account_id),
            access_token=pick(number.access_token, settings.whatsapp_access_token),
            app_secret=pick(number.app_secret, settings.meta_app_secret),
            verify_token=pick(number.verify_token, settings.whatsapp_webhook_verify_token),
            display_phone_number=number.phone_number,
            auto_reply=number.auto_reply,
        )
    else:
        update.update(
            infobip_api_key=pick(number.infobip_api_key, settings.infobip_api_key),
            infobip_base_url=pick(number.infobip_base_url, settings.infobip_base_url),
            infobip_phone_number=number.phone_number,
            infobip_calls_configuration_id=number.infobip_calls_configuration_id,
        )
    return tenant.model_copy(update=update)
