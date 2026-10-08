"""
What a company fills in on the settings screen, and what it is shown back.

Credentials are **write-only**. A value is accepted, sealed and stored; what
comes back is whether it is set and its last four characters — enough for
someone to confirm which token they pasted, never enough to use it. There is
no endpoint that returns a secret, which is what makes a support screenshot or
a shared browser session harmless.

Each channel is listed with exactly the fields it needs, in the order the
provider's own console shows them, because the person filling this in is
copying from that console with two tabs open.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .tenant import Tenant


class ChannelKind(str, Enum):
    SIM = "sim"
    WHATSAPP_CALL = "whatsapp_call"
    WHATSAPP_MESSAGE = "whatsapp_message"


class CredentialsUpdate(BaseModel):
    """The settings form.

    Every field is optional and None means "not mentioned": a form that only
    changes the greeting must not blank the access token. An empty string is
    different and does mean "clear this".
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # ---- Identity the company can correct themselves --------------------
    name: str | None = None
    display_phone_number: str | None = Field(default=None, alias="displayPhoneNumber")
    default_country_code: str | None = Field(default=None, alias="defaultCountryCode")

    # ---- Meta / WhatsApp -------------------------------------------------
    waba_id: str | None = Field(default=None, alias="businessAccountId")
    access_token: str | None = Field(default=None, alias="accessToken")
    app_secret: str | None = Field(default=None, alias="metaAppSecret")
    verify_token: str | None = Field(default=None, alias="webhookVerifyToken")
    graph_api_version: str | None = Field(default=None, alias="graphApiVersion")

    # ---- Carrier (SIM) ----------------------------------------------------
    infobip_api_key: str | None = Field(default=None, alias="infobipApiKey")
    infobip_base_url: str | None = Field(default=None, alias="infobipBaseUrl")
    infobip_phone_number: str | None = Field(default=None, alias="infobipPhoneNumber")
    infobip_calls_configuration_id: str | None = Field(
        default=None, alias="infobipCallsConfigurationId"
    )

    # ---- Agent ------------------------------------------------------------
    agent_greeting: str | None = Field(default=None, alias="agentGreeting")
    persona: str | None = None
    language: str | None = None
    tts_voice: str | None = Field(default=None, alias="ttsVoice")
    record_calls: bool | None = Field(default=None, alias="recordCalls")


# Which stored field backs each form field, and whether it is a secret.
# `phoneNumberId` is absent on purpose: it is the tenant's primary key, and
# changing it would mean a different company, not an edited one.
FIELD_MAP: dict[str, tuple[str, bool]] = {
    "businessAccountId": ("waba_id", False),
    "accessToken": ("access_token", True),
    "metaAppSecret": ("app_secret", True),
    "webhookVerifyToken": ("verify_token", True),
    "infobipApiKey": ("infobip_api_key", True),
    "infobipBaseUrl": ("infobip_base_url", False),
    "infobipPhoneNumber": ("infobip_phone_number", False),
    "infobipCallsConfigurationId": ("infobip_calls_configuration_id", False),
}

# What each channel needs before it can carry a call or a message.
CHANNEL_REQUIREMENTS: dict[ChannelKind, tuple[str, ...]] = {
    ChannelKind.SIM: ("infobipApiKey", "infobipBaseUrl", "infobipPhoneNumber"),
    ChannelKind.WHATSAPP_CALL: (
        "accessToken", "metaAppSecret", "businessAccountId", "webhookVerifyToken",
    ),
    ChannelKind.WHATSAPP_MESSAGE: ("accessToken", "businessAccountId"),
}

CHANNEL_LABEL: dict[ChannelKind, str] = {
    ChannelKind.SIM: "Phone line",
    ChannelKind.WHATSAPP_CALL: "WhatsApp calling",
    ChannelKind.WHATSAPP_MESSAGE: "WhatsApp messaging",
}


def _hint(value: str, secret: bool) -> str:
    """The last four characters, so someone can tell which value they pasted.

    A short value is hidden entirely rather than mostly shown — "…abcd" of a
    six-character secret gives away most of it.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if not secret:
        return value
    return f"…{value[-4:]}" if len(value) >= 8 else "…"


def credential_status(tenant: Tenant) -> dict[str, Any]:
    """Which credentials are set, without revealing any of them."""
    status: dict[str, Any] = {}
    for form_field, (attribute, secret) in FIELD_MAP.items():
        value = str(getattr(tenant, attribute, "") or "")
        status[form_field] = {
            "set": bool(value.strip()),
            "hint": _hint(value, secret),
            "secret": secret,
        }
    # Not editable, but the screen shows it: it is how Meta addresses them.
    status["phoneNumberId"] = {
        "set": bool(tenant.phone_number_id),
        "hint": tenant.phone_number_id,
        "secret": False,
        "readOnly": True,
    }
    return status


def channel_readiness(tenant: Tenant) -> list[dict[str, Any]]:
    """Per channel: can it work yet, and if not, what is still missing.

    Naming the missing fields is the difference between "not connected" and a
    screen someone can actually finish.
    """
    status = credential_status(tenant)
    channels = []
    for kind, required in CHANNEL_REQUIREMENTS.items():
        missing = [field for field in required if not status[field]["set"]]
        channels.append(
            {
                "id": kind.value,
                "kind": kind.value,
                "label": CHANNEL_LABEL[kind],
                "connected": not missing,
                "missing": missing,
                "required": list(required),
            }
        )
    return channels
