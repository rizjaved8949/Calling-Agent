"""
Numbers: connect, verify, assign agents, and route calls through them.

Verification itself talks to Meta and Infobip, so it is replaced here with a
stand-in that accepts or refuses; what is tested is everything around it —
that an unverified number cannot get an agent, that editing credentials sends
it back to pending, and that a call on a number reaches that number's agent.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def provider(monkeypatch):
    """Meta and Infobip, answering yes unless told otherwise."""
    from app.services import lines

    state = {"ok": True}

    async def fake(_tenant):
        if not state["ok"]:
            raise lines._Refused("refused by the provider")
        return "confirmed"

    monkeypatch.setattr(lines, "_verify_meta", fake)
    monkeypatch.setattr(lines, "_verify_infobip", fake)
    return state


def _sim(client, auth, key, phone="+923001112222", mode="both"):
    return client.post("/api/numbers", json={
        "kind": "sim", "phoneNumber": phone, "mode": mode,
        "infobipApiKey": "ib-secret-key-1234", "infobipBaseUrl": "abc.api.infobip.com",
    }, headers=auth(key))


def _agent(client, auth, key, name="Support", mode="both"):
    return client.post("/api/agents", json={"name": name, "mode": mode},
                       headers=auth(key)).json()


def test_adding_a_number_verifies_it_and_hides_secrets(client, tenant_factory, auth, provider):
    _, key = tenant_factory("500")
    resp = _sim(client, auth, key)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "verified"
    assert body["credentials"]["infobipApiKey"] == "…1234"
    assert body["credentials"]["infobipBaseUrl"] == "https://abc.api.infobip.com"
    assert "ib-secret-key-1234" not in resp.text


def test_refused_credentials_leave_the_number_failed(client, tenant_factory, auth, provider):
    _, key = tenant_factory("501")
    provider["ok"] = False
    body = _sim(client, auth, key).json()
    assert body["status"] == "failed"
    assert "refused" in body["statusDetail"]


def test_an_unverified_number_cannot_get_an_agent(client, tenant_factory, auth, provider):
    _, key = tenant_factory("502")
    provider["ok"] = False
    number = _sim(client, auth, key).json()
    agent = _agent(client, auth, key)
    resp = client.patch(f"/api/numbers/{number['id']}", json={"inboundAgentId": agent["id"]},
                        headers=auth(key))
    assert resp.status_code == 409


def test_agent_mode_must_fit_the_slot(client, tenant_factory, auth, provider):
    _, key = tenant_factory("503")
    number = _sim(client, auth, key).json()
    sales = _agent(client, auth, key, "Sales", mode="outbound")
    resp = client.patch(f"/api/numbers/{number['id']}", json={"inboundAgentId": sales["id"]},
                        headers=auth(key))
    assert resp.status_code == 422
    ok = client.patch(f"/api/numbers/{number['id']}", json={"outboundAgentId": sales["id"]},
                      headers=auth(key))
    assert ok.status_code == 200 and ok.json()["outboundAgentId"] == sales["id"]


def test_changing_credentials_reverifies(client, tenant_factory, auth, provider):
    _, key = tenant_factory("504")
    number = _sim(client, auth, key).json()
    provider["ok"] = False
    body = client.patch(f"/api/numbers/{number['id']}", json={"infobipApiKey": "new-key-5678"},
                        headers=auth(key)).json()
    assert body["status"] == "failed"
    # A blank secret on the edit form means "unchanged", not "clear it".
    body = client.patch(f"/api/numbers/{number['id']}", json={"infobipApiKey": "", "label": "Main"},
                        headers=auth(key)).json()
    assert body["credentials"]["infobipApiKey"] == "…5678"


def test_platform_credentials_need_the_operators_permission(client, tenant_factory, auth, provider):
    _, key = tenant_factory("505")
    resp = client.post("/api/numbers", json={
        "kind": "whatsapp", "phoneNumber": "+923001112222", "usePlatformCredentials": True,
    }, headers=auth(key))
    assert resp.status_code == 403
    _, demo_key = tenant_factory("506", allowPlatformCredentials=True)
    resp = client.post("/api/numbers", json={
        "kind": "whatsapp", "phoneNumber": "+923001112222", "usePlatformCredentials": True,
    }, headers=auth(demo_key))
    assert resp.status_code == 201


def test_outbound_calls_go_out_on_the_numbers_credentials(
    client, tenant_factory, auth, provider, monkeypatch,
):
    from app.services import telephony

    _, key = tenant_factory("507")
    number = _sim(client, auth, key, mode="outbound").json()
    agent = _agent(client, auth, key, "Sales", mode="outbound")
    client.patch(f"/api/numbers/{number['id']}", json={"outboundAgentId": agent["id"]},
                 headers=auth(key))
    seen = {}

    async def fake_place(self, to, *, from_number=""):
        seen["key"] = self.api_key
        seen["from"] = self.tenant.infobip_phone_number
        return {"id": "ib-call-1"}

    monkeypatch.setattr(telephony.Infobip, "place_call", fake_place)
    resp = client.post("/api/calls", json={"to": "+923334445555", "channel": "PHONE"},
                       headers=auth(key))
    assert resp.status_code == 201, resp.text
    assert resp.json()["lineId"] == number["id"]
    assert seen == {"key": "ib-secret-key-1234", "from": "+923001112222"}


def test_an_outbound_number_without_an_agent_needs_the_dialer(
    client, tenant_factory, auth, provider,
):
    _, key = tenant_factory("508")
    _sim(client, auth, key)
    resp = client.post("/api/calls", json={"to": "+923334445555", "channel": "PHONE"},
                       headers=auth(key))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_outbound_agent"


def test_an_inbound_only_number_does_not_place_calls(client, tenant_factory, auth, provider):
    _, key = tenant_factory("509")
    number = _sim(client, auth, key, mode="inbound").json()
    resp = client.post("/api/calls", json={
        "to": "+923334445555", "channel": "PHONE", "lineId": number["id"], "human": True,
    }, headers=auth(key))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_a_call_on_a_number_is_answered_by_its_inbound_agent(fake_db):
    from app.models.agent import Agent, AgentMode, Direction
    from app.models.call import Channel
    from app.models.number import NumberKind, NumberStatus, PhoneNumber
    from app.models.tenant import Tenant
    from app.repositories import agents as agent_repo
    from app.repositories import numbers as number_repo
    from app.repositories import tenants as tenant_repo
    from app.services import routing

    company = Tenant(phoneNumberId="co-1", name="Acme")
    await tenant_repo.save(company)
    support = await agent_repo.save_agent(
        Agent(tenantId="co-1", name="Support", mode=AgentMode.INBOUND)
    )
    line = await number_repo.save(PhoneNumber(
        tenantId="co-1", kind=NumberKind.WHATSAPP, phoneNumber="+92300",
        status=NumberStatus.VERIFIED, metaPhoneNumberId="meta-77", accessToken="tok",
        inboundAgentId=support.id,
    ))
    found = await number_repo.by_meta_phone_number_id("meta-77")
    assert found is not None and found.id == line.id
    overlay = number_repo.as_tenant(company, found)
    assert overlay.phone_number_id == "co-1" and overlay.graph_number_id == "meta-77"
    resolved = await routing.resolve(
        overlay, channel=Channel.WHATSAPP_CALL, direction=Direction.INBOUND
    )
    assert resolved.agent_id == support.id and resolved.resolved_by == "number"
    with pytest.raises(RuntimeError):
        await tenant_repo.save(overlay)


# ---------------------------------------------------------------------------
# Before the migration has been applied
# ---------------------------------------------------------------------------

@pytest.fixture
def numbers_table_missing(monkeypatch, fake_db):
    """`voice_numbers` unreadable, as on a deployment that skipped the migration."""
    from app.errors import UpstreamError
    from app.repositories import numbers as number_repo

    original = fake_db.select

    async def refuse(table, *, params=None):
        if table == number_repo.TABLE:
            raise UpstreamError(
                "The table 'voice_numbers' does not exist in this Supabase project."
            )
        return await original(table, params=params)

    monkeypatch.setattr(fake_db, "select", refuse)
    number_repo._by_meta_cache.clear()


def test_a_meta_webhook_still_answers_without_the_numbers_table(
    client, tenant_factory, numbers_table_missing,
):
    """Meta retries a non-2xx and eventually disables the subscription, so an
    unreadable numbers table must degrade rather than fail the webhook."""
    import hashlib
    import hmac
    import json

    tenant_factory("800", appSecret="webhook-secret")
    body = json.dumps({"entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "800"},
        "messages": [{"id": "wamid.1", "from": "923001112222", "type": "text",
                      "text": {"body": "are you open?"}, "timestamp": "1700000000"}],
    }}]}]})
    signature = hmac.new(b"webhook-secret", body.encode(), hashlib.sha256).hexdigest()

    resp = client.post("/api/webhooks/whatsapp", content=body, headers={
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "received"}
    # The message was still filed against the company.
    assert client.get("/api/messages", headers={
        "Authorization": f"Bearer {tenant_factory('801')[1]}",
    }).status_code == 200


def test_webhook_verification_survives_the_missing_table(client, numbers_table_missing, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "whatsapp_webhook_verify_token", "shared-token")
    resp = client.get("/api/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "shared-token", "hub.challenge": "42",
    })
    assert resp.status_code == 200 and resp.text == "42"


def test_routing_falls_back_when_the_numbers_table_is_unreadable(
    client, tenant_factory, numbers_table_missing,
):
    """A ringing call resolves to the company's own settings instead of raising."""
    import asyncio

    from app.models.agent import Direction
    from app.models.call import Channel
    from app.repositories import tenants as tenant_repo
    from app.services import routing

    tenant_factory("802", persona="You are the Acme assistant.")
    tenant = asyncio.run(tenant_repo.get("802"))
    overlay = tenant.model_copy(update={"line_id": "a-number-we-cannot-read"})
    resolved = asyncio.run(routing.resolve(
        overlay, channel=Channel.PHONE, direction=Direction.INBOUND,
    ))
    assert resolved.agent is None
    assert resolved.resolved_by == "company"


