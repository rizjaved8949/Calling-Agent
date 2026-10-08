"""
The call log, and the message log beside it.

Both tables share the `(id, tenant_id, data jsonb)` shape, so the two
repositories are nearly the same code — kept separate anyway, because the
queries they grow are not: calls are filtered by status and date, messages by
counterparty and delivery state.

Every read is scoped by tenant_id. That is the isolation boundary: a bug that
drops the filter leaks one customer's calls to another, so there is no function
here that reads rows without one.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..db.supabase import supabase
from ..models.call import Call, CallStatus, Message, RecordingState

log = logging.getLogger(__name__)

CALLS_TABLE = "voice_calls"
MESSAGES_TABLE = "voice_messages"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


def _call_from_row(row: dict[str, Any]) -> Call | None:
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    payload = {**data, "id": str(row.get("id") or data.get("id") or "")}
    if row.get("tenant_id") and not payload.get("tenantId"):
        payload["tenantId"] = row["tenant_id"]
    try:
        return Call.model_validate(payload)
    except Exception:  # noqa: BLE001 — one unreadable row must not break the list
        log.exception("call %s has a row this build cannot read", row.get("id"))
        return None


async def save_call(call: Call) -> Call:
    row = {
        "id": call.id,
        "tenant_id": call.tenant_id or None,
        "data": call.model_dump(by_alias=True, mode="json", exclude={"id"}),
        "updated_at": _timestamp(),
    }
    await supabase.upsert(CALLS_TABLE, row)
    return call


async def get_call(tenant_id: str, call_id: str) -> Call | None:
    row = await supabase.select_one(
        CALLS_TABLE, params={"id": f"eq.{call_id}", "tenant_id": f"eq.{tenant_id}"}
    )
    return _call_from_row(row) if row else None


async def find_by_provider_id(tenant_id: str, provider_call_id: str) -> Call | None:
    """Match a webhook back to the call it belongs to.

    The provider id is indexed as a jsonb expression, so this is a lookup
    rather than a scan — see the migration.
    """
    if not provider_call_id:
        return None
    row = await supabase.select_one(
        CALLS_TABLE,
        params={
            "tenant_id": f"eq.{tenant_id}",
            "data->>providerCallId": f"eq.{provider_call_id}",
        },
    )
    return _call_from_row(row) if row else None


async def list_calls(
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: CallStatus | None = None,
    channel: str | None = None,
    since: float | None = None,
) -> list[Call]:
    params: dict[str, Any] = {
        "tenant_id": f"eq.{tenant_id}",
        "order": "updated_at.desc",
        "limit": max(1, min(limit, 500)),
        "offset": max(0, offset),
    }
    if status is not None:
        params["data->>status"] = f"eq.{status.value}"
    if channel:
        params["data->>channel"] = f"eq.{channel}"
    if since is not None:
        # jsonb numbers compare as text unless cast, which would sort "9" after
        # "10". Comparing the timestamp column instead is both correct and
        # indexed.
        params["updated_at"] = f"gte.{datetime.fromtimestamp(since, timezone.utc).isoformat()}"
    rows = await supabase.select(CALLS_TABLE, params=params)
    return [c for c in (_call_from_row(row) for row in rows) if c is not None]


async def delete_call(tenant_id: str, call_id: str) -> None:
    await supabase.delete(
        CALLS_TABLE, params={"id": f"eq.{call_id}", "tenant_id": f"eq.{tenant_id}"}
    )


async def update_call(tenant_id: str, call_id: str, **fields: Any) -> Call | None:
    """Read, apply, write.

    Not an atomic update: PostgREST cannot merge into a jsonb column without a
    stored function, and a call row is written by one worker at a time in
    practice. If two webhooks for the same call ever do race, the later write
    wins — which is the order they describe anyway.
    """
    call = await get_call(tenant_id, call_id)
    if call is None:
        return None
    for key, value in fields.items():
        if hasattr(call, key):
            setattr(call, key, value)
    return await save_call(call)


async def call_stats(tenant_id: str) -> dict[str, Any]:
    """Counts for the dashboard header."""
    total = await supabase.count(CALLS_TABLE, params={"tenant_id": f"eq.{tenant_id}"})
    recent = await list_calls(tenant_id, limit=500)
    now = time.time()
    day = [c for c in recent if now - c.started_at < 86_400]
    answered = [c for c in day if c.answered_at]
    return {
        "total": total,
        "last24h": len(day),
        "answered24h": len(answered),
        "handedOff24h": sum(1 for c in day if c.status == CallStatus.HANDED_OFF),
        "failed24h": sum(1 for c in day if c.status == CallStatus.FAILED),
        "recordings24h": sum(
            1 for c in day if c.recording_state == RecordingState.READY
        ),
        "averageSeconds": (
            round(sum(c.duration_seconds for c in answered) / len(answered))
            if answered
            else 0
        ),
    }


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def _message_from_row(row: dict[str, Any]) -> Message | None:
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    payload = {**data, "id": str(row.get("id") or data.get("id") or "")}
    if row.get("tenant_id") and not payload.get("tenantId"):
        payload["tenantId"] = row["tenant_id"]
    try:
        return Message.model_validate(payload)
    except Exception:  # noqa: BLE001
        log.exception("message %s has a row this build cannot read", row.get("id"))
        return None


async def save_message(message: Message) -> Message:
    row = {
        "id": message.id,
        "tenant_id": message.tenant_id or None,
        "data": message.model_dump(by_alias=True, mode="json", exclude={"id"}),
        "updated_at": _timestamp(),
    }
    await supabase.upsert(MESSAGES_TABLE, row)
    return message


async def list_messages(
    tenant_id: str, *, counterparty: str | None = None, limit: int = 100
) -> list[Message]:
    params: dict[str, Any] = {
        "tenant_id": f"eq.{tenant_id}",
        "order": "updated_at.desc",
        "limit": max(1, min(limit, 500)),
    }
    if counterparty:
        params["data->>counterparty"] = f"eq.{counterparty}"
    rows = await supabase.select(MESSAGES_TABLE, params=params)
    return [m for m in (_message_from_row(row) for row in rows) if m is not None]


async def find_message_by_provider_id(
    tenant_id: str, provider_message_id: str
) -> Message | None:
    if not provider_message_id:
        return None
    row = await supabase.select_one(
        MESSAGES_TABLE,
        params={
            "tenant_id": f"eq.{tenant_id}",
            "data->>providerMessageId": f"eq.{provider_message_id}",
        },
    )
    return _message_from_row(row) if row else None
