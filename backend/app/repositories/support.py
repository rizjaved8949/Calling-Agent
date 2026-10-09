"""
Storing the support conversation.

Two tables rather than one. Messages are append-only history; the thread row is
a running summary — who spoke last, how much neither side has read. The summary
exists so that drawing the operator's list of companies is one query over
companies, not a scan of every message every company ever sent.

The summary is therefore a cache, and a cache can drift. `recount` rebuilds one
from the messages, and the unread figures are only ever used to draw a badge —
nothing is permitted or refused on their strength — so a stale count shows the
wrong number and costs nothing else.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..db.supabase import supabase
from ..models.support import Author, SupportMessage, SupportThread

log = logging.getLogger(__name__)

MESSAGES = "voice_support_messages"
THREADS = "voice_support_threads"

PREVIEW_CHARS = 120


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(row: dict[str, Any]) -> SupportMessage | None:
    data = row.get("data")
    message_id = str(row.get("id") or "")
    if not isinstance(data, dict) or not message_id:
        return None
    payload = {**data, "id": message_id}
    if row.get("tenant_id") and not payload.get("tenant_id"):
        payload["tenant_id"] = row["tenant_id"]
    try:
        return SupportMessage.model_validate(payload)
    except Exception:  # noqa: BLE001 — one unreadable row must not empty the thread
        log.exception("support message %s cannot be read by this build", message_id)
        return None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


async def history(tenant_id: str, *, limit: int = 200, before: float = 0.0
                  ) -> list[SupportMessage]:
    """The conversation, oldest first.

    Fetched newest-first so that a limit keeps the *recent* end of a long
    thread, then reversed, because that is the order it is read in.
    """
    params: dict[str, Any] = {
        "tenant_id": f"eq.{tenant_id}",
        "order": "updated_at.desc",
        "limit": max(1, min(limit, 500)),
    }
    if before:
        params["updated_at"] = f"lt.{datetime.fromtimestamp(before, timezone.utc).isoformat()}"
    try:
        rows = await supabase.select(MESSAGES, params=params)
    except Exception:  # noqa: BLE001
        log.exception("could not read the support thread for %s", tenant_id)
        return []
    found = [m for m in (_parse(r) for r in rows) if m is not None]
    found.sort(key=lambda m: m.created_at)
    return found


async def get(tenant_id: str, message_id: str) -> SupportMessage | None:
    try:
        row = await supabase.select_one(MESSAGES, params={
            "id": f"eq.{message_id}", "tenant_id": f"eq.{tenant_id}",
        })
    except Exception:  # noqa: BLE001
        log.exception("could not read support message %s", message_id)
        return None
    return _parse(row) if row else None


async def append(message: SupportMessage) -> SupportMessage:
    """Write a message and move the thread summary on."""
    data = message.model_dump(mode="json", exclude={"id"})
    await supabase.upsert(MESSAGES, {
        "id": message.id,
        "tenant_id": message.tenant_id,
        "data": data,
        # The row's own timestamp is the message time, not the write time, so
        # ordering by it orders the conversation.
        "updated_at": datetime.fromtimestamp(message.created_at, timezone.utc).isoformat(),
    })

    thread = await thread_for(message.tenant_id)
    thread.last_message_at = message.created_at
    thread.last_author = message.author
    thread.last_preview = _preview(message)
    if message.author is Author.COMPANY:
        thread.unread_for_operator += 1
        thread.company_waiting = True
    else:
        thread.unread_for_company += 1
        thread.company_waiting = False
    await save_thread(thread)
    return message


def _preview(message: SupportMessage) -> str:
    if message.body.strip():
        text = " ".join(message.body.split())
        return text[:PREVIEW_CHARS]
    if message.attachment:
        return {
            "image": "Sent a photo", "video": "Sent a video",
            "voice": "Sent a voice note", "file": "Sent a file",
        }.get(message.attachment.kind.value, "Sent a file")
    return ""


async def remove(tenant_id: str, message_id: str) -> None:
    try:
        await supabase.delete(MESSAGES, params={
            "id": f"eq.{message_id}", "tenant_id": f"eq.{tenant_id}",
        })
    except Exception:  # noqa: BLE001
        log.exception("could not delete support message %s", message_id)


async def mark_read(tenant_id: str, reader: Author) -> int:
    """Mark everything the *other* side wrote as read. Returns how many.

    Each message carries its own `readAt` so the sender can see a tick, and the
    thread counter is zeroed in the same breath. The two can disagree if a
    write fails halfway; the counter is the one that only draws a badge.
    """
    other = Author.OPERATOR if reader is Author.COMPANY else Author.COMPANY
    now = time.time()
    changed = 0
    for message in await history(tenant_id, limit=500):
        if message.author is other and message.read_at is None:
            message.read_at = now
            await supabase.upsert(MESSAGES, {
                "id": message.id,
                "tenant_id": message.tenant_id,
                "data": message.model_dump(mode="json", exclude={"id"}),
                "updated_at": datetime.fromtimestamp(
                    message.created_at, timezone.utc).isoformat(),
            })
            changed += 1

    thread = await thread_for(tenant_id)
    if reader is Author.COMPANY:
        thread.unread_for_company = 0
    else:
        thread.unread_for_operator = 0
        thread.company_waiting = False
    await save_thread(thread)
    return changed


# ---------------------------------------------------------------------------
# Thread summaries
# ---------------------------------------------------------------------------


async def thread_for(tenant_id: str) -> SupportThread:
    """The summary, or an empty one. Never raises: a missing summary is a
    thread nobody has written in yet, which is the common case."""
    try:
        row = await supabase.select_one(THREADS, params={"id": f"eq.{tenant_id}"})
    except Exception:  # noqa: BLE001
        log.exception("could not read the support summary for %s", tenant_id)
        return SupportThread(tenant_id=tenant_id)
    data = (row or {}).get("data") if isinstance(row, dict) else None
    if not isinstance(data, dict):
        return SupportThread(tenant_id=tenant_id)
    try:
        return SupportThread.model_validate({**data, "tenant_id": tenant_id})
    except Exception:  # noqa: BLE001
        log.exception("support summary for %s cannot be read", tenant_id)
        return SupportThread(tenant_id=tenant_id)


async def save_thread(thread: SupportThread) -> None:
    try:
        await supabase.upsert(THREADS, {
            "id": thread.tenant_id,
            "tenant_id": thread.tenant_id,
            "data": thread.model_dump(mode="json", exclude={"tenant_id"}),
            "updated_at": _timestamp(),
        })
    except Exception:  # noqa: BLE001 — a lost summary must not lose the message
        log.exception("could not save the support summary for %s", thread.tenant_id)


async def all_threads() -> dict[str, SupportThread]:
    """Every company's summary, for the operator's list. Keyed by company id."""
    try:
        rows = await supabase.select(THREADS, params={"order": "updated_at.desc"})
    except Exception:  # noqa: BLE001
        log.exception("could not list the support threads")
        return {}
    found: dict[str, SupportThread] = {}
    for row in rows:
        tenant_id = str(row.get("id") or "")
        data = row.get("data")
        if not tenant_id or not isinstance(data, dict):
            continue
        try:
            found[tenant_id] = SupportThread.model_validate({**data, "tenant_id": tenant_id})
        except Exception:  # noqa: BLE001
            log.exception("support summary for %s cannot be read", tenant_id)
    return found


async def recount(tenant_id: str) -> SupportThread:
    """Rebuild a summary from the messages themselves."""
    messages = await history(tenant_id, limit=500)
    thread = SupportThread(tenant_id=tenant_id)
    for message in messages:
        if message.read_at is not None:
            continue
        if message.author is Author.COMPANY:
            thread.unread_for_operator += 1
        else:
            thread.unread_for_company += 1
    if messages:
        last = messages[-1]
        thread.last_message_at = last.created_at
        thread.last_author = last.author
        thread.last_preview = _preview(last)
        thread.company_waiting = (
            last.author is Author.COMPANY and thread.unread_for_operator > 0
        )
    await save_thread(thread)
    return thread


async def purge(tenant_id: str) -> None:
    """Erase the whole conversation. Called when a company is deleted."""
    for table in (MESSAGES, THREADS):
        try:
            key = "tenant_id" if table == MESSAGES else "id"
            await supabase.delete(table, params={key: f"eq.{tenant_id}"})
        except Exception:  # noqa: BLE001
            log.exception("could not purge %s for %s", table, tenant_id)
