"""
Which agent answers this call, and what it is allowed to know.

Every call — inbound or outbound, phone or WhatsApp — walks the same chain to
find its answer, highest precedence first:

1. **the call setup** matched for that number and direction (`setup`)
2. **a knowledge base named on the request itself** (`explicit`) — a campaign
   or a single outbound call saying which one to use
3. **the agent's own default knowledge base** (`agent`)
4. **everything the company has uploaded** (`company`)

Step 4 is why this is safe to add to a running system: a company that never
creates an agent or a knowledge base resolves straight to the bottom of the
chain and behaves exactly as it did before any of this existed.

The resolution is recorded on the call (`knowledgeBaseId`, `resolvedBy`) so
"why did it answer that?" has an answer that does not require re-deriving
this logic by hand weeks later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..models.agent import Agent, Direction
from ..models.call import Channel
from ..models.tenant import Tenant
from ..repositories import agents as agent_repo
from ..repositories import knowledge as knowledge_repo

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Resolved:
    """What answers this call, and why that was chosen."""

    agent: Agent | None
    knowledge_base_id: str
    knowledge_base_name: str
    resolved_by: str  # setup | explicit | agent | company

    @property
    def agent_id(self) -> str:
        return self.agent.id if self.agent else ""


async def resolve(
    tenant: Tenant,
    *,
    channel: Channel,
    direction: Direction,
    knowledge_base_id: str = "",
    agent_id: str = "",
    line_id: str = "",
) -> Resolved:
    """Walk the chain. Never raises: an unanswerable call is worse than a
    call answered from the company's whole pile."""
    tenant_id = tenant.phone_number_id
    agent: Agent | None = None
    kb_id = ""
    resolved_by = "company"

    # 1. An explicitly named agent (a campaign's, or one named on the request)
    #    outranks whatever the number's setup says, because somebody chose it
    #    for this call specifically.
    if agent_id:
        agent = await agent_repo.get_agent(tenant_id, agent_id)
        if agent is None:
            log.warning("tenant %s: agent %s was named but does not exist", tenant_id, agent_id)

    # 2. The agent assigned to the number the call is on, for this direction.
    line_id = line_id or tenant.line_id
    if agent is None and line_id:
        from ..repositories import numbers as number_repo

        number = await number_repo.get_safe(tenant_id, line_id)
        if number is not None:
            assigned = (
                number.inbound_agent_id if direction is Direction.INBOUND
                else number.outbound_agent_id
            )
            if assigned:
                agent = await agent_repo.get_agent(tenant_id, assigned)
                if agent is not None:
                    resolved_by = "number"

    # 3. Otherwise the legacy call setup for this channel and direction.
    setup = None
    if agent is None and direction is Direction.INBOUND:
        setup = await agent_repo.active_inbound_setup(tenant_id, channel)
        if setup is not None:
            agent = await agent_repo.get_agent(tenant_id, setup.agent_id)
            if setup.knowledge_base_id:
                kb_id, resolved_by = setup.knowledge_base_id, "setup"

    # 3. A knowledge base named on the request beats the agent's own default,
    #    but not one the setup pinned to this line.
    if not kb_id and knowledge_base_id:
        kb_id, resolved_by = knowledge_base_id, "explicit"

    # 4. The agent's own knowledge base.
    if not kb_id and agent is not None and agent.knowledge_base_id:
        kb_id, resolved_by = agent.knowledge_base_id, "agent"

    # 5. The company's default base, if it marked one.
    if not kb_id:
        default = await agent_repo.default_knowledge_base(tenant_id)
        if default is not None:
            kb_id, resolved_by = default.id, "company"

    name = ""
    if kb_id:
        kb = await agent_repo.get_knowledge_base(tenant_id, kb_id)
        if kb is None:
            # Pointed at a base that has since been deleted. Fall back rather
            # than answer from nothing, and say so — a stale reference is a
            # configuration problem somebody should fix.
            log.warning("tenant %s: knowledge base %s no longer exists", tenant_id, kb_id)
            kb_id, resolved_by = "", "company"
        else:
            name = kb.name

    return Resolved(
        agent=agent,
        knowledge_base_id=kb_id,
        knowledge_base_name=name,
        resolved_by=resolved_by,
    )


async def context_for(tenant: Tenant, resolved: Resolved) -> str:
    """The documents behind a resolution, as prompt text."""
    return await knowledge_repo.context_for(
        tenant.phone_number_id, knowledge_base_id=resolved.knowledge_base_id
    )


def persona_for(tenant: Tenant, resolved: Resolved) -> dict[str, str]:
    """The agent's voice, falling back to the company's own settings.

    A company that has not built an agent yet still has a greeting, a persona
    and a language on its tenant row — the single-agent arrangement this
    replaced. Those stay the fallback rather than being migrated, so nothing
    has to be rewritten for a company that never creates an agent.
    """
    agent = resolved.agent
    if agent is None:
        return {
            "greeting": tenant.agent_greeting,
            "persona": tenant.persona,
            "language": tenant.language,
            "ttsVoice": tenant.tts_voice,
        }
    return {
        "greeting": agent.greeting or tenant.agent_greeting,
        "persona": agent.role_description or tenant.persona,
        "language": agent.language or tenant.language,
        "ttsVoice": agent.tts_voice or tenant.tts_voice,
    }
