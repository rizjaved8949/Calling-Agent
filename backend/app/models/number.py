"""
A phone number a company has connected, with the credentials it runs on.

This is the unit a company sells itself on: "our support line", "our sales
WhatsApp". A company may have as many as it likes, each from its own provider
account — a SIM number bought in Infobip, a WhatsApp number from Meta — and
each one carries everything needed to reach that provider.

The lifecycle is deliberate:

1. **pending** — credentials entered, nothing proven.
2. **verified** — `POST /numbers/{id}/verify` reached the provider with them
   and the provider recognised the number. Only now can it take calls.
3. **failed** — the provider refused; `statusDetail` says why in its words.

A verified number then gets an agent for inbound, an agent for outbound, or
both, depending on `mode`. Changing which agent answers is changing that one
field — nothing else about the number moves.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NumberKind(str, Enum):
    SIM = "sim"              # a phone line through Infobip
    WHATSAPP = "whatsapp"    # a WhatsApp Business number through Meta


class NumberMode(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    BOTH = "both"


class NumberStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


# Sealed before the row is written; never returned over HTTP.
SECRET_FIELDS = ("accessToken", "appSecret", "verifyToken", "infobipApiKey")


def _hint(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) >= 8 else "…"


class PhoneNumber(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = Field(default="", alias="tenantId")
    kind: NumberKind = NumberKind.SIM
    label: str = ""
    # E.164, as the customer sees it. For SIM this is also the caller ID.
    phone_number: str = Field(default="", alias="phoneNumber")
    mode: NumberMode = NumberMode.BOTH
    status: NumberStatus = NumberStatus.PENDING
    status_detail: str = Field(default="", alias="statusDetail")
    verified_at: float | None = Field(default=None, alias="verifiedAt")

    inbound_agent_id: str = Field(default="", alias="inboundAgentId")
    outbound_agent_id: str = Field(default="", alias="outboundAgentId")
    # WhatsApp only: reply to inbound text messages without a person.
    auto_reply: bool = Field(default=False, alias="autoReply")
    # Which agent writes those replies. Empty falls back to the inbound agent,
    # which is what every number did before this could be chosen separately —
    # the one that answers the phone is a reasonable default for the one that
    # answers a message, but a company that wants a different voice in writing
    # can now say so.
    message_agent_id: str = Field(default="", alias="messageAgentId")
    # And which documents those replies come from, when it should not be the
    # agent's own. Same shape as a call setup's override.
    message_knowledge_base_id: str = Field(default="", alias="messageKnowledgeBaseId")

    # Run on the platform owner's credentials from the server environment.
    # Only honoured for a company the operator has allowed it for.
    use_platform_credentials: bool = Field(default=False, alias="usePlatformCredentials")

    # ---- Meta ----------------------------------------------------------
    meta_phone_number_id: str = Field(default="", alias="metaPhoneNumberId")
    waba_id: str = Field(default="", alias="wabaId")
    access_token: str = Field(default="", alias="accessToken")
    app_secret: str = Field(default="", alias="appSecret")
    verify_token: str = Field(default="", alias="verifyToken")

    # ---- Infobip -------------------------------------------------------
    infobip_api_key: str = Field(default="", alias="infobipApiKey")
    infobip_base_url: str = Field(default="", alias="infobipBaseUrl")
    infobip_calls_configuration_id: str = Field(
        default="", alias="infobipCallsConfigurationId"
    )
    # Registered in the company's own Infobip account the first time a call is
    # bridged, and remembered here. Not a credential and not something a
    # company types: it names the socket this deployment listens on.
    infobip_websocket_config_id: str = Field(
        default="", alias="infobipWebsocketConfigId"
    )

    created_at: float = Field(default_factory=time.time, alias="createdAt")

    @property
    def verified(self) -> bool:
        return self.status is NumberStatus.VERIFIED

    @property
    def takes_inbound(self) -> bool:
        return self.mode in {NumberMode.INBOUND, NumberMode.BOTH}

    @property
    def makes_outbound(self) -> bool:
        return self.mode in {NumberMode.OUTBOUND, NumberMode.BOTH}

    def public(self, *, base_url: str = "") -> dict[str, Any]:
        base = base_url.rstrip("/")
        webhook = ""
        if base:
            webhook = (
                f"{base}/api/webhooks/whatsapp"
                if self.kind is NumberKind.WHATSAPP
                else f"{base}/api/webhooks/infobip/line/{self.id}"
            )
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "phoneNumber": self.phone_number,
            "mode": self.mode.value,
            "status": self.status.value,
            "statusDetail": self.status_detail or None,
            "verifiedAt": self.verified_at,
            "inboundAgentId": self.inbound_agent_id or None,
            "outboundAgentId": self.outbound_agent_id or None,
            "autoReply": self.auto_reply,
            "messageAgentId": self.message_agent_id or None,
            "messageKnowledgeBaseId": self.message_knowledge_base_id or None,
            "usePlatformCredentials": self.use_platform_credentials,
            "createdAt": self.created_at,
            "webhookUrl": webhook or None,
            "credentials": {
                "metaPhoneNumberId": self.meta_phone_number_id or None,
                "wabaId": self.waba_id or None,
                "accessToken": _hint(self.access_token) or None,
                "appSecret": _hint(self.app_secret) or None,
                "verifyToken": _hint(self.verify_token) or None,
                "infobipApiKey": _hint(self.infobip_api_key) or None,
                "infobipBaseUrl": self.infobip_base_url or None,
                "infobipCallsConfigurationId": self.infobip_calls_configuration_id or None,
            },
            "audioEndpointRegistered": bool(self.infobip_websocket_config_id),
        }


class NumberCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: NumberKind
    label: str = Field(default="", max_length=120)
    phone_number: str = Field(alias="phoneNumber", min_length=3, max_length=32)
    mode: NumberMode = NumberMode.BOTH
    use_platform_credentials: bool = Field(default=False, alias="usePlatformCredentials")

    meta_phone_number_id: str = Field(default="", alias="metaPhoneNumberId", max_length=64)
    waba_id: str = Field(default="", alias="wabaId", max_length=64)
    access_token: str = Field(default="", alias="accessToken", max_length=1024)
    app_secret: str = Field(default="", alias="appSecret", max_length=256)
    verify_token: str = Field(default="", alias="verifyToken", max_length=256)

    infobip_api_key: str = Field(default="", alias="infobipApiKey", max_length=512)
    infobip_base_url: str = Field(default="", alias="infobipBaseUrl", max_length=256)
    infobip_calls_configuration_id: str = Field(
        default="", alias="infobipCallsConfigurationId", max_length=128
    )


class NumberUpdate(BaseModel):
    """None means "not mentioned". A blank secret is ignored rather than
    clearing it, because the edit form never shows the old one back."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    label: str | None = Field(default=None, max_length=120)
    phone_number: str | None = Field(default=None, alias="phoneNumber", max_length=32)
    mode: NumberMode | None = None
    inbound_agent_id: str | None = Field(default=None, alias="inboundAgentId")
    outbound_agent_id: str | None = Field(default=None, alias="outboundAgentId")
    auto_reply: bool | None = Field(default=None, alias="autoReply")
    message_agent_id: str | None = Field(default=None, alias="messageAgentId")
    message_knowledge_base_id: str | None = Field(
        default=None, alias="messageKnowledgeBaseId")
    use_platform_credentials: bool | None = Field(default=None, alias="usePlatformCredentials")

    meta_phone_number_id: str | None = Field(default=None, alias="metaPhoneNumberId")
    waba_id: str | None = Field(default=None, alias="wabaId")
    access_token: str | None = Field(default=None, alias="accessToken")
    app_secret: str | None = Field(default=None, alias="appSecret")
    verify_token: str | None = Field(default=None, alias="verifyToken")

    infobip_api_key: str | None = Field(default=None, alias="infobipApiKey")
    infobip_base_url: str | None = Field(default=None, alias="infobipBaseUrl")
    infobip_calls_configuration_id: str | None = Field(
        default=None, alias="infobipCallsConfigurationId"
    )


# Changing any of these means the provider has to be asked again.
CREDENTIAL_FIELDS = {
    "phone_number", "meta_phone_number_id", "waba_id", "access_token", "app_secret",
    "verify_token", "infobip_api_key", "infobip_base_url",
    "infobip_calls_configuration_id", "use_platform_credentials",
}
SECRET_ATTRS = {"access_token", "app_secret", "verify_token", "infobip_api_key"}
