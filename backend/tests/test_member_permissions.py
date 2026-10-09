"""
What an employee may do, enforced on the server rather than drawn in the UI.

Every employee of a company holds the same company API key, so that key names
the company and never the person. Until the browser also sent its Firebase
token there was no way for the server to tell an owner from an employee, and
"staff cannot delete a recording" could only ever be a hidden button — which
is a suggestion, not a rule.

These pin the rule itself: who may delete, and whose calls an employee sees.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.call import Call, CallStatus, Channel, Direction, RecordingState
from app.repositories import calls as call_repo


def member(uid: str, email: str) -> dict:
    """The header a signed-in browser sends alongside the company key."""
    return {"X-User-Token": f"{uid}:{email}"}


@pytest.fixture
def company(client, tenant_factory, auth, firebase):
    """An owner and an employee of one company, both holding the same key."""
    _, key = tenant_factory("111", "Acme")
    firebase["owner-uid"] = {
        "uid": "owner-uid", "email": "owner@acme.test",
        "phoneNumberId": "111", "role": "owner",
    }
    firebase["staff-uid"] = {
        "uid": "staff-uid", "email": "staff@acme.test",
        "phoneNumberId": "111", "role": "staff",
    }
    return {
        "key": key,
        "auth": auth(key),
        "owner": {**auth(key), **member("owner-uid", "owner@acme.test")},
        "staff": {**auth(key), **member("staff-uid", "staff@acme.test")},
    }


def add_call(call_id: str, placed_by: str, *, tenant: str = "111") -> Call:
    call = Call(
        id=call_id, tenantId=tenant, channel=Channel.PHONE, direction=Direction.OUTBOUND,
        status=CallStatus.COMPLETED, counterparty="+923001112222",
        placedBy=placed_by, recordingState=RecordingState.READY,
        recordingPath="mem://x", recordingMime="audio/wav", recordingBytes=10,
    )
    asyncio.run(call_repo.save_call(call))
    return call


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


def test_an_employee_cannot_delete_a_recording(client, company, fake_db):
    add_call("c1", "staff@acme.test")
    refused = client.delete("/api/calls/c1/recording", headers=company["staff"])
    assert refused.status_code == 403
    assert "owner or admin" in refused.json()["error"]["message"]


def test_an_employee_cannot_delete_even_their_own_call(client, company, fake_db):
    """A recording is the company's record of what was said on its behalf.
    The person who said it is the last one who should be able to remove it."""
    add_call("c1", "staff@acme.test")
    refused = client.delete("/api/calls/c1", headers=company["staff"])
    assert refused.status_code == 403


def test_an_employee_cannot_bulk_delete(client, company, fake_db):
    add_call("c1", "staff@acme.test")
    refused = client.post("/api/recordings/delete", headers=company["staff"],
                          json={"callIds": ["c1"]})
    assert refused.status_code == 403


def test_the_owner_can_delete_a_recording(client, company, fake_db):
    add_call("c1", "staff@acme.test")
    gone = client.delete("/api/calls/c1/recording", headers=company["owner"])
    assert gone.status_code in (200, 204), gone.text


def test_the_owner_can_delete_the_employees_call(client, company, fake_db):
    add_call("c1", "staff@acme.test")
    gone = client.delete("/api/calls/c1", headers=company["owner"])
    assert gone.status_code in (200, 204), gone.text
    assert client.get("/api/calls/c1", headers=company["owner"]).status_code == 404


def test_a_bare_api_key_still_has_full_access(client, company, fake_db):
    """A script holding the company key has no Firebase session. Refusing it
    would break every integration, and the key already grants this anyway."""
    add_call("c1", "someone@acme.test")
    gone = client.delete("/api/calls/c1", headers=company["auth"])
    assert gone.status_code in (200, 204), gone.text


# ---------------------------------------------------------------------------
# Whose calls you see
# ---------------------------------------------------------------------------


def test_an_employee_sees_only_the_calls_they_placed(client, company, fake_db):
    add_call("mine", "staff@acme.test")
    add_call("theirs", "colleague@acme.test")
    add_call("agents", "")

    listed = client.get("/api/calls", headers=company["staff"]).json()["calls"]
    assert [c["id"] for c in listed] == ["mine"]


def test_the_owner_sees_everything(client, company, fake_db):
    add_call("mine", "staff@acme.test")
    add_call("theirs", "colleague@acme.test")
    listed = client.get("/api/calls", headers=company["owner"]).json()["calls"]
    assert {c["id"] for c in listed} == {"mine", "theirs"}


def test_the_match_ignores_case(client, company, fake_db):
    """Email case is not meaningful, and a mismatch here would hide an
    employee's own calls from them with no explanation."""
    add_call("mine", "Staff@Acme.test")
    listed = client.get("/api/calls", headers=company["staff"]).json()["calls"]
    assert [c["id"] for c in listed] == ["mine"]


# ---------------------------------------------------------------------------
# Who the server thinks placed a call
# ---------------------------------------------------------------------------


def test_who_placed_a_call_comes_from_the_session_not_the_body(
    client, company, fake_db, monkeypatch,
):
    """An employee only sees the calls they placed, so a name taken from the
    request body would be the thing deciding what that employee can see."""
    from app.services import lines, telephony

    async def verified(_tenant):
        return "ok"

    async def fake_place(self, to, *, from_number=""):
        return {"id": "provider-1"}

    monkeypatch.setattr(lines, "_verify_infobip", verified)
    monkeypatch.setattr(telephony.Infobip, "place_call", fake_place)

    number = client.post("/api/numbers", headers=company["owner"], json={
        "kind": "sim", "phoneNumber": "+923001112222", "mode": "outbound",
        "infobipApiKey": "k-12345678", "infobipBaseUrl": "x.api.infobip.com",
    }).json()

    made = client.post("/api/calls", headers=company["staff"], json={
        "to": "+923334445555", "channel": "PHONE", "lineId": number["id"],
        "human": True, "metadata": {"placedBy": "owner@acme.test"},
    })
    assert made.status_code in (200, 201), made.text
    # Their own address, not the one they claimed in the body.
    assert made.json()["placedBy"] == "staff@acme.test"

    # And it is their own call, so they can see it.
    listed = client.get("/api/calls", headers=company["staff"]).json()["calls"]
    assert [c["id"] for c in listed] == [made.json()["id"]]


# ---------------------------------------------------------------------------
# A token that proves nothing about this company
# ---------------------------------------------------------------------------


def test_a_token_for_another_company_is_ignored(client, company, firebase, fake_db):
    """Signed in, but not as a member here. Their Firebase identity says
    nothing about what they may do in this company, so the request falls back
    to what the company key alone allows."""
    firebase["outsider-uid"] = {
        "uid": "outsider-uid", "email": "nosy@other.test",
        "phoneNumberId": "999", "role": "staff",
    }
    add_call("c1", "someone@acme.test")
    headers = {**company["auth"], **member("outsider-uid", "nosy@other.test")}
    # Treated as no token at all: the company key's own full access.
    assert client.delete("/api/calls/c1", headers=headers).status_code in (200, 204)


def test_an_unreadable_token_is_treated_as_no_token(client, company, fake_db):
    add_call("c1", "someone@acme.test")
    headers = {**company["auth"], "X-User-Token": "not-a-token"}
    assert client.delete("/api/calls/c1", headers=headers).status_code in (200, 204)
