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
        "app.services.storage",
    ):
        module = importlib.import_module(name)
        if hasattr(module, "supabase"):
            monkeypatch.setattr(module, "supabase", fake)

    tenant_repo.invalidate()
    yield fake
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
