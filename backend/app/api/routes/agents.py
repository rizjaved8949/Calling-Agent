"""
Agents, knowledge bases, and which answers on which number.

Three small CRUD surfaces and one rule worth stating: an inbound call arriving
on a number must be answered by exactly one agent, so enabling a second
inbound setup for a channel is refused rather than silently making the
outcome depend on row order.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, status

from ...errors import AppError, Conflict, NotFound
from ...models.agent import (
    Agent,
    AgentCreate,
    AgentUpdate,
    CallSetup,
    CallSetupCreate,
    CallSetupUpdate,
    Direction,
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)
from ...repositories import agents as repo
from ...repositories import knowledge as knowledge_repo
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------

@router.get("/knowledge-bases")
async def list_knowledge_bases(tenant: CurrentTenant) -> dict:
    bases = await repo.list_knowledge_bases(tenant.phone_number_id)
    documents = await knowledge_repo.listing(tenant.phone_number_id)
    # The count is what tells somebody choosing between bases which one is
    # actually populated, so it is worth the one extra read.
    counts: dict[str, int] = {}
    for doc in documents:
        counts[str(doc.get("knowledgeBaseId") or "")] = (
            counts.get(str(doc.get("knowledgeBaseId") or ""), 0) + 1
        )
    return {
        "knowledgeBases": [
            {**kb.public(), "documentCount": counts.get(kb.id, 0)} for kb in bases
        ],
        # Documents uploaded before any base existed, which every agent can
        # still read. Surfaced so they are not invisible.
        "unfiledCount": counts.get("", 0),
    }


@router.post("/knowledge-bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(tenant: CurrentTenant, payload: KnowledgeBaseCreate) -> dict:
    kb = KnowledgeBase(
        tenantId=tenant.phone_number_id,
        name=payload.name.strip(),
        purpose=payload.purpose.strip(),
        isDefault=payload.is_default,
    )
    if kb.is_default:
        await repo.clear_default(tenant.phone_number_id, except_id=kb.id)
    await repo.save_knowledge_base(kb)
    log.info("tenant %s: knowledge base %r created", tenant.phone_number_id, kb.name)
    return kb.public()


@router.patch("/knowledge-bases/{kb_id}")
async def update_knowledge_base(
    tenant: CurrentTenant, kb_id: str, payload: KnowledgeBaseUpdate
) -> dict:
    kb = await repo.get_knowledge_base(tenant.phone_number_id, kb_id)
    if kb is None:
        raise NotFound("Knowledge base")
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(kb, field, value)
    if kb.is_default:
        await repo.clear_default(tenant.phone_number_id, except_id=kb.id)
    await repo.save_knowledge_base(kb)
    return kb.public()


@router.delete("/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
async def delete_knowledge_base(tenant: CurrentTenant, kb_id: str) -> None:
    kb = await repo.get_knowledge_base(tenant.phone_number_id, kb_id)
    if kb is None:
        raise NotFound("Knowledge base")
    # Refused rather than cascaded: deleting a base that a live number answers
    # from would silently change what that number says, which is the kind of
    # change somebody should make deliberately.
    for setup in await repo.list_setups(tenant.phone_number_id):
        if setup.knowledge_base_id == kb_id and setup.enabled:
            raise Conflict(
                f"{setup.name!r} answers from this knowledge base. "
                "Point it somewhere else first."
            )
    for agent in await repo.list_agents(tenant.phone_number_id):
        if agent.knowledge_base_id == kb_id:
            agent.knowledge_base_id = ""
            await repo.save_agent(agent)
    await repo.delete_knowledge_base(tenant.phone_number_id, kb_id)
    log.info("tenant %s: knowledge base %s deleted", tenant.phone_number_id, kb_id)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@router.get("/agents")
async def list_agents(tenant: CurrentTenant) -> dict:
    return {"agents": [a.public() for a in await repo.list_agents(tenant.phone_number_id)]}


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(tenant: CurrentTenant, payload: AgentCreate) -> dict:
    if payload.knowledge_base_id:
        kb = await repo.get_knowledge_base(tenant.phone_number_id, payload.knowledge_base_id)
        if kb is None:
            raise AppError(422, "That knowledge base does not exist.", code="no_such_kb")
    agent = Agent(
        tenantId=tenant.phone_number_id,
        **payload.model_dump(by_alias=True, exclude_none=True),
    )
    await repo.save_agent(agent)
    log.info("tenant %s: agent %r created", tenant.phone_number_id, agent.name)
    return agent.public()


@router.get("/agents/{agent_id}")
async def get_agent(tenant: CurrentTenant, agent_id: str) -> dict:
    agent = await repo.get_agent(tenant.phone_number_id, agent_id)
    if agent is None:
        raise NotFound("Agent")
    return agent.public()


@router.patch("/agents/{agent_id}")
async def update_agent(tenant: CurrentTenant, agent_id: str, payload: AgentUpdate) -> dict:
    agent = await repo.get_agent(tenant.phone_number_id, agent_id)
    if agent is None:
        raise NotFound("Agent")
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if changes.get("knowledge_base_id"):
        kb = await repo.get_knowledge_base(tenant.phone_number_id, changes["knowledge_base_id"])
        if kb is None:
            raise AppError(422, "That knowledge base does not exist.", code="no_such_kb")
    for field, value in changes.items():
        setattr(agent, field, value)
    await repo.save_agent(agent)
    return agent.public()


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
async def delete_agent(tenant: CurrentTenant, agent_id: str) -> None:
    agent = await repo.get_agent(tenant.phone_number_id, agent_id)
    if agent is None:
        raise NotFound("Agent")
    for setup in await repo.list_setups(tenant.phone_number_id):
        if setup.agent_id == agent_id and setup.enabled:
            raise Conflict(
                f"{setup.name!r} is answered by this agent. Disable it first."
            )
    await repo.delete_agent(tenant.phone_number_id, agent_id)
    log.info("tenant %s: agent %s deleted", tenant.phone_number_id, agent_id)


# ---------------------------------------------------------------------------
# Call setups — which agent answers where
# ---------------------------------------------------------------------------

async def _refuse_second_inbound(tenant_id: str, setup: CallSetup) -> None:
    """One enabled inbound setup per channel.

    A call arriving on a number has to be answered by exactly one agent. Two
    enabled setups would make that depend on which row came back first, which
    is a bug that shows up as the wrong agent answering now and then.
    """
    if not setup.enabled or setup.direction is not Direction.INBOUND:
        return
    for other in await repo.list_setups(tenant_id):
        if (
            other.id != setup.id
            and other.enabled
            and other.direction is Direction.INBOUND
            and other.channel is setup.channel
        ):
            raise Conflict(
                f"{other.name!r} already answers incoming {setup.channel.value} "
                "calls. Disable it first, or edit it instead."
            )


@router.get("/call-setups")
async def list_setups(tenant: CurrentTenant) -> dict:
    return {"callSetups": [s.public() for s in await repo.list_setups(tenant.phone_number_id)]}


@router.post("/call-setups", status_code=status.HTTP_201_CREATED)
async def create_setup(tenant: CurrentTenant, payload: CallSetupCreate) -> dict:
    agent = await repo.get_agent(tenant.phone_number_id, payload.agent_id)
    if agent is None:
        raise AppError(422, "That agent does not exist.", code="no_such_agent")
    if payload.knowledge_base_id:
        kb = await repo.get_knowledge_base(tenant.phone_number_id, payload.knowledge_base_id)
        if kb is None:
            raise AppError(422, "That knowledge base does not exist.", code="no_such_kb")
    setup = CallSetup(
        tenantId=tenant.phone_number_id,
        **payload.model_dump(by_alias=True, exclude_none=True),
    )
    await _refuse_second_inbound(tenant.phone_number_id, setup)
    await repo.save_setup(setup)
    log.info("tenant %s: call setup %r created", tenant.phone_number_id, setup.name)
    return setup.public()


@router.patch("/call-setups/{setup_id}")
async def update_setup(
    tenant: CurrentTenant, setup_id: str, payload: CallSetupUpdate
) -> dict:
    setup = await repo.get_setup(tenant.phone_number_id, setup_id)
    if setup is None:
        raise NotFound("Call setup")
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if changes.get("agent_id"):
        if await repo.get_agent(tenant.phone_number_id, changes["agent_id"]) is None:
            raise AppError(422, "That agent does not exist.", code="no_such_agent")
    if changes.get("knowledge_base_id"):
        if await repo.get_knowledge_base(
            tenant.phone_number_id, changes["knowledge_base_id"]
        ) is None:
            raise AppError(422, "That knowledge base does not exist.", code="no_such_kb")
    for field, value in changes.items():
        setattr(setup, field, value)
    await _refuse_second_inbound(tenant.phone_number_id, setup)
    await repo.save_setup(setup)
    return setup.public()


@router.delete("/call-setups/{setup_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
async def delete_setup(tenant: CurrentTenant, setup_id: str) -> None:
    if await repo.get_setup(tenant.phone_number_id, setup_id) is None:
        raise NotFound("Call setup")
    await repo.delete_setup(tenant.phone_number_id, setup_id)
