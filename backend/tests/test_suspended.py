"""
A suspended company's calls are declined on every carrier.

Suspension used to stop only the signed-in API (`api/deps.py`). The carriers
ring our webhooks directly, so an operator could switch a company off and its
agent would still pick up the phone. These pin the decline for Infobip and
WhatsApp; the Twilio one is in test_twilio.py, beside the fixture it needs.
"""
from __future__ import annotations

import pytest


async def _sim_line(company_id: str, *, suspended: bool):
    """A company with one SIM line that would answer, but for suspension."""
    from app.models.call import Call, Channel, Direction
    from app.models.number import NumberKind, NumberMode, NumberStatus, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import numbers as number_repo

    tenant = Tenant(phoneNumberId=company_id, name="Acme", suspended=suspended,
                    suspendedReason="Unpaid invoice." if suspended else "")
    line = await number_repo.save(PhoneNumber(
        tenantId=company_id, kind=NumberKind.SIM, phoneNumber="+92516028402",
        status=NumberStatus.VERIFIED, mode=NumberMode.BOTH, inboundAgentId="a-1"))
    call = Call(tenantId=company_id, channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923191611020", providerCallId="ib-1", lineId=line.id,
                fromNumber="+92516028402")
    return tenant, call


@pytest.fixture
def infobip_spy(monkeypatch):
    from app.api.routes import webhooks
    from app.services import telephony

    spy = {"hung_up": [], "bridged": []}

    async def hangup(self, call_id):
        spy["hung_up"].append(call_id)

    async def bridge(_tenant, call):
        spy["bridged"].append(call.id)

    monkeypatch.setattr(telephony.Infobip, "hangup", hangup)
    monkeypatch.setattr(webhooks, "_bridge_agent", bridge)
    return spy


@pytest.mark.asyncio
async def test_a_suspended_companys_sim_call_is_hung_up_and_recorded(fake_db, infobip_spy):
    from app.api.routes import webhooks
    from app.models.call import CallStatus

    tenant, call = await _sim_line("sus-1", suspended=True)
    await webhooks._answer_inbound(tenant, call)

    assert infobip_spy["bridged"] == [], "the agent was put on a suspended company's call"
    assert infobip_spy["hung_up"] == ["ib-1"]
    assert call.status is CallStatus.FAILED
    assert call.error == webhooks.SUSPENDED_REASON
    # The operator's private reason is for the company, not the call log.
    assert "Unpaid" not in call.error


@pytest.mark.asyncio
async def test_the_same_sim_call_is_answered_once_the_company_is_back(fake_db, infobip_spy):
    from app.api.routes import webhooks

    tenant, call = await _sim_line("sus-2", suspended=False)
    await webhooks._answer_inbound(tenant, call)

    assert infobip_spy["bridged"] == [call.id]
    assert infobip_spy["hung_up"] == []


@pytest.mark.asyncio
async def test_a_suspended_companys_whatsapp_call_is_rejected(fake_db, monkeypatch):
    """Declined even on a server without the WebRTC stack: saying no to a
    call needs no media, and suspension must hold everywhere."""
    from app.api.routes import webhooks
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.models.tenant import Tenant
    from app.services.agent import whatsapp_media

    rejected: list[str] = []

    async def reject_call(self, call_id):
        rejected.append(call_id)

    monkeypatch.setattr(webhooks.WhatsApp, "reject_call", reject_call)
    monkeypatch.setattr(whatsapp_media, "available", lambda: False)

    tenant = Tenant(phoneNumberId="sus-3", name="Acme", suspended=True)
    call = Call(tenantId="sus-3", channel=Channel.WHATSAPP_CALL,
                direction=Direction.INBOUND, counterparty="+923191611020",
                providerCallId="wacid.1")
    await webhooks._answer_whatsapp(tenant, call, "v=0")

    assert rejected == ["wacid.1"]
    assert call.status is CallStatus.FAILED
    assert call.error == webhooks.SUSPENDED_REASON


@pytest.mark.asyncio
async def test_a_number_that_takes_no_calls_still_says_so(fake_db, infobip_spy):
    """The older refusal keeps its own wording, so the call history tells an
    unconfigured number apart from a switched-off company."""
    from app.api.routes import webhooks
    from app.repositories import numbers as number_repo

    tenant, call = await _sim_line("sus-4", suspended=False)
    line = await number_repo.get("sus-4", call.line_id)
    line.inbound_agent_id = ""
    await number_repo.save(line)
    await webhooks._answer_inbound(tenant, call)

    assert call.error == webhooks.NOT_ANSWERING_REASON
    assert infobip_spy["bridged"] == []
