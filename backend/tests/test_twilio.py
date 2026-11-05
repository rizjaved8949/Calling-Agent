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
    fields = dict(
        id="tw1", tenantId="700", kind=NumberKind.TWILIO, label="Twilio line",
        phoneNumber="+15551234567", twilioAccountSid=SID, twilioAuthToken=TOKEN,
    )
    return PhoneNumber(**{**fields, **extra})


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


# ---------------------------------------------------------------------------
# Step 2: the Twilio client, and choosing a carrier
# ---------------------------------------------------------------------------

CALL_SID = "CA" + "1" * 32
PN_SID = "PN" + "2" * 32


class TwilioApi:
    """Twilio's REST API as an httpx mock transport. Records every request."""

    def __init__(self, replies: dict[tuple[str, str], tuple[int, dict]] | None = None):
        self.replies = replies or {}
        self.requests: list = []

    def handler(self, request):
        import httpx

        self.requests.append(request)
        path = request.url.path.removeprefix(f"/2010-04-01/Accounts/{SID}")
        status, body = self.replies.get((request.method, path), (200, {}))
        return httpx.Response(status, json=body)

    def form(self, index: int = -1) -> dict[str, list[str]]:
        from urllib.parse import parse_qs

        return parse_qs(self.requests[index].content.decode())


@pytest.fixture
def twilio_api(monkeypatch):
    import httpx

    from app.services import twilio_client

    api = TwilioApi()
    monkeypatch.setattr(twilio_client, "_client",
                        lambda: httpx.AsyncClient(transport=httpx.MockTransport(api.handler)))
    return api


def _overlay(**extra) -> Tenant:
    from app.repositories.numbers import as_tenant

    return as_tenant(Tenant(phoneNumberId="700"), _twilio_number(**extra))


