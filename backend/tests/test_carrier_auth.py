"""
A carrier refusing a line's key is told to the owner, not hidden in a 502.
"""
from __future__ import annotations

import asyncio

import pytest

from app.errors import AppError, UpstreamError
from app.models.call import Call, CallStatus, Channel, Direction
from app.models.number import NumberKind, NumberStatus, PhoneNumber
from app.models.tenant import Tenant
from app.repositories import numbers as number_repo
from app.services import carriers, telephony


def _sim(**extra) -> PhoneNumber:
    fields = dict(id="sim1", tenantId="111", kind=NumberKind.SIM, label="Lahore line",
                  phoneNumber="+923111111111", status=NumberStatus.VERIFIED,
                  infobipApiKey="key", infobipBaseUrl="https://api-pk2.infobip.com")
    return PhoneNumber(**{**fields, **extra})


def _call() -> Call:
    return Call(tenantId="111", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                counterparty="+923001112222", lineId="sim1")


async def test_an_infobip_401_marks_the_line_failed_with_what_to_do(fake_db, monkeypatch):
    await number_repo.save(_sim())
    company = Tenant(phoneNumberId="111", name="Acme")
    tenant = number_repo.as_tenant(company, await number_repo.get("111", "sim1"))

    async def refused(self, to, *, from_number=""):
        raise UpstreamError("The carrier refused the request (401).", http_status=401)

    monkeypatch.setattr(telephony.Infobip, "place_call", refused)
    with pytest.raises(AppError) as raised:
        await carriers.place_call(tenant, _call(), "+923001112222")
    assert raised.value.status == 409 and raised.value.code == "infobip_auth"
    assert raised.value.message == carriers.INFOBIP_KEY_REJECTED
    assert "Infobip rejected the API key. Update it on Numbers and press Verify." == raised.value.message

    line = await number_repo.get("111", "sim1")
    assert line.status is NumberStatus.FAILED
    assert line.status_detail == carriers.INFOBIP_KEY_REJECTED


async def test_other_carrier_failures_still_come_back_as_they_were(fake_db, monkeypatch):
    await number_repo.save(_sim())
    company = Tenant(phoneNumberId="111", name="Acme")
    tenant = number_repo.as_tenant(company, await number_repo.get("111", "sim1"))

    async def refused(self, to, *, from_number=""):
        raise UpstreamError("The carrier refused the request (500).", http_status=500)

    monkeypatch.setattr(telephony.Infobip, "place_call", refused)
    with pytest.raises(UpstreamError):
        await carriers.place_call(tenant, _call(), "+923001112222")
    assert (await number_repo.get("111", "sim1")).status is NumberStatus.VERIFIED


def test_the_dialer_shows_the_409_and_fails_the_call(client, tenant_factory, auth, monkeypatch):
    _tenant, key = tenant_factory("111", "Acme")
    asyncio.run(number_repo.save(_sim(outboundAgentId="agent-1")))

    async def refused(self, to, *, from_number=""):
        raise UpstreamError("The carrier refused the request (401).", http_status=401)

    monkeypatch.setattr(telephony.Infobip, "place_call", refused)
    resp = client.post("/api/calls", json={"to": "+923001112222", "lineId": "sim1"},
                       headers=auth(key))
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["message"] == carriers.INFOBIP_KEY_REJECTED
    [call] = client.get("/api/calls", headers=auth(key)).json()["calls"]
    assert call["status"] == "FAILED"
    [number] = client.get("/api/numbers", headers=auth(key)).json()["numbers"]
    assert number["status"] == "failed" and "press Verify" in number["statusDetail"]


async def test_a_line_that_is_not_verified_cannot_dial_out(fake_db, monkeypatch):
    """The campaign path never checked; now every path goes through one gate."""
    await number_repo.save(_sim(status=NumberStatus.FAILED,
                                statusDetail="Infobip rejected the API key."))
    company = Tenant(phoneNumberId="111", name="Acme")
    tenant = number_repo.as_tenant(company, await number_repo.get("111", "sim1"))
    dialled: list[str] = []

    async def place(self, to, *, from_number=""):
        dialled.append(to)
        return {"id": "x"}

    monkeypatch.setattr(telephony.Infobip, "place_call", place)
    with pytest.raises(AppError) as raised:
        await carriers.place_call(tenant, _call(), "+923001112222")
    assert raised.value.code == "number_not_verified"
    assert "Lahore line is not verified" in raised.value.message
    assert dialled == []


async def test_a_campaign_on_an_unverified_line_fails_the_contact_not_the_process(fake_db, monkeypatch):
    from app.models.campaign import Campaign, Contact, ContactState
    from app.repositories import campaigns as campaign_repo
    from app.services import campaign_runner

    await number_repo.save(_sim(status=NumberStatus.PENDING))
    tenant = Tenant(phoneNumberId="111", name="Acme", defaultCountryCode="92")
    campaign = Campaign(tenantId="111", name="List", channel=Channel.PHONE, lineId="sim1",
                        contacts=[Contact(number="+923001112222")])
    await campaign_repo.save(campaign)

    async def never(self, to, *, from_number=""):
        raise AssertionError("dialled on an unverified line")

    monkeypatch.setattr(telephony.Infobip, "place_call", never)
    await campaign_runner._call_one(tenant, campaign, campaign.contacts[0])
    fresh = await campaign_repo.get("111", campaign.id)
    [contact] = fresh.contacts
    assert contact.state is ContactState.FAILED
    assert "not verified" in contact.note


async def test_an_infobip_response_carries_its_status(monkeypatch):
    import httpx

    tenant = Tenant(phoneNumberId="111", name="Acme", infobipApiKey="k",
                    infobipBaseUrl="https://api.infobip.com", infobipPhoneNumber="+92311")

    class Client:
        def __init__(self, *a, **k): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def request(self, method, url, **kw):
            return httpx.Response(401, json={"requestError": {}},
                                  request=httpx.Request(method, url))

    monkeypatch.setattr(telephony.httpx, "AsyncClient", Client)
    with pytest.raises(UpstreamError) as raised:
        await telephony.Infobip(tenant).place_call("+923001112222")
    assert raised.value.http_status == 401
