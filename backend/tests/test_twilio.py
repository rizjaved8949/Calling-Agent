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


# ---------------------------------------------------------------------------
# Step 4: webhooks
# ---------------------------------------------------------------------------


def test_signature_matches_twilios_worked_example():
    """From twilio.com/docs/usage/security, "Explore the algorithm yourself"."""
    from app.services.twilio_client import expected_signature, signature_ok

    params = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+14158675310"),
              ("Digits", "1234"), ("From", "+14158675310"), ("To", "+18005551212")]
    url = "https://example.com/myapp.php?foo=1&bar=2"
    assert expected_signature("12345", url, params) == "L/OH5YylLD5NRKLltdqwSvS0BnU="
    assert signature_ok("12345", url, params, "L/OH5YylLD5NRKLltdqwSvS0BnU=")


def test_a_tampered_request_fails_the_signature():
    from app.services.twilio_client import signature_ok

    url = "https://example.com/myapp.php?foo=1&bar=2"
    good = "L/OH5YylLD5NRKLltdqwSvS0BnU="
    params = [("CallSid", "CA1234567890ABCDE"), ("Caller", "+14158675310"),
              ("Digits", "1234"), ("From", "+14158675310"), ("To", "+18005551212")]
    tampered = [(k, "+10000000000" if k == "From" else v) for k, v in params]
    assert not signature_ok("12345", url, tampered, good)
    assert not signature_ok("wrong-token", url, params, good)
    assert not signature_ok("12345", url, params, None)
    assert not signature_ok("", url, params, good)


INBOUND_SID = "CA" + "3" * 32


@pytest.fixture
def twilio_line(fake_db, monkeypatch):
    """A verified Twilio number answering inbound with an agent, plus spies on
    the background work the webhooks start."""
    import asyncio

    from app.config import settings
    from app.models.number import NumberMode, NumberStatus
    from app.repositories import numbers as repo
    from app.repositories import tenants as tenant_repo
    from app.api.routes import webhooks
    from app.services import reports
    from app.services.agent import live

    # The in-process "seen this CallSid" claims outlive a test; several tests
    # ring with the same CallSid, as no real pair of calls ever would.
    monkeypatch.setattr(webhooks, "_seen_inbound", {})
    monkeypatch.setattr(settings, "public_base_url", BASE)
    asyncio.run(tenant_repo.save(Tenant(phoneNumberId="700", name="Acme")))
    number = _twilio_number(status=NumberStatus.VERIFIED, mode=NumberMode.BOTH,
                            inboundAgentId="agent-1")
    asyncio.run(repo.save(number))

    spy = {"warmed": [], "finished": [], "synced": 0, "live": set()}

    async def warm_up(_tenant, call, **_kw):
        spy["warmed"].append(call.id)

    async def finish(_tenant, call_id, _reason=""):
        spy["finished"].append(call_id)

    def synced(_tenant, delay=None):
        spy["synced"] += 1

    monkeypatch.setattr(live, "warm_up", warm_up)
    monkeypatch.setattr(live, "finish", finish)
    monkeypatch.setattr(live, "get", lambda call_id: object() if call_id in spy["live"] else None)
    monkeypatch.setattr(reports, "schedule_sync", synced)
    return spy


def _twilio_post(client, path: str, form: dict, *, token: str = TOKEN, signature=None):
    from app.services.twilio_client import expected_signature

    sig = signature or expected_signature(token, BASE + path, list(form.items()))
    return client.post(path, data=form, headers={"X-Twilio-Signature": sig})


def _ring(client, call_sid: str = INBOUND_SID, **extra):
    form = {"CallSid": call_sid, "AccountSid": SID, "From": "+923001112222",
            "To": "+15551234567", "CallStatus": "ringing", "Direction": "inbound", **extra}
    return _twilio_post(client, "/api/webhooks/twilio/voice/tw1", form)


def _calls(fake_db):
    from app.models.call import Call
    from app.repositories import calls as call_repo

    rows = fake_db.tables.get(call_repo.CALLS_TABLE, {}).values()
    return [Call.model_validate({**r["data"], "id": r["id"]}) for r in rows]


