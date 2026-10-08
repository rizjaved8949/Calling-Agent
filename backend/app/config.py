"""
Process-wide settings.

Everything here is about the *platform*, not about any one customer. A
customer's Meta token, Infobip key or Google refresh token belongs to their
tenant row, not to this file — that is the difference between a SaaS and the
single-tenant services this backend was ported from, where onboarding meant
editing an env var and redeploying.

The only per-customer values that survive here are the legacy single-number
ones, kept so an existing single-tenant deployment keeps working while it is
migrated. See `app.repositories.tenants` for the resolution order.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # APP_ENV_FILE lets the test suite point somewhere that does not exist, so
    # tests never read the developer's own .env. A suite whose result depends
    # on local configuration is a suite that passes on one machine and fails on
    # another for reasons nobody can see.
    model_config = SettingsConfigDict(
        env_file=os.getenv("APP_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Service ----------------------------------------------------------
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    # Comma-separated CORS allowlist. Both spellings of the dev server are
    # included because they are *different origins* to a browser: opening the
    # app on 127.0.0.1 when only localhost is allowed fails every request with
    # an opaque CORS error that says nothing about the cause.
    frontend_url: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Where webhooks are delivered. Meta and Infobip both need an absolute URL.
    public_base_url: str = ""

    # ---- Persistence ------------------------------------------------------
    # Supabase is the system of record: voice_tenants, voice_calls and
    # voice_messages. The service key bypasses row-level security, which is why
    # it must never reach the browser.
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "call-recordings"
    supabase_signed_url_ttl_seconds: int = 3600
    # Supabase Storage rejects a single object above this; a longer call is
    # left with the provider rather than failing the save.
    supabase_max_upload_bytes: int = 50 * 1024 * 1024

    # ---- Credential encryption -------------------------------------------
    # Tenant tokens are sealed with this before they are written. Interoperable
    # with Conversation-Agent's voice/secretBox.ts — same scheme, same salt, so
    # either service can read what the other wrote.
    credentials_secret: str = ""

    # ---- Platform authentication -----------------------------------------
    # Presented as `X-Admin-Key` to create and edit tenants. Blank disables the
    # admin surface entirely rather than leaving it open.
    admin_api_key: str = ""

    # ---- Recording --------------------------------------------------------
    recordings_dir: str = "data/recordings"
    # 24k mono mp3: a seven-minute call is under a megabyte and sounds the same
    # down a phone line as the 6 MB wav the provider hands over.
    transcode_recordings: bool = True
    transcode_timeout_seconds: int = 120
    max_upload_bytes: int = 200 * 1024 * 1024
    # How long after a call starts a browser-made recording may still be posted.
    recording_upload_window_seconds: int = 3600

    # ---- Google ----------------------------------------------------------
    # The platform's OAuth *client*. The refresh token is per tenant: each
    # company connects its own Drive and its audio stays in its own account.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_url: str = ""
    google_drive_folder_name: str = "Calling Agent"
    # Keep the Sheet copy of the workbook in step with the call log.
    google_sheet_sync: bool = True
    google_sheet_sync_delay_seconds: float = 20.0

    # ---- Reports ---------------------------------------------------------
    excel_filename: str = "Call_Records.xlsx"

    # ---- Legacy single-tenant fallback ------------------------------------
    # Only read when no tenant row matches. Lets the pre-SaaS deployments keep
    # answering while their numbers are moved into the registry.
    meta_graph_api_version: str = "v23.0"
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_access_token: str = ""
    meta_app_secret: str = ""
    whatsapp_webhook_verify_token: str = ""
    # Expands a number typed in national form ("03191611020") into E.164.
    # Blank by default and on purpose: in a multi-tenant service a shared
    # default would dial a UK clinic's "07…" as a Pakistani number. Each
    # company sets its own; this is only the legacy single-tenant fallback.
    default_country_code: str = ""

    # ---- Telephony (SIM / SIP) -------------------------------------------
    infobip_base_url: str = ""
    infobip_api_key: str = ""

    # ---- Voice agent ------------------------------------------------------
    # Configured here, and **not yet used**: the engine that carries call audio
    # is the one piece this build does not have (see the README). The key and
    # the model live here so that when it lands there is nothing to go and find,
    # and so /health can say whether the agent could run at all.
    #
    # Greeting, language, voice and persona are deliberately absent: each
    # company sets its own on the settings screen, and a platform-wide default
    # would be a sentence spoken to someone else's callers.
    voice_engine: str = "gemini"
    gemini_api_key: str = ""
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    # The text model that answers WhatsApp messages. Separate from the live
    # one: a realtime voice model is the wrong tool for composing a sentence
    # nobody is waiting on, and costs more to do it.
    #
    # An explicit version rather than an alias, so an answer does not change
    # character on a day nobody deployed anything. Check it still exists when
    # upgrading: gemini-2.5-flash is still *listed* by the API and returns 404
    # with "no longer available to new users", so a model appearing in the
    # catalogue is not proof it can be called.
    gemini_text_model: str = "gemini-3.5-flash"

    request_timeout_seconds: float = Field(default=30.0)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_url.split(",") if o.strip()]

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url.strip() and self.supabase_service_key.strip())

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def recordings_path(self) -> Path:
        candidate = Path(self.recordings_dir)
        return candidate if candidate.is_absolute() else BASE_DIR / candidate

    @property
    def agent_configured(self) -> bool:
        """Whether the voice engine has a key. It still has no media bridge."""
        if self.voice_engine.lower() == "gemini":
            return bool(self.gemini_api_key.strip())
        return False

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
