"""
A local stand-in for the database, so the stack runs with nothing installed.

Supabase is the real answer. This exists because the alternative — needing a
Supabase project before the app will do anything at all — means developing
against production data, which is how a test company ends up in a customer's
dashboard.

Same method signatures as `SupabaseClient`, including the subset of PostgREST
filter syntax the repositories use (`eq.`, `gte.`, `order`, `limit`, `offset`),
so the code above it cannot tell the difference.

Rows live in one JSON file and are written on every change. That is the wrong
shape for anything with real traffic, and deliberately so: it should be
uncomfortable to run this anywhere but a laptop, and `/health` reports it.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from ..config import BASE_DIR

log = logging.getLogger(__name__)


class LocalStore:
    """A JSON file pretending to be PostgREST."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (BASE_DIR / "data" / "local-store.json")
        # Writes come from the event loop, but a rewrite is a read-modify-write
        # of a whole file and two overlapping ones would truncate each other.
        self._lock = threading.Lock()
        self._tables: dict[str, dict[str, dict[str, Any]]] = {}
        self._load()

    # ---- The same surface SupabaseClient offers ---------------------------

    @property
    def configured(self) -> bool:
        """True: rows do persist. It is simply not a database."""
        return True

    @property
    def is_local(self) -> bool:
        return True

    def storage(self) -> Any:
        raise RuntimeError(
            "The local store has no object storage; recordings fall back to disk."
        )

    async def close(self) -> None:
        return None

    async def select(
        self, table: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        params = params or {}
        rows = [row for row in self._table(table).values() if _matches(row, params)]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", len(rows)) or len(rows))
        return [json.loads(json.dumps(row)) for row in rows[offset : offset + limit]]

    async def select_one(
        self, table: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = await self.select(table, params=params)
        return rows[0] if rows else None

    async def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._table(table)[str(row["id"])] = json.loads(json.dumps(row))
            self._save()
        return row

    async def delete(self, table: str, *, params: dict[str, Any]) -> None:
        with self._lock:
            table_rows = self._table(table)
            for key in [k for k, row in table_rows.items() if _matches(row, params)]:
                table_rows.pop(key, None)
            self._save()

    async def count(self, table: str, *, params: dict[str, Any] | None = None) -> int:
        params = dict(params or {})
        params.pop("limit", None)
        params.pop("offset", None)
        return len(await self.select(table, params=params))

    # ---- File handling ----------------------------------------------------

    def _table(self, name: str) -> dict[str, dict[str, Any]]:
        return self._tables.setdefault(name, {})

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._tables = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            # A half-written file is not worth crashing over, but losing the
            # rows silently would be worse than saying so.
            log.exception("could not read %s — starting empty", self.path)
            self._tables = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Written beside the target and moved into place, so an interrupted
            # write leaves the previous file intact rather than a truncated one.
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._tables, indent=2), "utf-8")
            temporary.replace(self.path)
        except OSError:
            log.exception("could not write %s", self.path)


def _value(row: dict[str, Any], field: str) -> Any:
    if field.startswith("data->>"):
        return (row.get("data") or {}).get(field[len("data->>") :])
    return row.get(field)


def _matches(row: dict[str, Any], params: dict[str, Any]) -> bool:
    for field, condition in params.items():
        if field in {"order", "limit", "offset", "select"}:
            continue
        condition = str(condition)
        if condition.startswith("eq."):
            if str(_value(row, field)) != condition[3:]:
                return False
        elif condition.startswith("gte."):
            if str(_value(row, field) or "") < condition[4:]:
                return False
    return True