def _stream_of(xml: str) -> tuple[str, str]:
    """(socket URL, token) from Connect/Stream TwiML; fails if it is not that."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    assert root.tag == "Response"
    stream = root.find("./Connect/Stream")
    assert stream is not None, xml
    assert root.find("./Start") is None  # one-way streams would leave the agent mute
    token = stream.find("./Parameter[@name='token']").get("value")
    return stream.get("url"), token


def test_an_inbound_call_is_connected_to_the_agents_socket(client, twilio_line, fake_db):
    from app.models.call import CallStatus, Direction
    from app.security import tickets
    from app.services.twilio_client import MEDIA_AUDIENCE

    resp = _ring(client)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    [call] = _calls(fake_db)
    assert call.direction is Direction.INBOUND
    assert call.status is CallStatus.RINGING
    assert call.provider_call_id == INBOUND_SID
    assert call.line_id == "tw1"
    assert call.counterparty == "+923001112222"

    url, token = _stream_of(resp.text)
    assert url == f"wss://api.example.com/api/media/twilio/{call.id}"
    assert tickets.verify(token, MEDIA_AUDIENCE)["s"] == call.id
    assert twilio_line["warmed"] == [call.id]


def test_the_stream_token_expires_in_about_two_minutes(client, twilio_line):
    import base64
    import json
    import time

    _url, token = _stream_of(_ring(client).text)
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert 100 <= payload["x"] - time.time() <= 125


def test_the_same_inbound_call_delivered_twice_is_one_call(client, twilio_line, fake_db):
    first = _ring(client)
    second = _ring(client)
    assert len(_calls(fake_db)) == 1
    assert _stream_of(first.text)[0] == _stream_of(second.text)[0]


@pytest.mark.parametrize("field, value", [("mode", "outbound"), ("inbound_agent_id", "")])
def test_a_number_that_does_not_answer_rejects_and_records_why(client, twilio_line, fake_db,
                                                               field, value):
    import asyncio

    from app.models.call import CallStatus
    from app.models.number import NumberMode
    from app.repositories import numbers as repo

    number = asyncio.run(repo.get("700", "tw1"))
    setattr(number, field, NumberMode(value) if field == "mode" else value)
    asyncio.run(repo.save(number))

    resp = _ring(client)
    assert resp.status_code == 200
    assert "<Reject/>" in resp.text
    [call] = _calls(fake_db)
    assert call.status is CallStatus.FAILED
    assert "not set up to answer" in call.error
    assert twilio_line["warmed"] == []


def test_a_suspended_companys_call_is_rejected(client, twilio_line, fake_db):
    import asyncio

    from app.api.routes import webhooks
    from app.models.call import CallStatus
    from app.repositories import tenants as tenant_repo

    company = asyncio.run(tenant_repo.get("700"))
    company.suspended = True
    asyncio.run(tenant_repo.save(company))

    resp = _ring(client)
    assert resp.status_code == 200
    assert "<Reject/>" in resp.text and "<Stream" not in resp.text
    [call] = _calls(fake_db)
    assert call.status is CallStatus.FAILED
    assert call.error == webhooks.SUSPENDED_REASON
    assert twilio_line["warmed"] == []


def test_a_bad_signature_is_refused_and_nothing_is_stored(client, twilio_line, fake_db):
    form = {"CallSid": INBOUND_SID, "From": "+923001112222", "Direction": "inbound"}
    resp = _twilio_post(client, "/api/webhooks/twilio/voice/tw1", form, signature="forged=")
    assert resp.status_code == 403
    signed_with_wrong_token = _twilio_post(client, "/api/webhooks/twilio/voice/tw1", form,
                                           token="not-the-token")
    assert signed_with_wrong_token.status_code == 403
    assert _calls(fake_db) == []
    assert twilio_line["warmed"] == []


def test_an_unknown_number_is_404(client, twilio_line):
    resp = _twilio_post(client, "/api/webhooks/twilio/voice/nope", {"CallSid": INBOUND_SID})
    assert resp.status_code == 404


def test_an_infobip_number_does_not_take_twilio_webhooks(client, twilio_line):
    import asyncio

    from app.repositories import numbers as repo

    asyncio.run(repo.save(PhoneNumber(id="ib9", tenantId="700", kind=NumberKind.SIM,
                                      phoneNumber="+923000000000")))
    resp = _twilio_post(client, "/api/webhooks/twilio/voice/ib9", {"CallSid": INBOUND_SID})
    assert resp.status_code == 404


def test_without_https_the_inbound_call_is_rejected(client, twilio_line, fake_db, monkeypatch):
    """Twilio can reach a plain http URL; it cannot stream audio to ws://."""
    from app.config import settings
    from app.models.call import CallStatus
    from app.services.twilio_client import expected_signature

    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")
    path = "/api/webhooks/twilio/voice/tw1"
    form = {"CallSid": INBOUND_SID, "From": "+923001112222", "Direction": "inbound"}
    sig = expected_signature(TOKEN, "http://localhost:8000" + path, list(form.items()))
    resp = client.post(path, data=form, headers={"X-Twilio-Signature": sig})
    assert "<Reject/>" in resp.text
    [call] = _calls(fake_db)
    assert call.status is CallStatus.FAILED


# ---- outbound: the person we rang picks up --------------------------------


def _outbound_call(fake_db, **extra):
    import asyncio

    from app.models.call import Call, CallStatus, Direction
    from app.repositories import calls as call_repo

    fields = dict(id="out1", tenantId="700", direction=Direction.OUTBOUND,
                  status=CallStatus.RINGING, lineId="tw1", providerCallId=CALL_SID,
                  counterparty="+923001112222")
    call = Call(**{**fields, **extra})
    asyncio.run(call_repo.save_call(call))
    return call


