"""
A list of people to call, and how far through it we are.

Deliberately simple: a name, a set of numbers, and a state machine with four
states. The complicated parts of a dialler — pacing, retries, abandon rates —
are where the regulatory trouble lives, so this does the conservative thing
instead. One call at a time, a gap between them, and no automatic redialling of
someone who did not answer.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .call import Channel


class CampaignStatus(str, Enum):
    DRAFT = "DRAFT"        # being built; nothing has been called
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DONE = "DONE"


class ContactState(str, Enum):
    WAITING = "WAITING"
    CALLING = "CALLING"
    DONE = "DONE"          # the call connected and ended normally
    FAILED = "FAILED"      # the carrier or Meta refused, or nobody answered
    SKIPPED = "SKIPPED"    # removed before it was reached


class Contact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    number: str
    name: str = ""
    state: ContactState = ContactState.WAITING
    call_id: str = Field(default="", alias="callId")
    attempts: int = 0
    note: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "name": self.name or None,
            "state": self.state.value,
            "callId": self.call_id or None,
            "attempts": self.attempts,
            "note": self.note or None,
        }


class Campaign(BaseModel):
    """One outbound list. Serialised into `voice_campaigns.data`."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = Field(default="", alias="tenantId")
    name: str = ""
    channel: Channel = Channel.PHONE
    status: CampaignStatus = CampaignStatus.DRAFT
    contacts: list[Contact] = Field(default_factory=list)

    # What the agent should open with on these calls. Falls back to the
    # company's usual greeting when empty, so a campaign does not have to
    # restate the whole persona to change one sentence.
    opening: str = ""

    # Which agent works this list, and what it may answer from. Blank falls
    # back the same way any other call does — see services/routing.py.
    agent_id: str = Field(default="", alias="agentId")
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")

    # Seconds between calls. A floor rather than a target: the point is that a
    # list is worked through at a human pace, not that it finishes quickly.
    gap_seconds: int = Field(default=20, alias="gapSeconds")

    created_at: float = Field(default_factory=time.time, alias="createdAt")
    started_at: float | None = Field(default=None, alias="startedAt")
    finished_at: float | None = Field(default=None, alias="finishedAt")
    last_error: str = Field(default="", alias="lastError")

    # ---- derived ----------------------------------------------------------

    def counts(self) -> dict[str, int]:
        tally = {state.value: 0 for state in ContactState}
        for contact in self.contacts:
            tally[contact.state.value] += 1
        return tally

    def next_waiting(self) -> Contact | None:
        return next((c for c in self.contacts if c.state is ContactState.WAITING), None)

    @property
    def is_finished(self) -> bool:
        return not any(
            c.state in {ContactState.WAITING, ContactState.CALLING} for c in self.contacts
        )

    def public(self) -> dict[str, Any]:
        tally = self.counts()
        total = len(self.contacts)
        return {
            "id": self.id,
            "tenantId": self.tenant_id,
            "name": self.name,
            "channel": self.channel.value,
            "status": self.status.value,
            "opening": self.opening or None,
            "agentId": self.agent_id or None,
            "knowledgeBaseId": self.knowledge_base_id or None,
            "gapSeconds": self.gap_seconds,
            "total": total,
            "counts": tally,
            "progress": round(
                ((total - tally["WAITING"] - tally["CALLING"]) / total) * 100
            ) if total else 0,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "lastError": self.last_error or None,
            "contacts": [c.public() for c in self.contacts],
        }


class CampaignCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1)
    channel: Channel = Channel.PHONE
    opening: str = ""
    gap_seconds: int = Field(default=20, ge=5, le=600, alias="gapSeconds")
    agent_id: str = Field(default="", alias="agentId")
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    # Pasted in, one per line: "number" or "number, name". Parsed server-side
    # so the same rules apply however it was entered.
    numbers: str = ""


class CampaignUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = None
    opening: str | None = None
    gap_seconds: int | None = Field(default=None, ge=5, le=600, alias="gapSeconds")
    agent_id: str | None = Field(default=None, alias="agentId")
    knowledge_base_id: str | None = Field(default=None, alias="knowledgeBaseId")
    numbers: str | None = None
