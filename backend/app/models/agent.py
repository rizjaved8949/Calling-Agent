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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """What the agent is for, as far as *calls* go. A number's inbound slot
    takes an inbound or both agent; its outbound slot takes an outbound or
    both agent.

    Kept because every stored row has one and the number slots are written in
    its terms. `AgentChannel` is the fuller answer and the one to read when
    deciding what an agent may be used for; this stays in step with it.
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class AgentChannel(str, Enum):
    """Each separate job an agent can be given.

    Calls were the only thing an agent did, so "inbound / outbound / both" was
    the whole question. Answering WhatsApp messages is a third job and not a
    direction — an agent can reply to messages without ever taking a call —
    so it does not fit on that axis and gets its own.
    """

    INBOUND_CALL = "inbound_call"
    OUTBOUND_CALL = "outbound_call"
    WHATSAPP_MESSAGE = "whatsapp_message"


#: The call channels, in the order a form should offer them.
CALL_CHANNELS = (AgentChannel.INBOUND_CALL, AgentChannel.OUTBOUND_CALL)


def channels_from_mode(mode: AgentMode) -> list[AgentChannel]:
    """What an agent stored before channels existed is able to do.

    Messages are deliberately not included: an agent built when the only
    choice was a call direction never had anyone decide it should answer
    WhatsApp, and switching that on for every existing agent on upgrade would
    be this release answering a company's messages without being asked.
    """
    if mode is AgentMode.INBOUND:
        return [AgentChannel.INBOUND_CALL]
    if mode is AgentMode.OUTBOUND:
        return [AgentChannel.OUTBOUND_CALL]
    return [AgentChannel.INBOUND_CALL, AgentChannel.OUTBOUND_CALL]


def mode_from_channels(channels: list[AgentChannel]) -> AgentMode:
    """The call direction implied by a set of channels.

    A messages-only agent has no call direction at all. It reports `inbound`,
    which is the harmless legacy value — nothing picks an agent for a call by
    mode any more, it is filtered on channels, so this only has to be a value
    the column accepts.
    """
    takes = AgentChannel.INBOUND_CALL in channels
    makes = AgentChannel.OUTBOUND_CALL in channels
    if takes and makes:
        return AgentMode.BOTH
    if makes:
        return AgentMode.OUTBOUND
    return AgentMode.INBOUND


class SpeakingPace(str, Enum):
    """How fast the agent talks.

    A phone line is not a podcast: the same words at the wrong speed are the
    difference between being understood and being asked to repeat. Slow suits
    an older or less confident caller and anything with numbers in it; brisk
    suits a busy caller who already knows what they want.
    """

    SLOW = "slow"
    NATURAL = "natural"
    BRISK = "brisk"


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
    #: Every job this agent may be given. Empty on a row written before this
    #: existed, which `_fill_channels` reads from `mode` instead.
    channels: list[AgentChannel] = Field(default_factory=list)

    # ---- What it says ----------------------------------------------------
    greeting: str = ""
    role_description: str = Field(default="", alias="roleDescription")
    language: str = ""
    tone_notes: str = Field(default="", alias="toneNotes")
    escalation_rules: str = Field(default="", alias="escalationRules")
    tts_voice: str = Field(default="", alias="ttsVoice")
    speaking_pace: SpeakingPace = Field(default=SpeakingPace.NATURAL, alias="speakingPace")

    # ---- The exact instructions -----------------------------------------
    # Each one overrides a block of the standard prompt; empty means "use the
    # standard wording", which is what every agent does until somebody edits
    # one. See `services/agent/prompt.py` for the blocks and the defaults.
    prompt_blocks: dict[str, str] = Field(default_factory=dict, alias="promptBlocks")
    # Appended at the very end, so it wins any disagreement with the blocks
    # above — which is what somebody writing their own rule is asking for.
    extra_rules: str = Field(default="", alias="extraRules")
    # Replaces the assembled instructions entirely. For somebody who would
    # rather write the whole thing than edit ours.
    prompt_override: str = Field(default="", alias="promptOverride")

    # ---- What it knows ---------------------------------------------------
    # Blank means "everything this company has uploaded", which is how a
    # company that never splits its documents keeps working.
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")

    created_at: float = Field(default_factory=time.time, alias="createdAt")

    @model_validator(mode="after")
    def _fill_channels(self) -> "Agent":
        """Keep `channels` and `mode` saying the same thing.

        A row stored before channels existed has none, and its `mode` is the
        only record of what it was for. A row that has them is the newer
        truth, and `mode` is brought into line behind it.
        """
        if not self.channels:
            object.__setattr__(self, "channels", channels_from_mode(self.mode))
        else:
            # De-duplicate and put them in a stable order, so two agents with
            # the same abilities compare equal and a form cannot store
            # ["outbound_call", "outbound_call"].
            ordered = [c for c in AgentChannel if c in set(self.channels)]
            object.__setattr__(self, "channels", ordered)
            object.__setattr__(self, "mode", mode_from_channels(ordered))
        return self

    # ---- What it is allowed to be used for ------------------------------

    @property
    def answers_calls(self) -> bool:
        return AgentChannel.INBOUND_CALL in self.channels

    @property
    def places_calls(self) -> bool:
        return AgentChannel.OUTBOUND_CALL in self.channels

    @property
    def handles_messages(self) -> bool:
        return AgentChannel.WHATSAPP_MESSAGE in self.channels

    def public(self) -> dict:
        body = self.model_dump(by_alias=True, mode="json")
        # Said plainly as well as as a list, because every screen that shows
        # an agent asks one of these three questions and not "what is in the
        # array".
        body["answersCalls"] = self.answers_calls
        body["placesCalls"] = self.places_calls
        body["handlesMessages"] = self.handles_messages
        return body


class AgentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    greeting: str = Field(default="", max_length=2000)
    role_description: str = Field(default="", alias="roleDescription", max_length=8000)
    language: str = ""
    tone_notes: str = Field(default="", alias="toneNotes", max_length=2000)
    escalation_rules: str = Field(default="", alias="escalationRules", max_length=2000)
    tts_voice: str = Field(default="", alias="ttsVoice")
    speaking_pace: SpeakingPace = Field(default=SpeakingPace.NATURAL, alias="speakingPace")
    prompt_blocks: dict[str, str] = Field(default_factory=dict, alias="promptBlocks")
    extra_rules: str = Field(default="", alias="extraRules", max_length=8000)
    prompt_override: str = Field(default="", alias="promptOverride", max_length=20000)
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    status: AgentStatus = AgentStatus.DRAFT
    mode: AgentMode = AgentMode.BOTH
    #: Preferred over `mode`. Left empty, `mode` decides, so an older client
    #: keeps working unchanged.
    channels: list[AgentChannel] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    greeting: str | None = Field(default=None, max_length=2000)
    role_description: str | None = Field(default=None, alias="roleDescription", max_length=8000)
    language: str | None = None
    tone_notes: str | None = Field(default=None, alias="toneNotes", max_length=2000)
    escalation_rules: str | None = Field(default=None, alias="escalationRules", max_length=2000)
    tts_voice: str | None = Field(default=None, alias="ttsVoice")
    speaking_pace: SpeakingPace | None = Field(default=None, alias="speakingPace")
    prompt_blocks: dict[str, str] | None = Field(default=None, alias="promptBlocks")
    extra_rules: str | None = Field(default=None, alias="extraRules", max_length=8000)
    prompt_override: str | None = Field(
        default=None, alias="promptOverride", max_length=20000)
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    status: AgentStatus | None = None
    mode: AgentMode | None = None
    channels: list[AgentChannel] | None = None


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