# ---------------------------------------------------------------------------
# What the provider is actually asked
# ---------------------------------------------------------------------------

def _infobip_tenant():
    from app.models.tenant import Tenant

    return Tenant(
        phoneNumberId="900", name="Acme", infobipApiKey="key-12345678",
        infobipBaseUrl="https://6zpl9e.api-pk2.infobip.com",
        infobipPhoneNumber="+923001112222",
    )


class _Reply:
    """The shape of the one httpx response `_verify_infobip` reads."""

    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def _fake_client(routes: dict[str, _Reply], seen: list[str] | None = None):
    import contextlib

    class Client:
        def __init__(self, **_kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_exc): return False
        async def get(self, url, headers=None, params=None):
            path = url.split(".com", 1)[-1]
            if seen is not None:
                seen.append(path)
            return routes.get(path, _Reply(404, {"requestError": {}}))

    return Client


def test_infobip_is_asked_for_calls_configurations_not_the_balance(monkeypatch):
    """`/account/1/balance` 404s on some regional hosts, so a good key looked
    refused. Voice scope is the permission a call needs, so that is the ask."""
    import asyncio

    from app.services import lines

    seen: list[str] = []
    monkeypatch.setattr(lines.httpx, "AsyncClient", _fake_client({
        "/calls/1/configurations": _Reply(200, {"results": [
            {"id": "cfg-1", "name": "Voice Agent"},
        ]}),
    }, seen))

    detail = asyncio.run(lines._verify_infobip(_infobip_tenant()))
    assert seen == ["/calls/1/configurations"], "the balance endpoint was asked again"
    assert "1 calls configuration" in detail


