"""
Agents, knowledge bases, and the routing that picks between them.

A company used to have exactly one of each: one persona on its tenant row, one
pile of documents. These models are what let it have several — a support agent
answering the main line from the support handbook, a sales agent working an
outbound list from the price list, on the same numbers.

Field names match the shapes the frontend already models (`lib/types.ts`), so
a screen built against fixtures can be pointed at the API without a mapping
layer in between.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .call import Channel


def _id() -> str:
    return uuid.uuid4().hex


class KnowledgeBase(BaseModel):
    """A named set of documents an agent answers from."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=_id)
    tenant_id: str = Field(default="", alias="tenantId")
    name: str = ""
    # What this one is for, in the company's own words. Shown when choosing
    # between several, which is the moment the difference matters.
    purpose: str = ""
    # Exactly one per company. The fallback when nothing names a base.
    is_default: bool = Field(default=False, alias="isDefault")
    created_at: float = Field(default_factory=time.time, alias="createdAt")

    def public(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(default="", max_length=500)
    is_default: bool = Field(default=False, alias="isDefault")


class KnowledgeBaseUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    purpose: str | None = Field(default=None, max_length=500)
    is_default: bool | None = Field(default=None, alias="isDefault")


class AgentMode(str, Enum):
    """What the agent is for. A number's inbound slot takes an inbound or
    both agent; its outbound slot takes an outbound or both agent."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class AgentStatus(str, Enum):
    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"


class Agent(BaseModel):
    """One configured way of answering: a voice, a persona, and a knowledge base.

    The persona fields are kept on the agent rather than in a separate record.
    The frontend models them apart, but a persona has never been shared between
    two agents in practice, and one table that holds the whole answer is easier
    to reason about than two that must be read together.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=_id)
    tenant_id: str = Field(default="", alias="tenantId")
    name: str = ""
    status: AgentStatus = AgentStatus.DRAFT
    mode: AgentMode = AgentMode.BOTH

    # ---- What it says ----------------------------------------------------
    greeting: str = ""
    role_description: str = Field(default="", alias="roleDescription")
    language: str = ""
    tone_notes: str = Field(default="", alias="toneNotes")
    escalation_rules: str = Field(default="", alias="escalationRules")
    tts_voice: str = Field(default="", alias="ttsVoice")

    # ---- What it knows ---------------------------------------------------
    # Blank means "everything this company has uploaded", which is how a
    # company that never splits its documents keeps working.
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")

    created_at: float = Field(default_factory=time.time, alias="createdAt")

    def public(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


class AgentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    greeting: str = Field(default="", max_length=2000)
    role_description: str = Field(default="", alias="roleDescription", max_length=8000)
    language: str = ""
    tone_notes: str = Field(default="", alias="toneNotes", max_length=2000)
    escalation_rules: str = Field(default="", alias="escalationRules", max_length=2000)
    tts_voice: str = Field(default="", alias="ttsVoice")
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    status: AgentStatus = AgentStatus.DRAFT
    mode: AgentMode = AgentMode.BOTH


class AgentUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    greeting: str | None = Field(default=None, max_length=2000)
    role_description: str | None = Field(default=None, alias="roleDescription", max_length=8000)
    language: str | None = None
    tone_notes: str | None = Field(default=None, alias="toneNotes", max_length=2000)
    escalation_rules: str | None = Field(default=None, alias="escalationRules", max_length=2000)
    tts_voice: str | None = Field(default=None, alias="ttsVoice")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    status: AgentStatus | None = None
    mode: AgentMode | None = None


class Direction(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class CallSetup(BaseModel):
    """Which agent answers on which number, in which direction.

    One *enabled* inbound setup per channel, because a call arriving on a
    number has to be answered by exactly one agent and "both" is not an
    answer. As many outbound setups as a company likes — those are chosen
    deliberately when placing a call or starting a campaign, so several can
    sit side by side without ambiguity.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=_id)
    tenant_id: str = Field(default="", alias="tenantId")
    name: str = ""
    channel: Channel = Channel.PHONE
    direction: Direction = Direction.INBOUND
    agent_id: str = Field(default="", alias="agentId")
    # Overrides the agent's own base when set. The narrower choice wins,
    # because it was made later and about this specific line.
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    enabled: bool = True
    created_at: float = Field(default_factory=time.time, alias="createdAt")

    def public(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


class CallSetupCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    channel: Channel = Channel.PHONE
    direction: Direction = Direction.INBOUND
    agent_id: str = Field(alias="agentId", min_length=1)
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    enabled: bool = True


class CallSetupUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    channel: Channel | None = None
    direction: Direction | None = None
    agent_id: str | None = Field(default=None, alias="agentId")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    enabled: bool | None = None
