"""
A tenant is a customer company and the credentials its agent runs on.

Meta identifies the business on every webhook and every Graph call by
`phone_number_id`, so that is the key — the same choice the TypeScript registry
makes, and the reason `voice_tenants.id` is text rather than a surrogate uuid.

Field names are camelCase inside `data` to stay readable from the TS service,
which writes the same rows. The Python side converts at the edge.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GoogleDriveLink(BaseModel):
    """A company's own Drive, connected by OAuth.

    The refresh token is the company's, not ours: recordings are written into
    their account, under their quota, and revoking our access in their Google
    settings is enough to cut us off. `folder_id` is cached after the first
    upload so every later one skips the lookup.
    """

    model_config = ConfigDict(populate_by_name=True)

    refresh_token: str = Field(default="", alias="refreshToken")
    folder_id: str = Field(default="", alias="folderId")
    folder_name: str = Field(default="", alias="folderName")
    account_email: str = Field(default="", alias="accountEmail")
    # The Sheet copy of the call-record workbook, kept at a stable link.
    sheet_id: str = Field(default="", alias="sheetId")
    sheet_link: str = Field(default="", alias="sheetLink")
    connected_at: str = Field(default="", alias="connectedAt")

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token.strip())


class Tenant(BaseModel):
    """One customer's calling agent."""

    model_config = ConfigDict(populate_by_name=True)

    # ---- Identity ---------------------------------------------------------
    phone_number_id: str = Field(alias="phoneNumberId")
    waba_id: str = Field(default="", alias="wabaId")
    name: str = ""
    display_phone_number: str = Field(default="", alias="displayPhoneNumber")
    organization_id: str = Field(default="", alias="organizationId")

    # ---- Meta / WhatsApp credentials -------------------------------------
    access_token: str = Field(default="", alias="accessToken")
    app_secret: str = Field(default="", alias="appSecret")
    verify_token: str = Field(default="", alias="verifyToken")
    graph_api_version: str = Field(default="", alias="graphApiVersion")

    # ---- Agent persona ----------------------------------------------------
    agent_greeting: str = Field(default="", alias="agentGreeting")
    language: str = ""
    tts_voice: str = Field(default="", alias="ttsVoice")
    persona: str = ""
    knowledge_base_pdf: str = Field(default="", alias="knowledgeBasePdf")

    # ---- SIM / SIP telephony ---------------------------------------------
    # Per-tenant Infobip credentials; blank falls back to the platform's.
    infobip_api_key: str = Field(default="", alias="infobipApiKey")
    infobip_base_url: str = Field(default="", alias="infobipBaseUrl")
    infobip_phone_number: str = Field(default="", alias="infobipPhoneNumber")
    infobip_calls_configuration_id: str = Field(
        default="", alias="infobipCallsConfigurationId"
    )

    # ---- Storage ----------------------------------------------------------
    google_drive: GoogleDriveLink = Field(
        default_factory=GoogleDriveLink, alias="googleDrive"
    )
    record_calls: bool = Field(default=True, alias="recordCalls")
    # Whether the agent answers inbound WhatsApp messages by itself. Off by
    # default: a company that has not yet uploaded its material would have an
    # agent answering questions about it from nothing.
    auto_reply: bool = Field(default=False, alias="autoReply")

    # ---- Access -----------------------------------------------------------
    # Presented by this customer when reading their own calls and recordings.
    api_key: str = Field(default="", alias="apiKey")
    # Where finished calls are pushed for QA scoring, if the company wants them.
    qa_webhook_url: str = Field(default="", alias="qaWebhookUrl")
    qa_api_key: str = Field(default="", alias="qaApiKey")

    default_country_code: str = Field(default="", alias="defaultCountryCode")

    # Set by the platform operator, never by the company: lets this company's
    # numbers run on the platform's own Meta/Infobip credentials from the
    # server environment. This is how a demo account calls on the owner's line.
    allow_platform_credentials: bool = Field(default=False, alias="allowPlatformCredentials")

    # ---- Per-call overlay (never stored) ----------------------------------
    # A company has many numbers, each with its own credentials. When a call
    # or message is handled for one of them, `repositories/numbers.as_tenant`
    # returns a copy of the tenant with that number's credentials laid over
    # these fields and these two set. `phone_number_id` stays the company key,
    # so every row is still filed under the company.
    line_id: str = Field(default="", alias="lineId")
    meta_phone_number_id: str = Field(default="", alias="metaPhoneNumberId")

    # ---- Derived ----------------------------------------------------------

    @property
    def graph_number_id(self) -> str:
        """The id Meta's Graph API knows this number by."""
        return self.meta_phone_number_id or self.phone_number_id

    @property
    def configured(self) -> bool:
        """Enough credentials to actually talk to Meta as this business."""
        return bool(
            self.access_token
            and self.waba_id
            and self.graph_number_id
            and not self.graph_number_id.startswith("pending-")
        )

    def public(self) -> dict[str, Any]:
        """Safe to log or return over HTTP — never includes a secret."""
        return {
            "phoneNumberId": self.phone_number_id,
            "wabaId": self.waba_id,
            "name": self.name,
            "displayPhoneNumber": self.display_phone_number,
            "organizationId": self.organization_id or None,
            "configured": self.configured,
            "language": self.language or None,
            "ttsVoice": self.tts_voice or None,
            "agentGreeting": self.agent_greeting or None,
            # Returned so the settings screen can show what was entered. Not a
            # secret — it is the company's own description of its agent, and a
            # field that cannot be read back is a field nobody can correct.
            "persona": self.persona or None,
            "knowledgeBasePdf": self.knowledge_base_pdf or None,
            "recordCalls": self.record_calls,
            "autoReply": self.auto_reply,
            "googleDrive": {
                "connected": self.google_drive.connected,
                "accountEmail": self.google_drive.account_email or None,
                "folderName": self.google_drive.folder_name or None,
                "folderId": self.google_drive.folder_id or None,
                "sheetLink": self.google_drive.sheet_link or None,
            },
            "telephony": {
                "infobipConfigured": bool(self.infobip_api_key),
                "phoneNumber": self.infobip_phone_number or None,
            },
            "qaWebhookConfigured": bool(self.qa_webhook_url),
            "allowPlatformCredentials": self.allow_platform_credentials,
        }