def test_a_configuration_id_that_does_not_exist_is_named(monkeypatch):
    import asyncio

    from app.services import lines

    tenant = _infobip_tenant()
    tenant.infobip_calls_configuration_id = "typo-id"
    monkeypatch.setattr(lines.httpx, "AsyncClient", _fake_client({
        "/calls/1/configurations": _Reply(200, {"results": [
            {"id": "cfg-1", "name": "Voice Agent"},
        ]}),
    }))

    with pytest.raises(lines._Refused) as refused:
        asyncio.run(lines._verify_infobip(tenant))
    assert "typo-id" in str(refused.value) and "cfg-1" in str(refused.value)


def test_a_chosen_configuration_is_confirmed_by_name(monkeypatch):
    import asyncio

    from app.services import lines

    tenant = _infobip_tenant()
    tenant.infobip_calls_configuration_id = "cfg-1"
    monkeypatch.setattr(lines.httpx, "AsyncClient", _fake_client({
        "/calls/1/configurations": _Reply(200, {"results": [
            {"id": "cfg-1", "name": "Voice Agent"},
        ]}),
    }))
    assert "Voice Agent" in asyncio.run(lines._verify_infobip(tenant))


def test_an_account_without_the_calls_api_says_so(monkeypatch):
    """A working key on an account with no Voice product is a different
    problem from a wrong key, and has a different fix."""
    import asyncio

    from app.services import lines

    monkeypatch.setattr(lines.httpx, "AsyncClient", _fake_client({
        "/calls/1/configurations": _Reply(404, {"requestError": {}}),
        "/account/1/balance": _Reply(200, {"balance": 10.0, "currency": "EUR"}),
    }))
    with pytest.raises(lines._Refused) as refused:
        asyncio.run(lines._verify_infobip(_infobip_tenant()))
    assert "no Voice/Calls API" in str(refused.value)


def test_a_rejected_key_is_reported_as_a_key_problem(monkeypatch):
    import asyncio

    from app.services import lines

    monkeypatch.setattr(lines.httpx, "AsyncClient", _fake_client({
        "/calls/1/configurations": _Reply(401, {}),
    }))
    with pytest.raises(lines._Refused) as refused:
        asyncio.run(lines._verify_infobip(_infobip_tenant()))
    assert "rejected the API key" in str(refused.value)
