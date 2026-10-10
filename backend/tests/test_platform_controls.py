"""
What the platform operator controls: who can sign in, who is switched off, and
which engine carries the conversations.
"""
from __future__ import annotations

import pytest

ADMIN = {"X-Admin-Key": "test-admin-key"}


# ---------------------------------------------------------------------------
# Switching a company off
# ---------------------------------------------------------------------------

def test_a_suspended_company_cannot_reach_anything(client, tenant_factory, auth):
    """A pause, not a deletion: the key is still valid, so the refusal says
    what is wrong rather than sending them to sign in again forever."""
    _, key = tenant_factory("980", name="Acme")
    assert client.get("/api/calls", headers=auth(key)).status_code == 200

    off = client.post("/api/platform/companies/980/suspend",
                      json={"suspended": True, "reason": "Unpaid invoice."},
                      headers=ADMIN)
    assert off.status_code == 200 and off.json()["suspended"] is True

    blocked = client.get("/api/calls", headers=auth(key))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["message"] == "Unpaid invoice."


def test_switching_a_company_back_on_restores_everything(client, tenant_factory, auth):
    _, key = tenant_factory("981")
    client.post("/api/platform/companies/981/suspend", json={"suspended": True},
                headers=ADMIN)
    assert client.get("/api/calls", headers=auth(key)).status_code == 403

    back = client.post("/api/platform/companies/981/suspend", json={"suspended": False},
                       headers=ADMIN)
    assert back.json()["suspended"] is False
    assert back.json()["suspendedReason"] is None
    assert client.get("/api/calls", headers=auth(key)).status_code == 200


def test_suspending_keeps_the_company_and_its_calls(client, tenant_factory, auth):
    import asyncio

    from app.models.call import Call, Channel
    from app.repositories import calls as call_repo

    _, _key = tenant_factory("982")
    asyncio.run(call_repo.save_call(Call(id="c-1", tenantId="982", channel=Channel.PHONE,
                                         counterparty="+92300")))
    client.post("/api/platform/companies/982/suspend", json={"suspended": True},
                headers=ADMIN)

    detail = client.get("/api/platform/companies/982", headers=ADMIN).json()
    assert detail["company"]["suspended"] is True
    assert len(detail["recentCalls"]) == 1, "suspending removed their calls"


def test_only_the_operator_can_suspend(client, tenant_factory, auth):
    _, key = tenant_factory("983")
    refused = client.post("/api/platform/companies/983/suspend",
                          json={"suspended": True}, headers=auth(key))
    assert refused.status_code == 401


# ---------------------------------------------------------------------------
# Registering a company somebody can actually sign in to
# ---------------------------------------------------------------------------

def test_a_company_can_be_registered_with_an_owner(client, firebase, fake_db):
    created = client.post("/api/companies", json={
        "name": "Northwind", "phoneNumberId": "pending-northwind",
        "ownerEmail": "owner@northwind.test", "ownerPassword": "a-good-password",
        "ownerName": "Sara",
    }, headers=ADMIN)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["owner"] == {"email": "owner@northwind.test", "role": "owner"}
    assert body["apiKey"].startswith("ca_")


def test_registering_without_an_owner_still_works(client, fake_db):
    """Not everyone is set up by the operator; a company usually signs itself
    up and this path has to keep working."""
    created = client.post("/api/companies", json={
        "name": "Quiet Co", "phoneNumberId": "pending-quiet",
    }, headers=ADMIN)
    assert created.status_code == 201
    assert created.json()["owner"] is None


def test_a_weak_owner_password_is_refused_and_nothing_is_left_behind(client, firebase, fake_db):
    """A company nobody can sign in to is worse than no company: the operator
    would have to find and delete the row before trying again."""
    refused = client.post("/api/companies", json={
        "name": "Northwind", "phoneNumberId": "pending-weak",
        "ownerEmail": "owner@northwind.test", "ownerPassword": "short",
    }, headers=ADMIN)
    assert refused.status_code == 422

    listed = client.get("/api/companies", headers=ADMIN).json()["companies"]
    assert not any(c["phoneNumberId"] == "pending-weak" for c in listed)


# ---------------------------------------------------------------------------
# The speech engine
# ---------------------------------------------------------------------------

def test_the_engine_falls_back_to_the_environment(client, fake_db):
    body = client.get("/api/platform/engine", headers=ADMIN).json()
    assert body["engine"] == "gemini"
    assert body["source"] == "the server environment"
    assert any(e["id"] == "gemini" and e["supported"] for e in body["engines"])


def test_a_key_set_in_the_portal_takes_over(client, fake_db):
    saved = client.put("/api/platform/engine", json={
        "engine": "gemini", "model": "gemini-3.1-flash-live-preview",
        "apiKey": "AIza-platform-key-1234",
    }, headers=ADMIN).json()
    assert saved["source"] == "platform settings"
    assert saved["keyHint"] == "…1234"
    assert "AIza-platform-key-1234" not in str(saved), "the key was handed back"