# The fields inside `data` that are sealed before the row is written. Anything
# listed here is run through secret_box on the way in and out.
SECRET_FIELDS = (
    "accessToken",
    "appSecret",
    "verifyToken",
    "apiKey",
    "qaApiKey",
    "infobipApiKey",
)
# Nested secrets, as (container, field) pairs.
NESTED_SECRET_FIELDS = (("googleDrive", "refreshToken"),)


class TenantCreate(BaseModel):
    """What the admin surface accepts when onboarding a company."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    phone_number_id: str = Field(alias="phoneNumberId", min_length=1)
    name: str = Field(min_length=1)
    waba_id: str = Field(default="", alias="wabaId")
    display_phone_number: str = Field(default="", alias="displayPhoneNumber")
    organization_id: str = Field(default="", alias="organizationId")
    access_token: str = Field(default="", alias="accessToken")
    app_secret: str = Field(default="", alias="appSecret")
    verify_token: str = Field(default="", alias="verifyToken")
    agent_greeting: str = Field(default="", alias="agentGreeting")
    language: str = ""
    tts_voice: str = Field(default="", alias="ttsVoice")
    persona: str = ""
    infobip_api_key: str = Field(default="", alias="infobipApiKey")
    infobip_base_url: str = Field(default="", alias="infobipBaseUrl")
    infobip_phone_number: str = Field(default="", alias="infobipPhoneNumber")
    infobip_calls_configuration_id: str = Field(
        default="", alias="infobipCallsConfigurationId"
    )
    record_calls: bool = Field(default=True, alias="recordCalls")
    auto_reply: bool = Field(default=False, alias="autoReply")
    qa_webhook_url: str = Field(default="", alias="qaWebhookUrl")
    qa_api_key: str = Field(default="", alias="qaApiKey")
    default_country_code: str = Field(default="", alias="defaultCountryCode")
    allow_platform_credentials: bool = Field(default=False, alias="allowPlatformCredentials")


class TenantUpdate(BaseModel):
    """Same fields, all optional.

    Every default is None rather than "", so a PATCH that omits a field leaves
    it alone instead of blanking it — the difference between "I did not mention
    my access token" and "delete my access token".
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = None
    waba_id: str | None = Field(default=None, alias="wabaId")
    display_phone_number: str | None = Field(default=None, alias="displayPhoneNumber")
    organization_id: str | None = Field(default=None, alias="organizationId")
    access_token: str | None = Field(default=None, alias="accessToken")
    app_secret: str | None = Field(default=None, alias="appSecret")
    verify_token: str | None = Field(default=None, alias="verifyToken")
    agent_greeting: str | None = Field(default=None, alias="agentGreeting")
    language: str | None = None
    tts_voice: str | None = Field(default=None, alias="ttsVoice")
    persona: str | None = None
    infobip_api_key: str | None = Field(default=None, alias="infobipApiKey")
    infobip_base_url: str | None = Field(default=None, alias="infobipBaseUrl")
    infobip_phone_number: str | None = Field(default=None, alias="infobipPhoneNumber")
    infobip_calls_configuration_id: str | None = Field(
        default=None, alias="infobipCallsConfigurationId"
    )
    record_calls: bool | None = Field(default=None, alias="recordCalls")
    auto_reply: bool | None = Field(default=None, alias="autoReply")
    qa_webhook_url: str | None = Field(default=None, alias="qaWebhookUrl")
    qa_api_key: str | None = Field(default=None, alias="qaApiKey")
    default_country_code: str | None = Field(default=None, alias="defaultCountryCode")
    allow_platform_credentials: bool | None = Field(default=None, alias="allowPlatformCredentials")
