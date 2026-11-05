"""
Twilio around the edges: recordings, the health check and the help text.

A Twilio call is recorded by the agent's own session, as an Infobip call is,
so the one thing to pin about recordings is that nothing ever goes asking
Infobip for a Twilio call's audio.
"""
from __future__ import annotations

import asyncio

import pytest

SID = "AC" + "0" * 32
CALL_SID = "CA" + "1" * 32


@pytest.fixture
def company(client, tenant_factory, auth, fake_db, monkeypatch):
    """Company 700, signed in, with an Infobip carrier that must not be asked."""
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.repositories import numbers as repo
    from app.services import telephony

    asked: list[str] = []

    async def refuse(self, *args, **kwargs):
        asked.append(args[0] if args else "")
        return []

    monkeypatch.setattr(telephony.Infobip, "call_recordings", refuse)
    monkeypatch.setattr(telephony.Infobip, "dialog_recordings", refuse)
    _tenant, key = tenant_factory("700", "Acme", infobipApiKey="k",
                                  infobipBaseUrl="https://x", infobipPhoneNumber="+9251")
    asyncio.run(repo.save(PhoneNumber(
        id="tw1", tenantId="700", kind=NumberKind.TWILIO, label="Twilio",
        phoneNumber="+15551234567", twilioAccountSid=SID, twilioAuthToken="t",
        status=NumberStatus.VERIFIED)))
    return {"headers": auth(key), "asked": asked}


def _call(**extra):
    from app.models.call import Call, CallStatus, Channel, Direction, RecordingState
    from app.repositories import calls as call_repo

    call = Call(tenantId="700", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.COMPLETED, counterparty="+923001112222",
                recordingState=RecordingState.READY, **extra)
    asyncio.run(call_repo.save_call(call))
    return call


def test_a_twilio_call_never_asks_infobip_for_its_recording(client, company):
    from app.models.call import RecordingState
    from app.repositories import calls as call_repo

    call = _call(lineId="tw1", providerCallId=CALL_SID)
    resp = client.post(f"/api/calls/{call.id}/recording/fetch", headers=company["headers"])
    assert resp.status_code == 409
    assert "recorded by the agent" in resp.json()["error"]["message"]
    assert company["asked"] == [], "Infobip was asked about a Twilio call"
    # The recording the session stored is left exactly as it was.
    back = asyncio.run(call_repo.get_call("700", call.id))
    assert back.recording_state is RecordingState.READY


def test_an_infobip_call_still_asks_infobip(client, company):
    """The guard is Twilio's alone: a SIM call goes to Infobip as before."""
    call = _call(providerCallId="ib-call-1")
    resp = client.post(f"/api/calls/{call.id}/recording/fetch", headers=company["headers"])
    assert resp.status_code == 202
    assert resp.json()["status"] == "absent"
    assert company["asked"] == ["ib-call-1"]


def test_health_reports_twilio_without_naming_secrets(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "twilio_account_sid", "")
    monkeypatch.setattr(settings, "twilio_auth_token", "")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")
    off = client.get("/health").json()["capabilities"]
    assert off["twilio"] is False and off["twilioReachable"] is False

    monkeypatch.setattr(settings, "twilio_account_sid", SID)
    monkeypatch.setattr(settings, "twilio_auth_token", "platform-secret-token")
    monkeypatch.setattr(settings, "public_base_url", "https://api.example.com")
    resp = client.get("/health")
    on = resp.json()["capabilities"]
    assert on["twilio"] is True and on["twilioReachable"] is True
    assert "platform-secret-token" not in resp.text and SID not in resp.text


def test_the_help_assistant_knows_twilio_lines():
    from app.services.help import PRODUCT_GUIDE

    for fact in ("Phone line (Twilio)", "Account SID", "Verified Caller IDs",
                 "Geo permissions", "Verify again"):
        assert fact in " ".join(PRODUCT_GUIDE.split()), fact
