"""
Test fixtures.

The Supabase layer is replaced with an in-memory stand-in rather than mocked
per test. Every repository goes through `SupabaseClient`, so faking that one
seam gives the routes a real database to talk to — including the `eq.` filter
syntax, which is where a tenant-isolation bug would actually show up.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

# Set before any app module is imported: Settings reads the environment once.
# Pointed at a file that does not exist, so the suite never picks up the
# developer's own .env and start passing or failing on local configuration.
os.environ["APP_ENV_FILE"] = "tests/.env.absent"
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("CREDENTIALS_SECRET", "test-credentials-secret")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("APP_ENV", "test")


class FakeSupabase:
    """An in-memory PostgREST, good enough for the filters this API uses."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def configured(self) -> bool:
        return True

    def _table(self, name: str) -> dict[str, dict[str, Any]]:
        return self.tables.setdefault(name, {})

    @staticmethod
    def _value(row: dict[str, Any], field: str) -> Any:
        if field.startswith("data->>"):
            return (row.get("data") or {}).get(field[len("data->>"):])
        return row.get(field)

    def _matches(self, row: dict[str, Any], params: dict[str, Any]) -> bool:
        for field, condition in params.items():
            if field in {"order", "limit", "offset", "select"}:
                continue
            condition = str(condition)
            if condition.startswith("eq."):
                if str(self._value(row, field)) != condition[3:]:
                    return False
            elif condition.startswith("gte."):
                if str(self._value(row, field) or "") < condition[4:]:
                    return False
            elif condition.startswith("ilike."):
                needle = condition[6:].strip("*").lower()
                if needle not in str(self._value(row, field) or "").lower():
                    return False
        return True

    async def select(self, table: str, *, params: dict[str, Any] | None = None) -> list[dict]:
        params = params or {}
        rows = [r for r in self._table(table).values() if self._matches(r, params)]
        rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", len(rows)) or len(rows))
        return [dict(r) for r in rows[offset : offset + limit]]

    async def select_one(self, table: str, *, params: dict[str, Any] | None = None):
        rows = await self.select(table, params=params)
        return rows[0] if rows else None

    async def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        self._table(table)[str(row["id"])] = dict(row)
        return dict(row)

    async def delete(self, table: str, *, params: dict[str, Any]) -> None:
        doomed = [
            key for key, row in self._table(table).items() if self._matches(row, params)
        ]
        for key in doomed:
            self._table(table).pop(key, None)

    async def count(self, table: str, *, params: dict[str, Any] | None = None) -> int:
        return len(await self.select(table, params=params))

    def storage(self):  # pragma: no cover - storage is faked per test instead
        raise AssertionError("storage() should not be reached in these tests")

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_prompt_cache():
    """Prompt text is cached for two minutes in production. Inside one test
    run that means the previous test's documents, so every test starts cold."""
    from app.repositories import knowledge as knowledge_repo

    knowledge_repo.forget_context()
    yield
    knowledge_repo.forget_context()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """A fresh rate-limit window per test.

    The limiter is in-memory and keyed by API key (see
    `app/security/rate_limit.py`), so without this a test that happens to run
    late in a fast suite could inherit hits from a different test's tenant —
    harmless in practice since keys are per-tenant, but the one shared key is
    `ADMIN_API_KEY`, which every test shares. Reset keeps the suite's result
    independent of run order rather than merely usually independent of it.
    """
    from app.security import rate_limit

    rate_limit._blanket._hits.clear()
    rate_limit._expensive._hits.clear()
    yield
    rate_limit._blanket._hits.clear()
    rate_limit._expensive._hits.clear()


@pytest.fixture
def fake_db(monkeypatch) -> FakeSupabase:
    import importlib

    from app.db import supabase as supabase_module
    from app.repositories import tenants as tenant_repo

    fake = FakeSupabase()
    monkeypatch.setattr(supabase_module, "supabase", fake)

    # Every module that did `from ..db.supabase import supabase` holds its own
    # reference, bound at import. Patching the source module does not reach
    # them, so each one is patched by name — and the list is derived rather
    # than written down, because a repository added later would otherwise talk
    # to the real database from inside the test suite without anyone noticing.
    for name in (
        "app.repositories.calls",
        "app.repositories.tenants",
        "app.repositories.campaigns",
        "app.repositories.knowledge",
        "app.repositories.agents",
        "app.repositories.numbers",
        "app.repositories.support",
        "app.security.superadmin",
        "app.services.storage",
    ):
        module = importlib.import_module(name)
        if hasattr(module, "supabase"):
            monkeypatch.setattr(module, "supabase", fake)

    tenant_repo.invalidate()
    from app.repositories import knowledge as knowledge_repo
    from app.security import superadmin

    superadmin._cache = None
    # Prompt text is cached for two minutes in production, which inside one
    # test run means the previous test's documents.
    knowledge_repo.forget_context()
    yield fake
    knowledge_repo.forget_context()
    tenant_repo.invalidate()