async def test_placing_a_call_asks_twilio_with_both_urls(twilio_api):
    import base64

    from app.services.twilio_client import Twilio

    twilio_api.replies[("POST", "/Calls.json")] = (201, {"sid": CALL_SID})
    result = await Twilio(_overlay()).place_call(
        "+923001112222", answer_url="https://x/answer", status_url="https://x/status")

    assert result["sid"] == CALL_SID
    request = twilio_api.requests[0]
    assert request.url.path == f"/2010-04-01/Accounts/{SID}/Calls.json"
    assert request.headers["authorization"] == "Basic " + base64.b64encode(
        f"{SID}:{TOKEN}".encode()).decode()
    form = twilio_api.form()
    assert form["To"] == ["+923001112222"]
    assert form["From"] == ["+15551234567"]  # the line's own number
    assert form["Url"] == ["https://x/answer"]
    assert form["StatusCallback"] == ["https://x/status"]
    assert form["StatusCallbackMethod"] == ["POST"]
    assert form["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]


async def test_hanging_up_marks_the_call_completed(twilio_api):
    from app.services.twilio_client import Twilio

    await Twilio(_overlay()).hangup(CALL_SID)
    assert twilio_api.requests[0].url.path.endswith(f"/Calls/{CALL_SID}.json")
    assert twilio_api.form() == {"Status": ["completed"]}


@pytest.mark.parametrize("reply, code, words", [
    ((401, {"code": 20003, "message": "Authenticate"}), "twilio_auth", "Account SID or Auth Token"),
    ((401, {}), "twilio_auth", "Account SID or Auth Token"),
    ((400, {"code": 21219, "message": "unverified"}), "twilio_trial_unverified", "21219"),
    ((400, {"code": 21215, "message": "geo"}), "twilio_geo_permission", "Geo permissions"),
    ((400, {"code": 21211, "message": "bad To"}), "twilio_bad_destination", "21211"),
    ((500, {"code": 99999, "message": "?"}), "twilio_refused", "99999"),
])
async def test_twilio_refusals_become_sentences(twilio_api, reply, code, words):
    from app.services.twilio_client import Twilio, TwilioRefused

    twilio_api.replies[("POST", "/Calls.json")] = reply
    with pytest.raises(TwilioRefused) as caught:
        await Twilio(_overlay()).place_call(
            "+923001112222", answer_url="https://x/a", status_url="https://x/s")
    assert caught.value.code == code
    assert words in caught.value.message
    assert TOKEN not in caught.value.message


async def test_a_malformed_account_sid_never_reaches_twilio(twilio_api):
    from app.errors import AppError
    from app.services.twilio_client import Twilio

    tenant = _overlay(twilioAccountSid="AC123/../../other")
    with pytest.raises(AppError) as caught:
        await Twilio(tenant).account()
    assert caught.value.code == "twilio_bad_account_sid"
    assert twilio_api.requests == []


async def test_a_malformed_call_id_never_reaches_twilio(twilio_api):
    from app.errors import AppError
    from app.services.twilio_client import Twilio

    with pytest.raises(AppError):
        await Twilio(_overlay()).hangup("CA../../Accounts")
    assert twilio_api.requests == []


async def test_without_credentials_a_twilio_line_says_so(twilio_api):
    from app.errors import AppError
    from app.services.twilio_client import Twilio

    tenant = _overlay(twilioAuthToken="")
    assert not Twilio(tenant).configured
    with pytest.raises(AppError) as caught:
        await Twilio(tenant).hangup(CALL_SID)
    assert caught.value.code == "telephony_not_configured"


async def test_finding_and_configuring_a_number(twilio_api):
    from app.services.twilio_client import Twilio

    twilio_api.replies[("GET", "/IncomingPhoneNumbers.json")] = (200, {
        "incoming_phone_numbers": [{"sid": PN_SID, "capabilities": {"voice": True}}]})
    client = Twilio(_overlay())
    found = await client.find_number("+15551234567")
    assert found["sid"] == PN_SID
    assert twilio_api.requests[0].url.params["PhoneNumber"] == "+15551234567"

    await client.configure_number(PN_SID, "https://x/voice", "https://x/status")
    assert twilio_api.requests[1].url.path.endswith(f"/IncomingPhoneNumbers/{PN_SID}.json")
    assert twilio_api.form() == {
        "VoiceUrl": ["https://x/voice"], "VoiceMethod": ["POST"],
        "StatusCallback": ["https://x/status"], "StatusCallbackMethod": ["POST"],
    }


async def test_a_number_not_in_the_account_is_none(twilio_api):
    from app.services.twilio_client import Twilio

    twilio_api.replies[("GET", "/IncomingPhoneNumbers.json")] = (
        200, {"incoming_phone_numbers": []})
    assert await Twilio(_overlay()).find_number("+15550000000") is None


def test_the_carrier_is_chosen_by_the_overlay():
    from app.services.carriers import carrier_for
    from app.services.telephony import Infobip
    from app.services.twilio_client import Twilio

    assert isinstance(carrier_for(_overlay()), Twilio)
    assert isinstance(carrier_for(Tenant(phoneNumberId="700")), Infobip)  # blank = Infobip
    assert isinstance(carrier_for(Tenant(phoneNumberId="700", carrier="infobip")), Infobip)


async def test_a_twilio_call_is_placed_with_urls_naming_the_line_and_call(twilio_api, monkeypatch):
    from app.config import settings
    from app.models.call import Call
    from app.services import carriers

    monkeypatch.setattr(settings, "public_base_url", "https://api.example.com/")
    twilio_api.replies[("POST", "/Calls.json")] = (201, {"sid": CALL_SID})
    call = Call(id="c1", tenantId="700", lineId="tw1", fromNumber="+15551234567")
    await carriers.place_call(_overlay(), call, "+923001112222")

    assert call.provider_call_id == CALL_SID
    form = twilio_api.form()
    assert form["Url"] == ["https://api.example.com/api/webhooks/twilio/outbound/tw1/c1"]
    assert form["StatusCallback"] == ["https://api.example.com/api/webhooks/twilio/status/tw1"]


@pytest.mark.parametrize("base", ["", "http://localhost:8000"])
async def test_a_twilio_call_needs_a_public_https_address(twilio_api, monkeypatch, base):
    from app.config import settings
    from app.errors import AppError
    from app.models.call import Call
    from app.services import carriers

    monkeypatch.setattr(settings, "public_base_url", base)
    with pytest.raises(AppError) as caught:
        await carriers.place_call(_overlay(), Call(id="c1", lineId="tw1"), "+923001112222")
    assert caught.value.code == "no_public_base_url"
    assert twilio_api.requests == []  # nobody's phone rang


async def test_hanging_up_a_twilio_call_goes_to_twilio(twilio_api):
    from app.services import carriers

    await carriers.hang_up(_overlay(), CALL_SID)
    assert len(twilio_api.requests) == 1
    assert twilio_api.requests[0].url.host == "api.twilio.com"


async def test_a_twilio_campaign_is_ready_on_its_own_credentials(fake_db):
    from app.api.routes.campaigns import _phone_line_ready
    from app.repositories import numbers as repo

    company = Tenant(phoneNumberId="700")  # no Infobip settings at all
    await repo.save(_twilio_number())
    assert await _phone_line_ready(company, "tw1")
    await repo.save(_twilio_number(twilioAuthToken=""))
    assert not await _phone_line_ready(company, "tw1")
    assert not await _phone_line_ready(company, "")  # unchanged: company's Infobip


# ---------------------------------------------------------------------------
# Step 3: verifying a Twilio number
# ---------------------------------------------------------------------------

BASE = "https://api.example.com"


@pytest.fixture
def twilio_account(twilio_api, monkeypatch):
    """An active, fully paid account that owns +15551234567 with voice."""
    from app.config import settings

    monkeypatch.setattr(settings, "public_base_url", BASE)
    twilio_api.replies.update({
        ("GET", ".json"): (200, {"sid": SID, "status": "active", "type": "Full"}),
        ("GET", "/IncomingPhoneNumbers.json"): (200, {"incoming_phone_numbers": [
            {"sid": PN_SID, "phone_number": "+15551234567", "capabilities": {"voice": True}},
        ]}),
        ("POST", f"/IncomingPhoneNumbers/{PN_SID}.json"): (200, {"sid": PN_SID}),
    })
    return twilio_api


def _add_twilio(client, auth, key, **fields):
    body = {"kind": "twilio", "phoneNumber": "+15551234567",
            "twilioAccountSid": SID, "twilioAuthToken": TOKEN, **fields}
    return client.post("/api/numbers", json=body, headers=auth(key))


def test_a_twilio_number_is_verified_and_pointed_here(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("710")
    resp = _add_twilio(client, auth, key)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "verified", body["statusDetail"]
    assert "now point here" in body["statusDetail"]
    assert "Trial" not in body["statusDetail"]
    assert body["credentials"]["twilioPhoneNumberSid"] == PN_SID
    assert body["credentials"]["twilioAuthToken"] == "…abcd"
    assert TOKEN not in resp.text

    configure = twilio_account.requests[-1]
    assert configure.url.path.endswith(f"/IncomingPhoneNumbers/{PN_SID}.json")
    assert twilio_account.form() == {
        "VoiceUrl": [f"{BASE}/api/webhooks/twilio/voice/{body['id']}"],
        "VoiceMethod": ["POST"],
        "StatusCallback": [f"{BASE}/api/webhooks/twilio/status/{body['id']}"],
        "StatusCallbackMethod": ["POST"],
    }
    assert body["webhookUrl"] == f"{BASE}/api/webhooks/twilio/voice/{body['id']}"


def test_a_wrong_token_fails_verification(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("711")
    twilio_account.replies[("GET", ".json")] = (401, {"code": 20003, "message": "Authenticate"})
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "failed"
    assert "Account SID or Auth Token" in body["statusDetail"]
    assert body["credentials"]["twilioPhoneNumberSid"] is None


def test_a_number_from_another_account_fails(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("712")
    twilio_account.replies[("GET", "/IncomingPhoneNumbers.json")] = (
        200, {"incoming_phone_numbers": []})
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "failed"
    assert "not a number in this Twilio account" in body["statusDetail"]
    # Nothing was configured on a number we could not find.
    assert not any(r.method == "POST" for r in twilio_account.requests)


def test_a_suspended_account_fails(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("713")
    twilio_account.replies[("GET", ".json")] = (200, {"status": "suspended", "type": "Full"})
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "failed"
    assert "suspended" in body["statusDetail"]


def test_a_number_without_voice_fails(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("714")
    twilio_account.replies[("GET", "/IncomingPhoneNumbers.json")] = (200, {
        "incoming_phone_numbers": [{"sid": PN_SID, "capabilities": {"voice": False}}]})
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "failed"
    assert "voice" in body["statusDetail"]


def test_a_trial_account_is_verified_with_a_note(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("715")
    twilio_account.replies[("GET", ".json")] = (200, {"status": "active", "type": "Trial"})
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "verified"
    assert "Verified Caller IDs" in body["statusDetail"]


def test_missing_fields_are_named(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("716")
    body = _add_twilio(client, auth, key, twilioAuthToken="").json()
    assert body["status"] == "failed"
    assert body["statusDetail"] == "Missing: Auth Token."
    assert twilio_account.requests == []


@pytest.mark.parametrize("base", ["", "http://localhost:8000"])
def test_without_https_the_urls_must_be_pasted(client, tenant_factory, auth, twilio_account,
                                               monkeypatch, base):
    from app.config import settings

    _, key = tenant_factory("717")
    monkeypatch.setattr(settings, "public_base_url", base)
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "verified"
    assert "by hand" in body["statusDetail"]
    assert not any(r.method == "POST" for r in twilio_account.requests)


def test_a_refused_webhook_update_still_verifies_with_a_note(client, tenant_factory, auth,
                                                             twilio_account):
    _, key = tenant_factory("718")
    twilio_account.replies[("POST", f"/IncomingPhoneNumbers/{PN_SID}.json")] = (
        403, {"code": 20403, "message": "Forbidden"})
    body = _add_twilio(client, auth, key).json()
    assert body["status"] == "verified"
    assert "could not be set automatically" in body["statusDetail"]
    assert body["credentials"]["twilioPhoneNumberSid"] == PN_SID


def test_changing_the_token_reverifies(client, tenant_factory, auth, twilio_account):
    _, key = tenant_factory("719")
    number = _add_twilio(client, auth, key).json()
    twilio_account.replies[("GET", ".json")] = (401, {"code": 20003})
    resp = client.patch(f"/api/numbers/{number['id']}",
                        json={"twilioAuthToken": "a-new-token-9999"}, headers=auth(key))
    body = resp.json()
    assert body["status"] == "failed"
    assert body["credentials"]["twilioAuthToken"] == "…9999"