def test_an_answered_outbound_call_is_connected_to_the_agent(client, twilio_line, fake_db):
    from app.security import tickets
    from app.services.twilio_client import MEDIA_AUDIENCE

    _outbound_call(fake_db)
    resp = _twilio_post(client, "/api/webhooks/twilio/outbound/tw1/out1",
                        {"CallSid": CALL_SID, "CallStatus": "in-progress",
                         "Direction": "outbound-api"})
    url, token = _stream_of(resp.text)
    assert url == "wss://api.example.com/api/media/twilio/out1"
    assert tickets.verify(token, MEDIA_AUDIENCE)["s"] == "out1"
    assert twilio_line["warmed"] == ["out1"]


@pytest.mark.parametrize("path, sid", [
    ("/api/webhooks/twilio/outbound/tw1/out1", "CA" + "9" * 32),  # someone else's call
    ("/api/webhooks/twilio/outbound/tw1/missing", CALL_SID),      # no such call
])
def test_an_answer_that_does_not_match_the_call_hangs_up(client, twilio_line, fake_db, path, sid):
    _outbound_call(fake_db)
    resp = _twilio_post(client, path, {"CallSid": sid, "Direction": "outbound-api"})
    assert "<Hangup/>" in resp.text
    assert twilio_line["warmed"] == []


def test_an_answer_before_the_sid_was_saved_keeps_it(client, twilio_line, fake_db):
    import asyncio

    from app.repositories import calls as call_repo

    _outbound_call(fake_db, providerCallId="")
    resp = _twilio_post(client, "/api/webhooks/twilio/outbound/tw1/out1",
                        {"CallSid": CALL_SID, "Direction": "outbound-api"})
    assert "<Stream" in resp.text
    assert asyncio.run(call_repo.get_call("700", "out1")).provider_call_id == CALL_SID


# ---- status callbacks --------------------------------------------------------


def _status(client, status: str, sid: str = CALL_SID, **extra):
    return _twilio_post(client, "/api/webhooks/twilio/status/tw1",
                        {"CallSid": sid, "CallStatus": status, **extra})


def _get(call_id="out1"):
    import asyncio

    from app.repositories import calls as call_repo

    return asyncio.run(call_repo.get_call("700", call_id))


def test_an_answered_call_settles_as_completed_with_its_duration(client, twilio_line, fake_db):
    from app.models.call import CallStatus

    _outbound_call(fake_db)
    twilio_line["live"].add("out1")
    assert _status(client, "in-progress").status_code == 200
    assert _get().status is CallStatus.IN_PROGRESS
    assert _get().answered_at

    _status(client, "completed", CallDuration="42")
    call = _get()
    assert call.status is CallStatus.COMPLETED
    assert call.duration_seconds == 42
    assert call.ended_at
    assert twilio_line["finished"] == ["out1"]
    assert twilio_line["synced"] == 1


def test_a_repeated_final_status_is_ignored(client, twilio_line, fake_db):
    _outbound_call(fake_db)
    twilio_line["live"].add("out1")
    _status(client, "completed", CallDuration="10")
    first_end = _get().ended_at
    _status(client, "completed", CallDuration="99")
    assert _get().duration_seconds == 10
    assert _get().ended_at == first_end
    assert twilio_line["synced"] == 1
    assert twilio_line["finished"] == ["out1"]


@pytest.mark.parametrize("outcome, expected, words", [
    ("busy", "NO_ANSWER", "busy"),
    ("no-answer", "NO_ANSWER", ""),
    ("canceled", "NO_ANSWER", ""),
    ("failed", "FAILED", "could not connect"),
])
def test_unanswered_endings(client, twilio_line, fake_db, outcome, expected, words):
    _outbound_call(fake_db)
    _status(client, outcome)
    call = _get()
    assert call.status.value == expected
    assert words in (call.error or "")
    assert call.recording_state.value == "NONE"
    assert call.duration_seconds == 0


def test_a_completed_call_without_an_in_progress_callback_still_counts(client, twilio_line,
                                                                       fake_db):
    from app.models.call import CallStatus

    _outbound_call(fake_db)
    _status(client, "completed", CallDuration="30")
    call = _get()
    assert call.status is CallStatus.COMPLETED
    assert call.answered_at and round(call.ended_at - call.answered_at) == 30


def test_a_late_ringing_does_not_undo_in_progress(client, twilio_line, fake_db):
    from app.models.call import CallStatus

    _outbound_call(fake_db)
    _status(client, "in-progress")
    _status(client, "ringing")
    assert _get().status is CallStatus.IN_PROGRESS


def test_a_status_for_an_unknown_call_is_accepted_and_ignored(client, twilio_line, fake_db):
    resp = _status(client, "initiated", sid="CA" + "8" * 32)
    assert resp.status_code == 200
    assert _calls(fake_db) == []


def test_a_status_with_a_bad_signature_changes_nothing(client, twilio_line, fake_db):
    from app.models.call import CallStatus

    _outbound_call(fake_db)
    resp = _twilio_post(client, "/api/webhooks/twilio/status/tw1",
                        {"CallSid": CALL_SID, "CallStatus": "completed"}, signature="nope")
    assert resp.status_code == 403
    assert _get().status is CallStatus.RINGING
