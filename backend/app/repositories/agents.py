"""
Storing agents, knowledge bases and call setups.

Three tables, one shape — `(id, tenant_id, data jsonb)`, same as every sibling
table — so this is one generic pair of read/write helpers rather than three
near-identical copies of the same twelve lines.

Every read is scoped by `tenant_id` in the query itself, not filtered after
the fact, which is what makes a cross-tenant read impossible rather than
merely unlikely.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from ..db.supabase import supabase
from ..models.agent import Agent, CallSetup, Direction, KnowledgeBase
from ..models.call import Channel

log = logging.getLogger(__name__)

KNOWLEDGE_BASES = "voice_knowledge_bases"
AGENTS = "voice_agents"
CALL_SETUPS = "voice_call_setups"

T = TypeVar("T", bound=BaseModel)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(model: type[T], row: dict[str, Any]) -> T | None:
    data = row.get("data")
    if not isinstance(data, dict):
        return None
    payload = {**data, "id": str(row.get("id") or data.get("id") or "")}
    if row.get("tenant_id") and not payload.get("tenantId"):
        payload["tenantId"] = row["tenant_id"]
    try:
        return model.model_validate(payload)
    except Exception:  # noqa: BLE001 — one unreadable row must not break the list
        log.exception("%s %s has a row this build cannot read", model.__name__, row.get("id"))
        return None


async def _save(table: str, record: Any) -> Any:
    await supabase.upsert(
        table,
        {
            "id": record.id,
            "tenant_id": record.tenant_id or None,
            "data": record.model_dump(by_alias=True, mode="json", exclude={"id"}),
            "updated_at": _timestamp(),
        },
    )
    return record


async def _get(table: str, model: type[T], tenant_id: str, record_id: str) -> T | None:
    row = await supabase.select_one(
        table, params={"id": f"eq.{record_id}", "tenant_id": f"eq.{tenant_id}"}
    )
    return _parse(model, row) if row else None


async def _listing(table: str, model: type[T], tenant_id: str, limit: int = 200) -> list[T]:
    rows = await supabase.select(
        table,
        params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc", "limit": limit},
    )
    return [x for x in (_parse(model, row) for row in rows) if x is not None]


async def _delete(table: str, tenant_id: str, record_id: str) -> None:
    await supabase.delete(
        table, params={"id": f"eq.{record_id}", "tenant_id": f"eq.{tenant_id}"}
    )


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------

async def save_knowledge_base(kb: KnowledgeBase) -> KnowledgeBase:
    return await _save(KNOWLEDGE_BASES, kb)


async def get_knowledge_base(tenant_id: str, kb_id: str) -> KnowledgeBase | None:
    return await _get(KNOWLEDGE_BASES, KnowledgeBase, tenant_id, kb_id)


async def list_knowledge_bases(tenant_id: str) -> list[KnowledgeBase]:
    return await _listing(KNOWLEDGE_BASES, KnowledgeBase, tenant_id)


async def delete_knowledge_base(tenant_id: str, kb_id: str) -> None:
    await _delete(KNOWLEDGE_BASES, tenant_id, kb_id)


async def default_knowledge_base(tenant_id: str) -> KnowledgeBase | None:
    for kb in await list_knowledge_bases(tenant_id):
        if kb.is_default:
            return kb
    return None


async def clear_default(tenant_id: str, except_id: str = "") -> None:
    """Leave at most one base marked default.

    Called before marking a new one, rather than trusting callers to unset the
    old one — two defaults means the fallback is whichever row sorted first,
    which is a bug that only shows up as the agent occasionally answering from
    the wrong documents.
    """
    for kb in await list_knowledge_bases(tenant_id):
        if kb.is_default and kb.id != except_id:
            kb.is_default = False
            await save_knowledge_base(kb)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

async def save_agent(agent: Agent) -> Agent:
    return await _save(AGENTS, agent)


async def get_agent(tenant_id: str, agent_id: str) -> Agent | None:
    return await _get(AGENTS, Agent, tenant_id, agent_id)


async def list_agents(tenant_id: str) -> list[Agent]:
    return await _listing(AGENTS, Agent, tenant_id)


async def delete_agent(tenant_id: str, agent_id: str) -> None:
    await _delete(AGENTS, tenant_id, agent_id)


# ---------------------------------------------------------------------------
# Call setups
# ---------------------------------------------------------------------------

async def save_setup(setup: CallSetup) -> CallSetup:
    return await _save(CALL_SETUPS, setup)


async def get_setup(tenant_id: str, setup_id: str) -> CallSetup | None:
    return await _get(CALL_SETUPS, CallSetup, tenant_id, setup_id)


async def list_setups(tenant_id: str) -> list[CallSetup]:
    return await _listing(CALL_SETUPS, CallSetup, tenant_id)


async def delete_setup(tenant_id: str, setup_id: str) -> None:
    await _delete(CALL_SETUPS, tenant_id, setup_id)


async def active_inbound_setup(
    tenant_id: str, channel: Channel
) -> CallSetup | None:
    """The one setup that answers calls arriving on this channel.

    First enabled match wins, and the API refuses to enable a second one for
    the same channel, so "first" and "only" are the same thing here.
    """
    for setup in await list_setups(tenant_id):
        if (
            setup.enabled
            and setup.direction is Direction.INBOUND
            and setup.channel is channel
        ):
            return setup
    return None
