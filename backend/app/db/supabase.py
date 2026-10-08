"""
The Supabase client: PostgREST for rows, Storage for objects.

Deliberately HTTP rather than a Postgres driver. The existing tables
(`voice_tenants`, `voice_calls`) are reached by the TypeScript service through
PostgREST with the service key, and going around it with psycopg would mean two
different notions of what a row looks like and two sets of credentials to keep.
It also keeps the dependency list to httpx.

Everything here is keyed on the `(id, tenant_id, data jsonb)` shape the
migrations define: the typed columns are for indexing and isolation, and the
record itself lives in `data`. That is what lets this service and the TS one
add fields without a migration each time.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import settings
from ..errors import UpstreamError

log = logging.getLogger(__name__)


class SupabaseNotConfigured(RuntimeError):
    """Raised when a repository is used before SUPABASE_URL is set."""


class SupabaseClient:
    """One shared client. PostgREST and Storage sit on the same host."""

    def __init__(self) -> None:
        self._rest: httpx.AsyncClient | None = None
        self._storage: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return settings.supabase_configured

    def _require(self) -> None:
        if not self.configured:
            raise SupabaseNotConfigured(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for persistence."
            )

    @property
    def _headers(self) -> dict[str, str]:
        key = settings.supabase_service_key.strip()
        # The newer `sb_secret_…` keys are not JWTs and Storage rejects them in
        # an Authorization header ("Invalid Compact JWS"); `apikey` is the one
        # that authenticates them. PostgREST wants both, so both are sent.
        return {"apikey": key, "Authorization": f"Bearer {key}"}

    def rest(self) -> httpx.AsyncClient:
        self._require()
        if self._rest is None:
            self._rest = httpx.AsyncClient(
                base_url=f"{settings.supabase_url.rstrip('/')}/rest/v1",
                headers={**self._headers, "Content-Type": "application/json"},
                timeout=settings.request_timeout_seconds,
            )
        return self._rest

    def storage(self) -> httpx.AsyncClient:
        self._require()
        if self._storage is None:
            self._storage = httpx.AsyncClient(
                base_url=f"{settings.supabase_url.rstrip('/')}/storage/v1",
                headers={"apikey": settings.supabase_service_key.strip()},
                timeout=60.0,
            )
        return self._storage

    async def close(self) -> None:
        for client in (self._rest, self._storage):
            if client is not None:
                await client.aclose()
        self._rest = self._storage = None

    # ---- Row operations ---------------------------------------------------

    async def select(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        response = await self._send("GET", f"/{table}", params=params or {})
        body = response.json()
        return body if isinstance(body, list) else []

    async def select_one(
        self, table: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = await self.select(table, params={**(params or {}), "limit": 1})
        return rows[0] if rows else None

    async def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Insert, or replace the row with the same primary key.

        `resolution=merge-duplicates` is what makes a second write to the same
        call id an update rather than a 409 — call records are written many
        times as a call progresses.
        """
        response = await self._send(
            "POST",
            f"/{table}",
            json=[row],
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        body = response.json()
        return body[0] if isinstance(body, list) and body else row

    async def delete(self, table: str, *, params: dict[str, Any]) -> None:
        await self._send("DELETE", f"/{table}", params=params)

    async def count(self, table: str, *, params: dict[str, Any] | None = None) -> int:
        """A row count without transferring the rows.

        PostgREST returns it in Content-Range as `*/<n>` when asked for an
        exact count over an empty range.
        """
        response = await self._send(
            "GET",
            f"/{table}",
            params={**(params or {}), "select": "id", "limit": 0},
            headers={"Prefer": "count=exact"},
        )
        header = response.headers.get("content-range", "")
        _, _, total = header.partition("/")
        return int(total) if total.isdigit() else 0

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = await self.rest().request(
                method, path, params=params, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"The database did not respond: {exc}") from exc
        if response.status_code >= 400:
            # The body carries PostgREST's own message, which names the column
            # or constraint — far more use than the status alone.
            log.warning(
                "supabase %s %s -> %s: %s",
                method, path, response.status_code, response.text[:300],
            )
            raise UpstreamError(
                f"The database rejected the request ({response.status_code}).",
                details=_safe_detail(response.text),
            )
        return response


def _safe_detail(text: str) -> str | None:
    """PostgREST errors are safe to pass on; they never echo the service key."""
    cleaned = (text or "").strip()
    return cleaned[:300] or None


supabase = SupabaseClient()
