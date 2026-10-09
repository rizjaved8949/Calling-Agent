"""
Storing campaigns, in the same shape as everything else.

`(id, tenant_id, data jsonb)` like the sibling tables, so the migration is one
`create table` and a field can be added without another.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..db.supabase import supabase
from ..models.campaign import Campaign

log = logging.getLogger(__name__)

TABLE = "voice_campaigns"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_row(row: dict[str, Any]) -> Campaign | None:
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    payload = {**data, "id": str(row.get("id") or data.get("id") or "")}
    if row.get("tenant_id") and not payload.get("tenantId"):
        payload["tenantId"] = row["tenant_id"]
    try:
        return Campaign.model_validate(payload)
    except Exception:  # noqa: BLE001 — one unreadable row must not break the list
        log.exception("campaign %s has a row this build cannot read", row.get("id"))
        return None


async def save(campaign: Campaign) -> Campaign:
    await supabase.upsert(
        TABLE,
        {
            "id": campaign.id,
            "tenant_id": campaign.tenant_id or None,
            "data": campaign.model_dump(by_alias=True, mode="json", exclude={"id"}),
            "updated_at": _timestamp(),
        },
    )
    return campaign


async def get(tenant_id: str, campaign_id: str) -> Campaign | None:
    row = await supabase.select_one(
        TABLE, params={"id": f"eq.{campaign_id}", "tenant_id": f"eq.{tenant_id}"}
    )
    return _from_row(row) if row else None


async def listing(tenant_id: str, limit: int = 50) -> list[Campaign]:
    rows = await supabase.select(
        TABLE,
        params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc", "limit": limit},
    )
    return [c for c in (_from_row(row) for row in rows) if c is not None]


async def delete(tenant_id: str, campaign_id: str) -> None:
    await supabase.delete(
        TABLE, params={"id": f"eq.{campaign_id}", "tenant_id": f"eq.{tenant_id}"}
    )


async def running(tenant_id: str) -> list[Campaign]:
    """Campaigns that should be working through their list right now."""
    return [c for c in await listing(tenant_id) if c.status.value == "RUNNING"]
