"""
A call, and the WhatsApp messages around it.

The record is the telephony state machine — provider ids, timings, where the
recording ended up — and it is deliberately not the QA record of whether anyone
has graded the conversation. Merging the two ties a call's signalling lifecycle
to a scoring workflow that may never run.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Channel(str, Enum):
    PHONE = "PHONE"                      # SIM / SIP, through Infobip
    WHATSAPP_CALL = "WHATSAPP_CALL"      # Meta WhatsApp Business Calling
    WHATSAPP_MESSAGE = "WHATSAPP_MESSAGE"
    BROWSER = "BROWSER"                  # WebRTC leg taken by a human operator


class Direction(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class CallStatus(str, Enum):
    QUEUED = "QUEUED"
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NO_ANSWER = "NO_ANSWER"
    HANDED_OFF = "HANDED_OFF"


# A call in one of these has stopped moving: nothing will update it again
# except a recording arriving late.
TERMINAL_STATUSES = {
    CallStatus.COMPLETED,
    CallStatus.FAILED,
    CallStatus.NO_ANSWER,
    CallStatus.HANDED_OFF,
}


class RecordingState(str, Enum):
    NONE = "NONE"            # not recorded, and not expected to be
    PENDING = "PENDING"      # the call is recording, or we are waiting for the file
    READY = "READY"          # audio is stored and playable
    ABSENT = "ABSENT"        # the call ended and no recording ever arrived
    FAILED = "FAILED"        # we know why it is missing; `recording_error` says


class Call(BaseModel):
    """One call. Serialised into `voice_calls.data`."""

    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = Field(default="", alias="tenantId")

    channel: Channel = Channel.PHONE
    direction: Direction = Direction.OUTBOUND
    status: CallStatus = CallStatus.QUEUED

    # The other party, always E.164. Stored in full; masked only when logged.
    counterparty: str = ""
    # The number the customer's agent presented.
    from_number: str = Field(default="", alias="fromNumber")

    # What the provider calls this call. Infobip uses callId/dialogId; Meta
    # uses a call id on the webhook. Either way it is how a webhook that
    # arrives minutes later is matched back to this row.
    provider_call_id: str = Field(default="", alias="providerCallId")
    provider_dialog_id: str = Field(default="", alias="providerDialogId")

    started_at: float = Field(default_factory=time.time, alias="startedAt")
    answered_at: float | None = Field(default=None, alias="answeredAt")
    ended_at: float | None = Field(default=None, alias="endedAt")
    duration_seconds: int = Field(default=0, alias="durationSeconds")

    # ---- Recording --------------------------------------------------------
    recording_state: RecordingState = Field(
        default=RecordingState.NONE, alias="recordingState"
    )
    # Where the audio is, in the storage layer's own notation:
    #   gd://<drive file id>   — the customer's Google Drive
    #   sb://<object key>      — Supabase Storage
    #   a relative path        — this machine's disk (development only)
    recording_path: str = Field(default="", alias="recordingPath")
    recording_mime: str = Field(default="", alias="recordingMime")
    recording_bytes: int = Field(default=0, alias="recordingBytes")
    # The provider's own file id, kept even when our copy succeeded: it is the
    # fallback source for a call too large to store.
    recording_file_id: str = Field(default="", alias="recordingFileId")
    recording_error: str = Field(default="", alias="recordingError")

    # ---- Content ----------------------------------------------------------
    transcript: str = ""
    summary: str = ""
    topic: str = ""
    language: str = ""
    error: str = ""
    # Whoever ultimately spoke to the caller: the agent, or a named person.
    handled_by: str = Field(default="agent", alias="handledBy")

    # ---- Which agent answered, and what it was allowed to know -----------
    # Filled in by `services/routing.py` when the call is answered. Recorded
    # rather than re-derived, because "why did it say that?" is asked weeks
    # later, by which time the setups have been edited and the chain would
    # resolve differently.
    agent_id: str = Field(default="", alias="agentId")
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    knowledge_base_name: str = Field(default="", alias="knowledgeBaseName")
    # setup | explicit | agent | company — see services/routing.py
    resolved_by: str = Field(default="", alias="resolvedBy")

    metadata: dict[str, Any] = Field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        """The shape the dashboard reads. camelCase, and no internal fields."""
        return {
            "id": self.id,
            "tenantId": self.tenant_id,
            "channel": self.channel.value,
            "direction": self.direction.value,
            "status": self.status.value,
            "counterparty": self.counterparty,
            "fromNumber": self.from_number or None,
            "providerCallId": self.provider_call_id or None,
            "startedAt": self.started_at,
            "answeredAt": self.answered_at,
            "endedAt": self.ended_at,
            "durationSeconds": self.duration_seconds,
            "recording": {
                "state": self.recording_state.value,
                "mime": self.recording_mime or None,
                "bytes": self.recording_bytes or None,
                # The reference itself is withheld: it names a storage bucket
                # or a Drive file id, neither of which the browser should see.
                # `GET /calls/{id}/recording` is how audio is reached.
                "available": self.recording_state == RecordingState.READY,
                "error": self.recording_error or None,
            },
            "transcript": self.transcript or None,
            "summary": self.summary or None,
            "topic": self.topic or None,
            "language": self.language or None,
            "handledBy": self.handled_by,
            "agentId": self.agent_id or None,
            "knowledgeBaseId": self.knowledge_base_id or None,
            "knowledgeBaseName": self.knowledge_base_name or None,
            "resolvedBy": self.resolved_by or None,
            "error": self.error or None,
        }


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    to: str = Field(min_length=3)
    channel: Channel = Channel.PHONE
    # Overrides the tenant's default greeting for this call only.
    greeting: str | None = None
    # Which agent makes this call, and what it may answer from. Both optional:
    # left out, `services/routing.py` falls back the same way it does for a
    # call that arrives with nothing specified.
    agent_id: str = Field(default="", alias="agentId")
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class Message(BaseModel):
    """A WhatsApp message. Serialised into `voice_messages.data`."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = Field(default="", alias="tenantId")
    # Meta's message id — the handle used for delivery receipts and replies.
    provider_message_id: str = Field(default="", alias="providerMessageId")
    direction: MessageDirection = MessageDirection.OUTBOUND
    counterparty: str = ""
    kind: str = "text"  # text | template | interactive | media | system
    body: str = ""
    template_name: str = Field(default="", alias="templateName")
    status: str = "accepted"  # accepted | sent | delivered | read | failed
    error: str = ""
    created_at: float = Field(default_factory=time.time, alias="createdAt")
    # Set when the message belongs to the conversation around a call.
    call_id: str = Field(default="", alias="callId")

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenantId": self.tenant_id,
            "providerMessageId": self.provider_message_id or None,
            "direction": self.direction.value,
            "counterparty": self.counterparty,
            "kind": self.kind,
            "body": self.body,
            "templateName": self.template_name or None,
            "status": self.status,
            "error": self.error or None,
            "createdAt": self.created_at,
            "callId": self.call_id or None,
        }


class SendTextRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    to: str = Field(min_length=3)
    body: str = Field(min_length=1, max_length=4000)
    call_id: str | None = Field(default=None, alias="callId")


class CallPermissionRequest(BaseModel):
    """Asking someone whether a business may call them.

    Only the number: the template is Meta's own and carries no parameters.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    to: str = Field(min_length=3)
    template: str = "call_permission_request"
    language: str = "en"


class SendTemplateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    to: str = Field(min_length=3)
    template: str = Field(min_length=1)
    language: str = "en_US"
    # Positional body parameters, in order. Meta has no named parameters.
    parameters: list[str] = Field(default_factory=list)
    call_id: str | None = Field(default=None, alias="callId")
