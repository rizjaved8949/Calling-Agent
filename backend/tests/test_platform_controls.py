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


def test_an_engine_this_build_cannot_speak_is_refused(client, fake_db):
    """Storing it would mean every call failing with nothing to say why."""
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
