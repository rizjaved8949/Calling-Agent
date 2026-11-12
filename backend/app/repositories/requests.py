"""
Storing the requests callers make, and numbering them.

The reference is the part worth reading. `{PREFIX}-{YYYYMMDD}-{NN}` with a
counter per company, per day, per prefix, in the company's own day: a
complaint logged at one in the morning in Lahore belongs to that day, not to
UTC's yesterday. Generated here, under a lock, so two calls ending together
cannot be handed the same number; the model never makes one up.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from ..db.supabase import supabase
from ..models.call import Call
from ..models.request import PREFIXES, RecordedRequest, RequestStatus, RequestType
from ..models.tenant import Tenant

log = logging.getLogger(__name__)

TABLE = "voice_requests"

#: The zone a company's day runs in when it has not said: by its country code.
COUNTRY_ZONES = {
    "92": "Asia/Karachi", "91": "Asia/Kolkata", "971": "Asia/Dubai",
    "966": "Asia/Riyadh", "974": "Asia/Qatar", "965": "Asia/Kuwait",
    "968": "Asia/Muscat", "973": "Asia/Bahrain", "44": "Europe/London",
    "65": "Asia/Singapore", "60": "Asia/Kuala_Lumpur", "880": "Asia/Dhaka",
    "61": "Australia/Sydney", "90": "Europe/Istanbul", "20": "Africa/Cairo",
}
#: Fixed offsets for when the zone database is missing (a bare Windows host).
_FIXED_OFFSETS = {
    "Asia/Karachi": 5, "Asia/Kolkata": 5.5, "Asia/Dubai": 4, "Asia/Riyadh": 3,
    "Asia/Qatar": 3, "Asia/Kuwait": 3, "Asia/Muscat": 4, "Asia/Bahrain": 3,
    "Asia/Singapore": 8, "Asia/Kuala_Lumpur": 8, "Asia/Dhaka": 6,
    "Europe/Istanbul": 3, "Africa/Cairo": 2,
}


def zone_for(tenant: Tenant) -> tzinfo:
    """The company's time zone: its own setting, else its country's, else UTC."""
    from ..config import settings

    name = (tenant.timezone or "").strip()
    if not name:
        country = (tenant.default_country_code or settings.default_country_code).strip()
        name = COUNTRY_ZONES.get(country.lstrip("+"), "")
    if not name:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — no tz database on this host
        hours = _FIXED_OFFSETS.get(name)
        return timezone(timedelta(hours=hours)) if hours is not None else timezone.utc


def local_day(tenant: Tenant, now: float | None = None) -> str:
    """Today as YYYYMMDD where the company is."""
    moment = datetime.fromtimestamp(now if now is not None else datetime.now().timestamp(),
                                    zone_for(tenant))
    return moment.strftime("%Y%m%d")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(row: dict[str, Any]) -> RecordedRequest | None:
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    payload = {**data, "id": str(row.get("id") or data.get("id") or "")}
    if row.get("tenant_id") and not payload.get("tenantId"):
        payload["tenantId"] = row["tenant_id"]
    try:
        return RecordedRequest.model_validate(payload)
    except Exception:  # noqa: BLE001 — one unreadable row must not break the list
        log.exception("request %s has a row this build cannot read", row.get("id"))
        return None


async def save(record: RecordedRequest) -> RecordedRequest:
    await supabase.upsert(
        TABLE,
        {
            "id": record.id,
            "tenant_id": record.tenant_id or None,
            "data": record.model_dump(by_alias=True, mode="json", exclude={"id"}),
            "updated_at": _timestamp(),
        },
    )
    return record


async def get(tenant_id: str, request_id: str) -> RecordedRequest | None:
    row = await supabase.select_one(
        TABLE, params={"id": f"eq.{request_id}", "tenant_id": f"eq.{tenant_id}"}
    )
    return _parse(row) if row else None


async def list_for(
    tenant_id: str,
    *,
    limit: int = 100,
    status: RequestStatus | None = None,
    kind: RequestType | None = None,
) -> list[RecordedRequest]:
    params: dict[str, Any] = {
        "tenant_id": f"eq.{tenant_id}",
        "order": "updated_at.desc",
        "limit": max(1, min(limit, 5000)),
    }
    if status is not None:
        params["data->>status"] = f"eq.{status.value}"
    if kind is not None:
        params["data->>type"] = f"eq.{kind.value}"
    rows = await supabase.select(TABLE, params=params)
    return [r for r in (_parse(row) for row in rows) if r is not None]


async def count_on(tenant_id: str, prefix: str, day: str) -> int:
    rows = await supabase.select(
        TABLE,
        params={"tenant_id": f"eq.{tenant_id}", "data->>referenceDay": f"eq.{day}",
                "data->>prefix": f"eq.{prefix}"},
    )
    return len(rows)


_locks: dict[str, asyncio.Lock] = {}


async def create(tenant: Tenant, call: Call | None, kind: RequestType,
                 values: dict[str, Any]) -> RecordedRequest:
    """Record a request and give it the next reference of its kind today.

    Under a per-company lock so two calls ending at once cannot be given the
    same number; the counter itself is the count of today's rows with this
    prefix, so it survives a restart.
    """
    prefix = PREFIXES[kind]
    day = local_day(tenant)
    lock = _locks.setdefault(tenant.phone_number_id, asyncio.Lock())
    async with lock:
        sequence = await count_on(tenant.phone_number_id, prefix, day) + 1
        record = RecordedRequest(
            tenantId=tenant.phone_number_id,
            callId=call.id if call else "",
            reference=f"{prefix}-{day}-{sequence:02d}",
            prefix=prefix,
            referenceDay=day,
            type=kind,
            callerNumber=call.counterparty if call else "",
            **{k: v for k, v in values.items() if k in _STORED},
        )
        await save(record)
    log.info("tenant %s: recorded %s (%s) on call %s", tenant.phone_number_id,
             record.reference, kind.value, record.call_id or "-")
    return record


_STORED = {
    "name", "mobile", "city", "location", "preferred_times", "vehicle_model",
    "registration", "mileage", "details", "reply_by",
}
