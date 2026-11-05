"""
Fetching a call's recording from Infobip, with the right account's key.

A SIM line can have its own Infobip account. Its recordings live there, so
asking the company's account instead finds nothing (or, worse, a file from a
different account), and the call is marked as having no recording at all.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def infobip(client, tenant_factory, auth, fake_db, monkeypatch):
    """Company 800 on key "company-key", plus a SIM line on its own "line-key"."""
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.repositories import numbers as repo
    from app.services import telephony

    asked_with: list[str] = []

    async def call_recordings(self, provider_call_id):
        asked_with.append(self.api_key)
        return []

    monkeypatch.setattr(telephony.Infobip, "call_recordings", call_recordings)
    _tenant, key = tenant_factory("800", "Acme", infobipApiKey="company-key",
                                  infobipBaseUrl="https://company.api.infobip.com",
                                  infobipPhoneNumber="+92510000000")
    asyncio.run(repo.save(PhoneNumber(
        id="sim1", tenantId="800", kind=NumberKind.SIM, label="Own account",
        phoneNumber="+92511111111", infobipApiKey="line-key",
        infobipBaseUrl="https://line.api.infobip.com", status=NumberStatus.VERIFIED)))
    return {"headers": auth(key), "asked_with": asked_with}


def _call(**extra):
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.repositories import calls as call_repo

    call = Call(tenantId="800", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.COMPLETED, counterparty="+923001112222",
                providerCallId="ib-call-1", **extra)
    asyncio.run(call_repo.save_call(call))
    return call


def test_a_call_on_a_line_with_its_own_account_asks_that_account(client, infobip):
    call = _call(lineId="sim1")
    resp = client.post(f"/api/calls/{call.id}/recording/fetch", headers=infobip["headers"])
    assert resp.status_code == 202
    assert infobip["asked_with"] == ["line-key"], "the company's account was asked instead"


def test_a_call_on_no_line_still_asks_the_company_account(client, infobip):
    """Calls from before numbers existed keep using the company's credentials."""
    call = _call()
    resp = client.post(f"/api/calls/{call.id}/recording/fetch", headers=infobip["headers"])
    assert resp.status_code == 202
    assert infobip["asked_with"] == ["company-key"]
