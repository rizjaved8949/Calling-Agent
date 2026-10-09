"""
Where a signed-in browser becomes a company's API key.

Firebase itself (token verification, Firestore) is not exercised here — that
would mean a real Google project in CI. Instead, `verify_id_token` and the
Firestore-backed `users` repository are faked, the same way `conftest.py`
fakes Supabase: the fakes are simple enough that the tenant-isolation and
idempotency logic in `routes/auth.py` is still really being tested, just
against a stand-in for the one external call.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def firebase(monkeypatch):
    """A fake Firebase: `Authorization: Bearer <uid>:<email>` decodes as that
    person, and a users collection lives in a plain dict instead of Firestore.
    """
    from app.api import deps
    from app.repositories import users as user_repo

    store: dict[str, dict] = {}

    def fake_verify(token: str) -> dict:
        from app.errors import Unauthorized

        if ":" not in token:
            raise Unauthorized("bad test token")
        uid, email = token.split(":", 1)
        return {"uid": uid, "email": email, "name": email.split("@")[0]}

    async def fake_get(uid: str):
        return store.get(uid)

    async def fake_create(uid: str, *, email: str, display_name: str, phone_number_id: str):
        store[uid] = {
            "email": email, "displayName": display_name, "phoneNumberId": phone_number_id,
        }

    monkeypatch.setattr("app.services.firebase.verify_id_token", fake_verify)
    monkeypatch.setattr(deps.firebase, "verify_id_token", fake_verify)
    monkeypatch.setattr(user_repo, "get", fake_get)
    monkeypatch.setattr(user_repo, "create", fake_create)
    return store


def _auth(uid: str, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {uid}:{email}"}


def test_signup_creates_a_company_and_links_the_user(client, fake_db, firebase):
    resp = client.post(
        "/api/auth/signup", json={"companyName": "Acme Dental"},
        headers=_auth("uid-1", "owner@acme.test"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["companyName"] == "Acme Dental"
    assert body["apiKey"], "no API key was issued"
    assert body["phoneNumberId"].startswith("pending-")

    # The company really exists and really holds that key.
    from app.repositories import tenants as tenant_repo
    import asyncio
    tenant = asyncio.run(tenant_repo.get(body["phoneNumberId"]))
    assert tenant is not None
    assert tenant.api_key == body["apiKey"]
    assert tenant.name == "Acme Dental"


def test_signing_up_twice_is_idempotent(client, fake_db, firebase):
    first = client.post(
        "/api/auth/signup", json={"companyName": "Acme Dental"},
        headers=_auth("uid-1", "owner@acme.test"),
    ).json()
    second = client.post(
        "/api/auth/signup", json={"companyName": "A different name this time"},
        headers=_auth("uid-1", "owner@acme.test"),
    ).json()

    assert second["phoneNumberId"] == first["phoneNumberId"], "a second company was created"
    assert second["companyName"] == "Acme Dental", "the original company was renamed"


def test_logging_in_without_signing_up_asks_for_signup(client, fake_db, firebase):
    resp = client.post("/api/auth/login", headers=_auth("uid-new", "nobody@acme.test"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "needs_signup"


def test_login_returns_the_same_key_signup_issued(client, fake_db, firebase):
    signed_up = client.post(
        "/api/auth/signup", json={"companyName": "Acme Dental"},
        headers=_auth("uid-1", "owner@acme.test"),
    ).json()
    logged_in = client.post("/api/auth/login", headers=_auth("uid-1", "owner@acme.test")).json()

    assert logged_in["apiKey"] == signed_up["apiKey"]
    assert logged_in["phoneNumberId"] == signed_up["phoneNumberId"]


def test_a_malformed_token_is_rejected(client, fake_db, firebase):
    resp = client.post(
        "/api/auth/login", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_no_token_at_all_is_rejected(client, fake_db, firebase):
    resp = client.post("/api/auth/login")
    assert resp.status_code == 401


def test_one_person_cannot_log_into_another_persons_company(client, fake_db, firebase):
    """Two different `uid`s never resolve to the same company by accident."""
    first = client.post(
        "/api/auth/signup", json={"companyName": "Acme Dental"},
        headers=_auth("uid-1", "owner@acme.test"),
    ).json()
    second = client.post(
        "/api/auth/signup", json={"companyName": "Northstar Clinic"},
        headers=_auth("uid-2", "owner@northstar.test"),
    ).json()

    assert first["phoneNumberId"] != second["phoneNumberId"]
    assert first["apiKey"] != second["apiKey"]
