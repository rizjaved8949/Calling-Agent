"""
The live part of live chat: who is watching, and what to tell them.

A message is already saved before it reaches here. This only pushes it to
whoever has the conversation open, so neither side has to reload to see a
reply, and so the operator's list of companies reorders itself the moment
someone writes. Everything here is best-effort by design: a dropped event
costs a redraw, never a message, because the message is in the database first
and the client asks for history when it connects.

Subscribers are held in this process, like the live call sessions in
`agent/live.py`. That is sound for as long as the service runs as one process,
which it does — the call sessions would break first, and far more loudly.

Two kinds of watcher:

* a **conversation** watcher, which wants one company's thread. The company's
  own browser, or the operator with that company open.
* a **lobby** watcher, which wants to know that *something* happened anywhere,
  so the operator's company list and its unread badge stay honest without
  polling.

Queues are small and bounded. A browser that stops reading is a browser whose
tab is suspended; dropping its backlog and letting it refetch on wake is
better than growing a queue behind it for as long as the laptop is shut.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_LIMIT = 32


class Watcher:
    """One open socket's mailbox.

    `tenant_id` empty means the lobby: every event, from every company.
    """

    __slots__ = ("tenant_id", "queue", "side", "dropped")

    def __init__(self, tenant_id: str, side: str):
        self.tenant_id = tenant_id
        self.side = side
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self.dropped = 0

    def offer(self, event: dict[str, Any]) -> None:
        """Queue an event, or drop it. Never blocks and never raises."""
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            # Tell the client its view may be behind rather than leaving it
            # confidently wrong; it reloads history on seeing this.
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.get_nowait()
                self.queue.put_nowait({"type": "resync"})

    async def next(self, timeout: float) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return None


_watchers: set[Watcher] = set()


def join(tenant_id: str, side: str) -> Watcher:
    watcher = Watcher(tenant_id, side)
    _watchers.add(watcher)
    return watcher


def leave(watcher: Watcher) -> None:
    _watchers.discard(watcher)


def publish(
    tenant_id: str,
    event: dict[str, Any],
    *,
    lobby_only: bool = False,
    to_side: str = "",
) -> None:
    """Hand an event to everyone who should see it.

    `lobby_only` is for things that change a list but not a conversation — a
    recount, say — so an open thread is not redrawn for no reason.

    `to_side` sends only to the other party. Presence uses it: that the
    operator has arrived is news to the company and to nobody else, and
    without this a socket is told about its own joining.
    """
    payload = {**event, "tenantId": tenant_id}
    for watcher in list(_watchers):
        if watcher.tenant_id:
            if lobby_only or watcher.tenant_id != tenant_id:
                continue
            if to_side and watcher.side != to_side:
                continue
        elif to_side:
            # The lobby is the operator's, so it sees what the operator sees.
            if to_side != "operator":
                continue
        watcher.offer(payload)


def operator_is_watching(tenant_id: str) -> bool:
    """Whether someone from the platform has this company's thread open.

    The company is shown this, so "support is here" is a fact rather than a
    hope. It is also why a reply can be marked read immediately: the operator
    really is looking at it.
    """
    return any(
        w.side == "operator" and w.tenant_id == tenant_id for w in _watchers
    )


def company_is_watching(tenant_id: str) -> bool:
    return any(w.side == "company" and w.tenant_id == tenant_id for w in _watchers)


def watching_count() -> int:
    """For the health endpoint."""
    return len(_watchers)
