"""
The SIM voice pipeline: how the agent gets onto a phone call.

Infobip carries two-way audio to software by dialling a *dialog leg* into a
websocket it already knows about, not by starting a media stream at a URL we
hand it. The difference is invisible until a real call connects and the caller
hears silence, so it is pinned here.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.agent import claims


@pytest.fixture(autouse=True)
def _clear_claims():
    claims._waiting.clear()
    yield
    claims._waiting.clear()


# ---------------------------------------------------------------------------
# Reserving a call for the socket that is about to arrive
# ---------------------------------------------------------------------------

def test_a_socket_claims_the_call_reserved_for_it():
    claims.reserve("call-1", "tenant-a")
    assert claims.claim() == ("call-1", "tenant-a")
    assert claims.claim() is None, "the same reservation was handed out twice"


def test_reservations_are_claimed_oldest_first():
    claims.reserve("call-1", "t")
    claims.reserve("call-2", "t")
    assert claims.claim()[0] == "call-1"
    assert claims.claim()[0] == "call-2"


def test_a_cancelled_reservation_is_not_claimed():
    """A call that dies before its leg connects must not hand itself to
    whoever rings next."""
    claims.reserve("call-1", "t")
    claims.reserve("call-2", "t")
    claims.cancel("call-1")
    assert claims.claim()[0] == "call-2"
    assert claims.claim() is None


def test_a_stale_reservation_expires(monkeypatch):
    claims.reserve("call-1", "t")
    later = claims.time.monotonic() + claims.TTL_SECONDS + 1
    monkeypatch.setattr(claims.time, "monotonic", lambda: later)
    assert claims.claim() is None, "a long-dead call was still claimable"


# ---------------------------------------------------------------------------
# What is actually sent to Infobip
# ---------------------------------------------------------------------------

class _Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = str(self._body)
        self.content = b"{}"
        self.headers = {}

    def json(self):
        return self._body


def _carrier(monkeypatch, capture: list[tuple[str, str, dict]], body=None):
    from app.models.tenant import Tenant
    from app.services import telephony

    tenant = Tenant(
        phoneNumberId="t-1", name="Acme", infobipApiKey="k",
        infobipBaseUrl="https://x.api.infobip.com", infobipPhoneNumber="+923001112222",
    )
    carrier = telephony.Infobip(tenant)

    async def fake_request(method, path, **kw):
        capture.append((method, path, kw.get("json") or {}))
        return _Response(body=body)

    monkeypatch.setattr(carrier, "_request", fake_request)
    return carrier


def test_the_agent_leg_is_a_dialog_not_a_media_stream(monkeypatch):
    """`start-media-stream` is a one-way tap for recording. Using it for the
    agent is a call that connects and then stays silent."""
    sent: list[tuple[str, str, dict]] = []
    carrier = _carrier(monkeypatch, sent)

    asyncio.run(carrier.bridge_to_websocket(
        "provider-call-1", websocket_config_id="cfg-9", from_number="+923001112222",
    ))

    method, path, payload = sent[0]
    assert (method, path) == ("POST", "/calls/1/dialogs")
    assert payload["parentCallId"] == "provider-call-1"
    endpoint = payload["childCallRequest"]["endpoint"]
    assert endpoint == {"type": "WEBSOCKET", "websocketEndpointConfigId": "cfg-9"}
    assert "mediaStream" not in payload


def test_bridging_without_a_registered_endpoint_is_refused(monkeypatch):
    from app.errors import AppError

    carrier = _carrier(monkeypatch, [])
    with pytest.raises(AppError) as refused:
        asyncio.run(carrier.bridge_to_websocket("c-1", websocket_config_id=""))
    assert refused.value.code == "no_websocket_config"


def test_the_endpoint_config_sends_the_sample_rate_as_a_string(monkeypatch):
    """The API rejects the number outright, and says only that the body is
    'missing or not valid' — naming no field."""
    sent: list[tuple[str, str, dict]] = []
    carrier = _carrier(monkeypatch, sent, body={"id": "cfg-1"})

    asyncio.run(carrier.create_websocket_endpoint_config("agent audio", "wss://x/api/media/infobip"))

    _, path, payload = sent[0]
    assert path == "/calls/1/media-stream-configs"
    assert payload["type"] == "WEBSOCKET_ENDPOINT"
    assert payload["sampleRate"] == "16000"
    assert isinstance(payload["sampleRate"], str)


# ---------------------------------------------------------------------------
# Registering this deployment's socket in the company's own account
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_existing_endpoint_for_the_same_url_is_reused(fake_db, monkeypatch):
    """A redeploy must not litter the customer's Infobip account with a new
    endpoint every boot."""
    from app.config import settings
    from app.models.number import NumberKind, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import numbers as number_repo
    from app.services import lines, telephony

    monkeypatch.setattr(settings, "public_base_url", "https://agent.example.com")
    url = "wss://agent.example.com/api/media/infobip"
    assert lines.audio_socket_url() == url

    created: list[str] = []

    async def existing(self):
        return [{"id": "cfg-existing", "type": "WEBSOCKET_ENDPOINT", "url": url}]

    async def create(self, name, u):
        created.append(u)
        return {"id": "cfg-new"}

    monkeypatch.setattr(telephony.Infobip, "websocket_endpoint_configs", existing)
    monkeypatch.setattr(telephony.Infobip, "create_websocket_endpoint_config", create)

    tenant = Tenant(phoneNumberId="t-9", name="Acme", infobipApiKey="k",
                    infobipBaseUrl="https://x.api.infobip.com")
    number = await number_repo.save(PhoneNumber(
        tenantId="t-9", kind=NumberKind.SIM, phoneNumber="+923001112222",
    ))

    config_id = await lines.ensure_audio_endpoint(tenant, number)
    assert config_id == "cfg-existing"
    assert created == [], "a duplicate endpoint was registered"
    # Remembered, so the next call does not ask Infobip again.
    stored = await number_repo.get("t-9", number.id)
    assert stored.infobip_websocket_config_id == "cfg-existing"


@pytest.mark.asyncio
async def test_a_missing_public_base_url_is_a_clear_refusal(fake_db, monkeypatch):
    from app.config import settings
    from app.errors import AppError
    from app.models.number import NumberKind, PhoneNumber
    from app.models.tenant import Tenant
    from app.services import lines

    monkeypatch.setattr(settings, "public_base_url", "")
    with pytest.raises(AppError) as refused:
        await lines.ensure_audio_endpoint(
            Tenant(phoneNumberId="t", name="A"),
            PhoneNumber(tenantId="t", kind=NumberKind.SIM, phoneNumber="+92300"),
        )
    assert refused.value.code == "no_public_base_url"


# ---------------------------------------------------------------------------
# Finding the number a call arrived on
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_call_on_the_per_company_webhook_still_finds_its_number(fake_db):
    """The older webhook names the company and nothing else. Without this the
    bridge found no number, no audio endpoint, and gave up — which is a SIM
    call that connects and dies one second after the caller answers."""
    from app.api.routes.webhooks import _line_for
    from app.models.call import Call, Channel, Direction
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import numbers as number_repo

    tenant = Tenant(phoneNumberId="co-1", name="Acme", infobipPhoneNumber="+92516028402")
    line = await number_repo.save(PhoneNumber(
        tenantId="co-1", kind=NumberKind.SIM, phoneNumber="+92516028402",
        status=NumberStatus.VERIFIED, infobipWebsocketConfigId="cfg-1",
    ))
    # No line on the tenant (per-company webhook) and none on the call yet.
    call = Call(tenantId="co-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923191611020", fromNumber="+92516028402")
    found = await _line_for(tenant, call)
    assert found is not None and found.id == line.id


@pytest.mark.asyncio
async def test_the_number_recorded_on_the_call_wins(fake_db):
    from app.api.routes.webhooks import _line_for
    from app.models.call import Call, Channel, Direction
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import numbers as number_repo

    tenant = Tenant(phoneNumberId="co-2", name="Acme")
    first = await number_repo.save(PhoneNumber(
        tenantId="co-2", kind=NumberKind.SIM, phoneNumber="+92516028402",
        status=NumberStatus.VERIFIED))
    second = await number_repo.save(PhoneNumber(
        tenantId="co-2", kind=NumberKind.SIM, phoneNumber="+92519999999",
        status=NumberStatus.VERIFIED))
    call = Call(tenantId="co-2", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                counterparty="+923191611020", lineId=second.id)
    found = await _line_for(tenant, call)
    assert found.id == second.id and found.id != first.id


@pytest.mark.asyncio
async def test_a_whatsapp_call_never_matches_a_sim_number(fake_db):
    """Matching on digits alone would hand a WhatsApp call a carrier line."""
    from app.api.routes.webhooks import _line_for
    from app.models.call import Call, Channel, Direction
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import numbers as number_repo

    tenant = Tenant(phoneNumberId="co-3", name="Acme")
    await number_repo.save(PhoneNumber(
        tenantId="co-3", kind=NumberKind.SIM, phoneNumber="+92516028402",
        status=NumberStatus.VERIFIED))
    call = Call(tenantId="co-3", channel=Channel.WHATSAPP_CALL,
                direction=Direction.INBOUND, counterparty="+92319",
                fromNumber="+92516028402")
    assert await _line_for(tenant, call) is None


@pytest.mark.asyncio
async def test_an_unmatched_call_on_a_company_with_numbers_is_not_answered(fake_db):
    """Connecting numbers and then taking a call on none of them is a
    misconfiguration, not an invitation to answer with whatever is to hand."""
    from app.api.routes.webhooks import _inbound_allowed_for
    from app.models.call import Call, Channel, Direction
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import numbers as number_repo

    tenant = Tenant(phoneNumberId="co-4", name="Acme")
    await number_repo.save(PhoneNumber(
        tenantId="co-4", kind=NumberKind.SIM, phoneNumber="+92511111111",
        status=NumberStatus.VERIFIED, inboundAgentId="a-1"))
    stray = Call(tenantId="co-4", channel=Channel.PHONE, direction=Direction.INBOUND,
                 counterparty="+92319", fromNumber="+92517777777")
    # Two numbers so there is no unambiguous single candidate to fall back to.
    await number_repo.save(PhoneNumber(
        tenantId="co-4", kind=NumberKind.SIM, phoneNumber="+92512222222",
        status=NumberStatus.VERIFIED, inboundAgentId="a-1"))
    assert await _inbound_allowed_for(tenant, stray) is False


@pytest.mark.asyncio
async def test_a_company_with_no_numbers_answers_as_it_always_did(fake_db):
    """The arrangement from before numbers existed must keep working."""
    from app.api.routes.webhooks import _inbound_allowed_for
    from app.models.call import Call, Channel, Direction
    from app.models.tenant import Tenant

    tenant = Tenant(phoneNumberId="co-5", name="Acme")
    call = Call(tenantId="co-5", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+92319")
    assert await _inbound_allowed_for(tenant, call) is True