def test_a_blank_key_is_not_read_as_clearing_it(client, fake_db):
    """The screen never shows a key back, so an untouched field must not wipe
    the stored one."""
    client.put("/api/platform/engine", json={
        "engine": "gemini", "model": "m", "apiKey": "AIza-first-key-9999",
    }, headers=ADMIN)
    after = client.put("/api/platform/engine", json={
        "engine": "gemini", "model": "m2",
    }, headers=ADMIN).json()
    assert after["keyHint"] == "…9999"
    assert after["model"] == "m2"


def test_openai_can_now_be_chosen(client, fake_db):
    """It was refused while there was no engine behind it. There is one now,
    so storing it means calls actually run on it."""
    saved = client.put("/api/platform/engine", json={
        "engine": "openai", "model": "gpt-realtime", "apiKey": "sk-test",
    }, headers=ADMIN)
    assert saved.status_code == 200, saved.text
    assert saved.json()["engine"] == "openai"
    assert saved.json()["supported"] is True


def test_openai_is_marked_as_untried_on_a_real_call(client, fake_db):
    """The code is here and the key test is real, but no call has been placed
    on it from this deployment. That difference belongs on the screen."""
    body = client.get("/api/platform/engine", headers=ADMIN).json()
    openai = next(e for e in body["engines"] if e["id"] == "openai")
    assert openai["supported"] is True
    assert openai.get("unproven") is True
    assert "no call has been placed" in openai["note"]


def test_an_engine_this_build_cannot_speak_is_refused(client, fake_db, monkeypatch):
    """Storing one would mean every call failing with nothing to say why.

    Both listed engines are implemented now, so the guard is exercised by
    pretending one is not — the branch still has to work for whichever engine
    is listed before it is finished next time.
    """
    from app.services import platform_settings

    monkeypatch.setattr(platform_settings, "SUPPORTED", {"gemini"})
    refused = client.put("/api/platform/engine", json={
        "engine": "openai", "model": "gpt-realtime", "apiKey": "sk-test",
    }, headers=ADMIN)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "engine_not_supported"


def test_an_unknown_engine_is_refused(client, fake_db):
    refused = client.put("/api/platform/engine", json={"engine": "telepathy"},
                         headers=ADMIN)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "unknown_engine"


def test_a_call_uses_the_operators_key(client, fake_db):
    import asyncio

    from app.services.agent import live

    client.put("/api/platform/engine", json={
        "engine": "gemini", "model": "chosen-model", "apiKey": "AIza-chosen-5555",
    }, headers=ADMIN)
    chosen = asyncio.run(live.live_settings_now())
    assert chosen.api_key == "AIza-chosen-5555"
    assert chosen.model == "chosen-model"


# ---------------------------------------------------------------------------
# A company with its own engine
# ---------------------------------------------------------------------------

def test_a_company_can_be_given_its_own_key(client, tenant_factory, fake_db):
    import asyncio

    from app.repositories import tenants as tenant_repo
    from app.services.agent import live

    tenant_factory("990")
    client.put("/api/platform/engine", json={
        "engine": "gemini", "model": "platform-model", "apiKey": "AIza-platform-0000",
    }, headers=ADMIN)

    # Until it is given one, it runs on the platform's.
    shared = asyncio.run(tenant_repo.get("990"))
    assert asyncio.run(live.live_settings_now(shared)).api_key == "AIza-platform-0000"

    client.patch("/api/companies/990", json={
        "engineModel": "their-model", "engineApiKey": "AIza-theirs-7777",
    }, headers=ADMIN)

    theirs = asyncio.run(tenant_repo.get("990"))
    chosen = asyncio.run(live.live_settings_now(theirs))
    assert chosen.api_key == "AIza-theirs-7777"
    assert chosen.model == "their-model"


def test_one_companys_key_does_not_leak_to_another(client, tenant_factory, fake_db):
    import asyncio

    from app.repositories import tenants as tenant_repo
    from app.services.agent import live

    tenant_factory("991")
    tenant_factory("992")
    client.patch("/api/companies/991", json={"engineApiKey": "AIza-only-991"},
                 headers=ADMIN)

    other = asyncio.run(tenant_repo.get("992"))
    assert asyncio.run(live.live_settings_now(other)).api_key != "AIza-only-991"


def test_a_companys_engine_key_is_never_returned(client, tenant_factory, auth, fake_db):
    _, key = tenant_factory("993")
    client.patch("/api/companies/993", json={"engineApiKey": "AIza-secret-4321"},
                 headers=ADMIN)

    mine = client.get("/api/companies/me", headers=auth(key))
    assert "AIza-secret-4321" not in mine.text
    assert mine.json()["engineKeySet"] is True


def test_a_companys_engine_key_is_sealed_in_the_row(client, tenant_factory, fake_db):
    client_tenant = tenant_factory("994")
    client.patch("/api/companies/994", json={"engineApiKey": "AIza-sealed-8888"},
                 headers=ADMIN)
    stored = fake_db._table("voice_tenants")["994"]["data"]
    assert stored["engineApiKey"] != "AIza-sealed-8888", "the key was stored in the clear"