@pytest.fixture
def client(fake_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tenant_factory(fake_db):
    """Create a company directly, returning (tenant, api_key)."""
    import asyncio

    from app.models.tenant import Tenant
    from app.repositories import tenants as tenant_repo

    def make(phone_number_id: str = "111", name: str = "Acme", **extra) -> tuple[Any, str]:
        api_key = tenant_repo.new_api_key()
        tenant = Tenant(
            phoneNumberId=phone_number_id,
            wabaId=extra.pop("wabaId", "waba-1"),
            name=name,
            accessToken=extra.pop("accessToken", "graph-token"),
            appSecret=extra.pop("appSecret", "app-secret"),
            apiKey=api_key,
            defaultCountryCode=extra.pop("defaultCountryCode", "92"),
            **extra,
        )
        asyncio.run(tenant_repo.save(tenant))
        tenant_repo.invalidate()
        return tenant, api_key

    return make


@pytest.fixture
def auth():
    def headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    return headers


@pytest.fixture
def firebase(monkeypatch):
    """A fake Firebase and Firestore for the sign-in and team routes.

    `Authorization: Bearer <uid>:<email>` decodes as that person — good
    enough to exercise the real routing and isolation logic in
    `routes/auth.py` and `routes/team.py` without a real Google project in
    the loop. `users` and `invites` live in plain dicts rather than
    Firestore collections.
    """
    from app.api import deps
    from app.repositories import users as user_repo

    users: dict[str, dict] = {}
    invites: dict[str, dict] = {}

    def fake_verify(token: str) -> dict:
        from app.errors import Unauthorized

        if ":" not in token:
            raise Unauthorized("bad test token")
        uid, email = token.split(":", 1)
        return {"uid": uid, "email": email, "name": email.split("@")[0]}

    async def fake_get(uid: str):
        return users.get(uid)

    async def fake_create(
        uid: str, *, email: str, display_name: str, phone_number_id: str, role: str = "owner"
    ):
        users[uid] = {
            "email": email, "displayName": display_name, "phoneNumberId": phone_number_id,
            "role": role, "createdAt": len(users),
        }

    async def fake_list_for_company(phone_number_id: str):
        people = [
            {"uid": uid, **data} for uid, data in users.items()
            if data.get("phoneNumberId") == phone_number_id
        ]
        people.sort(key=lambda p: p.get("createdAt", 0))
        return people

    async def fake_remove(uid: str):
        users.pop(uid, None)

    async def fake_create_invite(*, email, phone_number_id, role, invited_by):
        token = f"invite-{len(invites)}"
        invites[token] = {
            "email": email.strip().lower(), "phoneNumberId": phone_number_id,
            "role": role, "invitedBy": invited_by, "status": "pending",
        }
        return token

    async def fake_get_invite(token: str):
        return invites.get(token)

    async def fake_consume_invite(token: str):
        if token in invites:
            invites[token]["status"] = "accepted"

    async def fake_list_invites(phone_number_id: str):
        return [
            {"token": t, **i} for t, i in invites.items()
            if i.get("phoneNumberId") == phone_number_id and i.get("status") == "pending"
        ]

    async def fake_revoke_invite(token: str):
        invites.pop(token, None)

    monkeypatch.setattr("app.services.firebase.verify_id_token", fake_verify)
    monkeypatch.setattr(deps.firebase, "verify_id_token", fake_verify)
    monkeypatch.setattr(user_repo, "get", fake_get)
    monkeypatch.setattr(user_repo, "create", fake_create)
    monkeypatch.setattr(user_repo, "list_for_company", fake_list_for_company)
    monkeypatch.setattr(user_repo, "remove", fake_remove)
    monkeypatch.setattr(user_repo, "create_invite", fake_create_invite)
    monkeypatch.setattr(user_repo, "get_invite", fake_get_invite)
    monkeypatch.setattr(user_repo, "consume_invite", fake_consume_invite)
    monkeypatch.setattr(user_repo, "list_invites", fake_list_invites)
    monkeypatch.setattr(user_repo, "revoke_invite", fake_revoke_invite)
    return users
