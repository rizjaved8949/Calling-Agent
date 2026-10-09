"""
Who else at a company can sign in, and what the role they're given does.

These routes are scoped by the signed-in person (CurrentPerson), not the
shared company key — so the thing most worth testing is that only the owner
can invite or remove, and that an invite can only be accepted by the email
it was sent to.
"""
from __future__ import annotations


def _auth(uid: str, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}:{email}"}


def _signup(client, name="Acme", uid="owner-1", email="owner@acme.test") -> dict:
    return client.post(
        "/api/auth/signup", json={"companyName": name}, headers=_auth(uid, email),
    ).json()


def test_the_owner_can_invite_someone(client, fake_db, firebase):
    _signup(client)
    resp = client.post(
        "/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
        headers=_auth("owner-1", "owner@acme.test"),
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "staff"


def test_a_staff_member_cannot_invite_anyone(client, fake_db, firebase):
    _signup(client)
    client.post("/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
                headers=_auth("owner-1", "owner@acme.test"))
    invite = client.post("/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
                         headers=_auth("owner-1", "owner@acme.test")).json()
    client.post("/api/auth/accept-invite", json={"token": invite["token"]},
               headers=_auth("staff-1", "staff@acme.test"))

    resp = client.post(
        "/api/team/invite", json={"email": "someone-else@acme.test", "role": "staff"},
        headers=_auth("staff-1", "staff@acme.test"),
    )
    assert resp.status_code == 403


def test_accepting_an_invite_joins_the_existing_company_not_a_new_one(client, fake_db, firebase):
    owner = _signup(client)
    invite = client.post(
        "/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
        headers=_auth("owner-1", "owner@acme.test"),
    ).json()

    accepted = client.post(
        "/api/auth/accept-invite", json={"token": invite["token"]},
        headers=_auth("staff-1", "staff@acme.test"),
    )
    assert accepted.status_code == 201
    body = accepted.json()
    assert body["phoneNumberId"] == owner["phoneNumberId"], "a second company was created"
    assert body["apiKey"] == owner["apiKey"]
    assert body["role"] == "staff"


def test_an_invite_can_only_be_accepted_by_the_email_it_names(client, fake_db, firebase):
    _signup(client)
    invite = client.post(
        "/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
        headers=_auth("owner-1", "owner@acme.test"),
    ).json()

    resp = client.post(
        "/api/auth/accept-invite", json={"token": invite["token"]},
        headers=_auth("imposter-1", "someone.else@acme.test"),
    )
    assert resp.status_code == 403


def test_an_invite_cannot_be_used_twice(client, fake_db, firebase):
    _signup(client)
    invite = client.post(
        "/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
        headers=_auth("owner-1", "owner@acme.test"),
    ).json()
    client.post("/api/auth/accept-invite", json={"token": invite["token"]},
               headers=_auth("staff-1", "staff@acme.test"))

    second = client.post(
        "/api/auth/accept-invite", json={"token": invite["token"]},
        headers=_auth("staff-2", "someone.else@acme.test"),
    )
    assert second.status_code == 404


def test_inviting_as_owner_is_refused(client, fake_db, firebase):
    """Ownership is transferred deliberately, not handed out by invitation."""
    _signup(client)
    resp = client.post(
        "/api/team/invite", json={"email": "co-owner@acme.test", "role": "owner"},
        headers=_auth("owner-1", "owner@acme.test"),
    )
    assert resp.status_code == 422


def test_the_team_list_shows_members_and_pending_invites(client, fake_db, firebase):
    _signup(client)
    client.post("/api/team/invite", json={"email": "pending@acme.test", "role": "staff"},
               headers=_auth("owner-1", "owner@acme.test"))

    body = client.get("/api/team", headers=_auth("owner-1", "owner@acme.test")).json()
    assert len(body["members"]) == 1
    assert body["members"][0]["role"] == "owner"
    assert body["members"][0]["isYou"] is True
    assert len(body["invites"]) == 1
    assert body["invites"][0]["email"] == "pending@acme.test"


def test_the_owner_can_remove_a_staff_member(client, fake_db, firebase):
    _signup(client)
    invite = client.post("/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
                         headers=_auth("owner-1", "owner@acme.test")).json()
    client.post("/api/auth/accept-invite", json={"token": invite["token"]},
               headers=_auth("staff-1", "staff@acme.test"))

    resp = client.delete("/api/team/staff-1", headers=_auth("owner-1", "owner@acme.test"))
    assert resp.status_code == 204

    login_again = client.post("/api/auth/login", headers=_auth("staff-1", "staff@acme.test"))
    assert login_again.status_code == 404, "a removed member can still sign in"


def test_the_owner_cannot_remove_themselves(client, fake_db, firebase):
    _signup(client)
    resp = client.delete("/api/team/owner-1", headers=_auth("owner-1", "owner@acme.test"))
    assert resp.status_code == 409


def test_a_staff_member_cannot_remove_anyone(client, fake_db, firebase):
    _signup(client)
    invite = client.post("/api/team/invite", json={"email": "staff@acme.test", "role": "staff"},
                         headers=_auth("owner-1", "owner@acme.test")).json()
    client.post("/api/auth/accept-invite", json={"token": invite["token"]},
               headers=_auth("staff-1", "staff@acme.test"))

    resp = client.delete("/api/team/owner-1", headers=_auth("staff-1", "staff@acme.test"))
    assert resp.status_code == 403


def test_one_companys_team_is_invisible_to_another(client, fake_db, firebase):
    _signup(client, "Acme", "owner-1", "owner@acme.test")
    _signup(client, "Northstar", "owner-2", "owner@northstar.test")

    body = client.get("/api/team", headers=_auth("owner-2", "owner@northstar.test")).json()
    emails = [m["email"] for m in body["members"]]
    assert "owner@acme.test" not in emails
