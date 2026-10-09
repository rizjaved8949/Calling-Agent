"""
Deleting things, and what the operator's portal reads.

The deletes are the ones a confirmation dialog sits in front of in the UI, so
each test pins down the part the words promise: a recording deleted without
taking its call with it, and a message removed from our own log only.
"""
from __future__ import annotations

import asyncio

import pytest


def _call(tenant_id: str, call_id: str, **extra):
    from app.models.call import Call, Channel
    from app.repositories import calls as call_repo

    asyncio.run(call_repo.save_call(Call(
        id=call_id, tenantId=tenant_id, channel=Channel.PHONE,
        counterparty="+923001112222", **extra,
    )))


def _message(tenant_id: str, message_id: str, body: str = "hello"):
    from app.models.call import Message, MessageDirection
    from app.repositories import calls as call_repo

    asyncio.run(call_repo.save_message(Message(
        id=message_id, tenantId=tenant_id, direction=MessageDirection.INBOUND,
        counterparty="+923001112222", body=body,
    )))


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------

def test_deleting_a_recording_keeps_the_call(client, tenant_factory, auth, monkeypatch):
    from app.models.call import RecordingState
    from app.services import storage

    _, key = tenant_factory("700")
    _call("700", "rec-1", recordingPath="sb://calls/rec-1.mp3", recordingMime="audio/mpeg",
          recordingState=RecordingState.READY, recordingBytes=12, transcript="caller: hello")

    erased: list[str] = []

    async def fake_delete(_tenant, reference):
        erased.append(reference)

    monkeypatch.setattr(storage, "delete", fake_delete)

    resp = client.delete("/api/calls/rec-1/recording", headers=auth(key))
    assert resp.status_code == 204, resp.text
    assert erased == ["sb://calls/rec-1.mp3"], "the stored audio was not erased"

    call = client.get("/api/calls/rec-1", headers=auth(key)).json()
    assert call["recording"]["available"] is False
    assert call["transcript"] == "caller: hello", "the transcript went with the audio"


def test_deleting_a_recording_that_is_not_there(client, tenant_factory, auth):
    _, key = tenant_factory("701")
    _call("701", "rec-2")
    assert client.delete("/api/calls/rec-2/recording", headers=auth(key)).status_code == 404


def test_one_company_cannot_delete_anothers_recording(client, tenant_factory, auth):
    from app.models.call import RecordingState

    _, key_a = tenant_factory("702")
    _, key_b = tenant_factory("703")
    _call("702", "rec-3", recordingPath="sb://calls/rec-3.mp3",
          recordingState=RecordingState.READY)
    assert client.delete("/api/calls/rec-3/recording", headers=auth(key_b)).status_code == 404


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def test_a_message_can_be_removed_from_the_log(client, tenant_factory, auth):
    _, key = tenant_factory("710")
    _message("710", "m-1", "are you open today?")
    assert len(client.get("/api/messages", headers=auth(key)).json()["messages"]) == 1

    resp = client.delete("/api/messages/m-1", headers=auth(key))
    assert resp.status_code == 204, resp.text
    assert client.get("/api/messages", headers=auth(key)).json()["messages"] == []


def test_one_company_cannot_delete_anothers_message(client, tenant_factory, auth):
    _, _key_a = tenant_factory("711")
    _, key_b = tenant_factory("712")
    _message("711", "m-2")
    assert client.delete("/api/messages/m-2", headers=auth(key_b)).status_code == 404
    assert client.delete("/api/messages/nothing", headers=auth(key_b)).status_code == 404


# ---------------------------------------------------------------------------
# The operator's portal
# ---------------------------------------------------------------------------

ADMIN = {"X-Admin-Key": "test-admin-key"}


def test_the_portal_overview_names_what_is_blocking_each_company(client, tenant_factory):
    tenant_factory("720", name="Nothing Yet")
    body = client.get("/api/platform/overview", headers=ADMIN).json()
    row = next(c for c in body["companies"] if c["id"] == "720")
    assert row["stage"] == "no numbers"
    assert "not connected a number" in row["blocker"]
    assert body["totals"]["companies"] >= 1


def test_the_overview_walks_a_company_to_live(client, tenant_factory, auth, monkeypatch):
    from app.services import lines

    async def accepted(_tenant):
        return "confirmed"

    monkeypatch.setattr(lines, "_verify_infobip", accepted)
    _, key = tenant_factory("721", name="Acme")

    def stage() -> str:
        body = client.get("/api/platform/overview", headers=ADMIN).json()
        return next(c for c in body["companies"] if c["id"] == "721")["stage"]

    number = client.post("/api/numbers", json={
        "kind": "sim", "phoneNumber": "+923001112222", "mode": "both",
        "infobipApiKey": "key-12345678", "infobipBaseUrl": "x.api.infobip.com",
    }, headers=auth(key)).json()
    assert stage() == "no agent", "a verified number with no agent should say so"

    agent = client.post("/api/agents", json={"name": "Support"}, headers=auth(key)).json()
    assert stage() == "not assigned"

    client.patch(f"/api/numbers/{number['id']}", json={"inboundAgentId": agent["id"]},
                 headers=auth(key))
    assert stage() == "live"


def test_the_portal_reads_one_company_in_full(client, tenant_factory, auth):
    _, key = tenant_factory("722", name="Acme")
    client.post("/api/agents", json={"name": "Support"}, headers=auth(key))
    body = client.get("/api/platform/companies/722", headers=ADMIN).json()
    assert body["company"]["name"] == "Acme"
    assert [a["name"] for a in body["agents"]] == ["Support"]
    assert "credentials" in body["company"] and "channels" in body["company"]
    # Never a secret, even for the operator.
    assert "accessToken" not in str(body["company"]["credentials"]).replace("'accessToken'", "")


def test_the_portal_needs_the_admin_key(client, tenant_factory, auth):
    _, key = tenant_factory("723")
    assert client.get("/api/platform/overview").status_code == 401
    assert client.get("/api/platform/overview", headers=auth(key)).status_code == 401
    assert client.get("/api/platform/companies/723", headers=auth(key)).status_code == 401
