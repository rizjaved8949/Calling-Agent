"""The super admin: created once, signs in, recovers a forgotten password."""
from __future__ import annotations


def _setup(client, password="correct horse battery"):
    return client.post("/api/platform/setup", json={
        "email": "Owner@Example.com", "password": password, "name": "Owner",
    })


def test_setup_happens_once_and_signs_in(client):
    assert client.get("/api/platform/status").json() == {"superAdminExists": False}
    first = _setup(client)
    assert first.status_code == 201
    body = first.json()
    assert body["recoveryCode"] and body["token"].startswith("sa1.")
    assert _setup(client, "another password!").status_code == 409
    assert client.get("/api/platform/status").json() == {"superAdminExists": True}
    # The session works wherever the admin key does.
    resp = client.get("/api/companies", headers={"X-Admin-Key": body["token"]})
    assert resp.status_code == 200


def test_login_checks_the_password(client):
    _setup(client)
    bad = client.post("/api/platform/login", json={"email": "owner@example.com", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/api/platform/login",
                     json={"email": "owner@example.com", "password": "correct horse battery"})
    assert ok.status_code == 200 and ok.json()["token"].startswith("sa1.")


def test_short_passwords_are_refused(client):
    assert _setup(client, "short").status_code == 422


def test_forgot_password_with_the_recovery_code(client):
    created = _setup(client).json()
    old_token = created["token"]
    wrong = client.post("/api/platform/forgot-password", json={
        "email": "owner@example.com", "recoveryCode": "AAAAA-BBBBB", "newPassword": "a brand new password",
    })
    assert wrong.status_code == 401
    reset = client.post("/api/platform/forgot-password", json={
        "email": "owner@example.com", "recoveryCode": created["recoveryCode"].lower(),
        "newPassword": "a brand new password",
    })
    assert reset.status_code == 200
    assert reset.json()["recoveryCode"] != created["recoveryCode"]
    # Resetting signs out every earlier session, and spends the old code.
    assert client.get("/api/companies", headers={"X-Admin-Key": old_token}).status_code == 401
    again = client.post("/api/platform/forgot-password", json={
        "email": "owner@example.com", "recoveryCode": created["recoveryCode"],
        "newPassword": "yet another password",
    })
    assert again.status_code == 401
    login = client.post("/api/platform/login",
                        json={"email": "owner@example.com", "password": "a brand new password"})
    assert login.status_code == 200


def test_a_forged_token_is_refused(client):
    _setup(client)
    resp = client.get("/api/companies", headers={"X-Admin-Key": "sa1.9999999999.1.deadbeef"})
    assert resp.status_code == 401
