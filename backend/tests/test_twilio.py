"""
Twilio phone lines: a third kind of number that behaves like an Infobip line.

Twilio is never reached from here. Its REST API is replaced with a fake httpx
client and its media socket with the test client's websocket, so what is
tested is our side of each conversation: what we store, what we ask Twilio
for, what we answer its webhooks with, and how its audio is turned into the
frames the agent expects.
"""
from __future__ import annotations

import pytest

from app.models.number import NumberKind, PhoneNumber, is_phone_kind
from app.models.tenant import Tenant

SID = "AC" + "0" * 32
TOKEN = "twilio-auth-token-abcd"


def _twilio_number(**extra) -> PhoneNumber:
    return PhoneNumber(
        id="tw1", tenantId="700", kind=NumberKind.TWILIO, label="Twilio line",
        phoneNumber="+15551234567", twilioAccountSid=SID, twilioAuthToken=TOKEN,
        **extra,
    )


# ---------------------------------------------------------------------------
# Step 1: the model and its storage
# ---------------------------------------------------------------------------


def test_phone_kinds_are_infobip_and_twilio_not_whatsapp():
    assert is_phone_kind(NumberKind.SIM)
    assert is_phone_kind(NumberKind.TWILIO)
    assert is_phone_kind("twilio")
    assert not is_phone_kind(NumberKind.WHATSAPP)
    assert not is_phone_kind("nonsense")
    assert not is_phone_kind(None)


def test_a_twilio_number_shows_its_two_urls_and_hides_its_token():
    public = _twilio_number().public(base_url="https://api.example.com/")
    assert public["kind"] == "twilio"
    assert public["webhookUrl"] == "https://api.example.com/api/webhooks/twilio/voice/tw1"
    assert public["statusCallbackUrl"] == "https://api.example.com/api/webhooks/twilio/status/tw1"
    assert public["credentials"]["twilioAccountSid"] == SID
    assert public["credentials"]["twilioAuthToken"] == "…abcd"
    assert TOKEN not in str(public)


def test_an_infobip_number_has_no_status_callback_url():
    number = PhoneNumber(id="ib1", kind=NumberKind.SIM, phoneNumber="+923001112222")
    public = number.public(base_url="https://api.example.com")
    assert public["webhookUrl"] == "https://api.example.com/api/webhooks/infobip/line/ib1"
    assert public["statusCallbackUrl"] is None


async def test_the_auth_token_is_sealed_in_storage(fake_db):
    from app.repositories import numbers as repo

    await repo.save(_twilio_number())
    raw = fake_db.tables["voice_numbers"]["tw1"]["data"]
    assert raw["twilioAuthToken"] and raw["twilioAuthToken"] != TOKEN
    assert raw["twilioAccountSid"] == SID  # an id, not a secret

    loaded = await repo.get("700", "tw1")
    assert loaded.twilio_auth_token == TOKEN


def test_the_overlay_carries_the_twilio_credentials_and_says_so():
    from app.repositories.numbers import as_tenant

    overlay = as_tenant(Tenant(phoneNumberId="700"), _twilio_number())
    assert overlay.carrier == "twilio"
    assert overlay.line_id == "tw1"
    assert overlay.twilio_account_sid == SID
    assert overlay.twilio_auth_token == TOKEN
    assert overlay.infobip_phone_number == "+15551234567"  # the line's caller ID
    assert overlay.infobip_api_key == ""


def test_an_infobip_overlay_is_marked_infobip():
    from app.repositories.numbers import as_tenant

    number = PhoneNumber(id="ib1", kind=NumberKind.SIM, phoneNumber="+923001112222",
                         infobipApiKey="k" * 12)
    assert as_tenant(Tenant(phoneNumberId="700"), number).carrier == "infobip"


@pytest.mark.parametrize("allowed, expected", [(True, "platform-token"), (False, "")])
def test_platform_twilio_credentials_need_the_operators_permission(monkeypatch, allowed, expected):
    from app.config import settings
    from app.repositories.numbers import as_tenant

    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "9" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", "platform-token")
    number = PhoneNumber(id="tw2", kind=NumberKind.TWILIO, phoneNumber="+15550000000",
                         usePlatformCredentials=True)
    overlay = as_tenant(Tenant(phoneNumberId="700", allowPlatformCredentials=allowed), number)
    assert overlay.twilio_auth_token == expected


async def test_a_company_row_never_stores_twilio_fields(fake_db):
    from app.repositories import tenants as tenant_repo

    await tenant_repo.save(Tenant(phoneNumberId="701", name="Acme"))
    data = fake_db.tables[tenant_repo.TABLE]["701"]["data"]
    assert "carrier" not in data
    assert "twilioAuthToken" not in data
    assert "twilioAccountSid" not in data


def test_changing_twilio_credentials_sends_the_number_back_to_verification():
    from app.models.number import CREDENTIAL_FIELDS, SECRET_ATTRS

    assert {"twilio_account_sid", "twilio_auth_token", "phone_number"} <= CREDENTIAL_FIELDS
    assert "twilio_auth_token" in SECRET_ATTRS
