"""
Live chat between a company and the platform operator.

Three surfaces on one conversation:

* the **company**, authenticated the way every other company route is — by its
  own API key, so the thread it reaches is derived from the credential and
  there is no request shape that asks for someone else's.
* the **operator**, authenticated by `X-Admin-Key` or a super-admin session,
  who names the company in the path because they are allowed to.
* a **websocket** for both, authenticated by a short-lived ticket, because a
  browser cannot put a header on a websocket handshake.

The transport is a websocket but the truth is the database. A message is saved
before it is pushed, and a client asks for history when it connects, so a
dropped socket loses nothing but liveness.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, Response

from ...errors import AppError, NotFound
from ...models.support import (
    EXTENSIONS, MAX_BODY_CHARS, MAX_BYTES, Attachment, Author, SupportMessage, kind_of,
)
from ...repositories import support as repo
from ...repositories import tenants as tenant_repo
from ...security import tickets
from ...security.rate_limit import check_expensive
from ...services import storage, support_hub
from ..deps import AdminOnly, CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/support", tags=["support"])
platform_router = APIRouter(prefix="/platform/support", tags=["support"])
socket_router = APIRouter(prefix="/support", tags=["support"])

SOCKET_AUDIENCE = "support-socket"
FILE_AUDIENCE = "support-file"
#: Long enough to leave a tab open for a working day without it going dead,
#: and the socket re-tickets on reconnect anyway.
SOCKET_TICKET_TTL = 8 * 3600
KEEPALIVE_SECONDS = 25.0


# ---------------------------------------------------------------------------
# Writing a message
# ---------------------------------------------------------------------------


async def _store_attachment(
    tenant_id: str, message_id: str, file: UploadFile, duration: float
) -> Attachment:
    mime = (file.content_type or "").split(";", 1)[0].strip().lower()
    kind = kind_of(mime)
    if kind is None:
        raise AppError(
            415,
            f"{mime or 'That kind of file'} cannot be attached. Send a photo, a "
            "video, a voice note, a PDF or a text file.",
            code="unsupported_media",
        )
    data = await file.read()
    if not data:
        raise AppError(400, "That file is empty.", code="empty_file")
    limit = MAX_BYTES[kind]
    if len(data) > limit:
        raise AppError(
            413,
            f"That {kind.value} is {len(data) // 1_048_576} MB. "
            f"The limit is {limit // 1_048_576} MB.",
            code="too_large",
        )

    extension = EXTENSIONS.get(mime, "bin")
    key = f"tenants/{tenant_id}/support/{message_id}.{extension}"
    reference = await storage.put_object(key, data, mime)
    if reference is None:
        raise AppError(
            502,
            "That file could not be stored. Try again, or send a smaller one.",
            code="storage_failed",
        )
    return Attachment(
        reference=reference, mime=mime, name=file.filename or f"{kind.value}.{extension}",
        bytes=len(data), kind=kind, duration_seconds=max(0.0, duration),
    )


async def _post(
    tenant_id: str, author: Author, author_name: str, body: str,
    file: UploadFile | None, duration: float,
) -> dict:
    text = (body or "").strip()[:MAX_BODY_CHARS]
    if not text and file is None:
        raise AppError(400, "Write something, or attach a file.", code="empty_message")

    message = SupportMessage(
        tenant_id=tenant_id, author=author, author_name=author_name, body=text,
    )
    if file is not None:
        message.attachment = await _store_attachment(tenant_id, message.id, file, duration)

    # The other side being in the room is the only honest reason to call
    # something read, and it is the reason the tick appears instantly in a
    # conversation where both people are present.
    other_watching = (
        support_hub.operator_is_watching(tenant_id) if author is Author.COMPANY
        else support_hub.company_is_watching(tenant_id)
    )
    if other_watching:
        message.read_at = time.time()

    await repo.append(message)
    if other_watching:
        await repo.mark_read(tenant_id, Author.OPERATOR if author is Author.COMPANY
                             else Author.COMPANY)

    public = message.public()
    support_hub.publish(tenant_id, {"type": "message", "message": public})
    log.info("support: %s wrote to thread %s%s", author.value, tenant_id,
             f" with a {message.attachment.kind.value}" if message.attachment else "")
    return public


# ---------------------------------------------------------------------------
# The company's own side
# ---------------------------------------------------------------------------


@router.get("/messages")
async def company_history(tenant: CurrentTenant, limit: int = Query(default=200, le=500)) -> dict:
    tenant_id = tenant.phone_number_id
    messages = await repo.history(tenant_id, limit=limit)
    thread = await repo.thread_for(tenant_id)
    return {
        "messages": [m.public() for m in messages],
        "thread": thread.public(),
        "operatorOnline": support_hub.operator_is_watching(tenant_id),
    }


@router.post("/messages", dependencies=[Depends(check_expensive)])
async def company_write(
    tenant: CurrentTenant,
    body: str = Form(default=""),
    durationSeconds: float = Form(default=0.0),
    file: UploadFile | None = File(default=None),
) -> dict:
    return await _post(
        tenant.phone_number_id, Author.COMPANY, tenant.name or "", body, file, durationSeconds,
    )


@router.post("/read")
async def company_read(tenant: CurrentTenant) -> dict:
    tenant_id = tenant.phone_number_id
    changed = await repo.mark_read(tenant_id, Author.COMPANY)
    if changed:
        support_hub.publish(tenant_id, {"type": "read", "by": Author.COMPANY.value})
    return {"read": changed}


@router.delete("/messages/{message_id}")
async def company_delete(tenant: CurrentTenant, message_id: str) -> dict:
    return await _delete(tenant.phone_number_id, message_id, Author.COMPANY)


@router.get("/ticket")
async def company_ticket(tenant: CurrentTenant) -> dict:
    """A ticket that lets this company's browser open the chat socket."""
    return {
        "ticket": tickets.mint(
            SOCKET_AUDIENCE, tenant.phone_number_id,
            ttl=SOCKET_TICKET_TTL, extra={"r": "company"},
        ),
        "expiresIn": SOCKET_TICKET_TTL,
    }


# ---------------------------------------------------------------------------
# The operator's side
# ---------------------------------------------------------------------------


@platform_router.get("/threads")
async def operator_threads(_: AdminOnly) -> dict:
    """Every company, with whatever it is waiting on.

    Companies that have never written are included: the operator starting a
    conversation is a legitimate thing to want, and a list that only shows
    people who already complained cannot be used for it.
    """
    companies = await tenant_repo.list_all()
    threads = await repo.all_threads()
    rows = []
    for company in companies:
        tenant_id = company.phone_number_id
        thread = threads.get(tenant_id)
        rows.append({
            "tenantId": tenant_id,
            "name": company.name or tenant_id,
            "suspended": company.suspended,
            "online": support_hub.company_is_watching(tenant_id),
            **(thread.public() if thread else {
                "lastMessageAt": 0.0, "lastPreview": "", "lastAuthor": None,
                "unreadForCompany": 0, "unreadForOperator": 0,
            }),
        })
    rows.sort(key=lambda r: (r["unreadForOperator"] > 0, r["lastMessageAt"]), reverse=True)
    return {
        "threads": rows,
        "waiting": sum(1 for r in rows if r["unreadForOperator"] > 0),
    }


@platform_router.get("/{tenant_id}/messages")
async def operator_history(
    _: AdminOnly, tenant_id: str, limit: int = Query(default=200, le=500)
) -> dict:
    company = await tenant_repo.get(tenant_id)
    if company is None:
        raise NotFound("Company")
    messages = await repo.history(tenant_id, limit=limit)
    thread = await repo.thread_for(tenant_id)
    return {
        "messages": [m.public() for m in messages],
        "thread": thread.public(),
        "company": {"id": tenant_id, "name": company.name or tenant_id,
                    "suspended": company.suspended},
        "companyOnline": support_hub.company_is_watching(tenant_id),
    }


@platform_router.post("/{tenant_id}/messages", dependencies=[Depends(check_expensive)])
async def operator_write(
    _: AdminOnly,
    tenant_id: str,
    body: str = Form(default=""),
    durationSeconds: float = Form(default=0.0),
    file: UploadFile | None = File(default=None),
) -> dict:
    if await tenant_repo.get(tenant_id) is None:
        raise NotFound("Company")
    return await _post(tenant_id, Author.OPERATOR, "Support", body, file, durationSeconds)


@platform_router.post("/{tenant_id}/read")
async def operator_read(_: AdminOnly, tenant_id: str) -> dict:
    changed = await repo.mark_read(tenant_id, Author.OPERATOR)
    if changed:
        support_hub.publish(tenant_id, {"type": "read", "by": Author.OPERATOR.value})
    return {"read": changed}


@platform_router.delete("/{tenant_id}/messages/{message_id}")
async def operator_delete(_: AdminOnly, tenant_id: str, message_id: str) -> dict:
    return await _delete(tenant_id, message_id, Author.OPERATOR)


@platform_router.get("/ticket")
async def operator_ticket(_: AdminOnly, tenantId: str = Query(default="")) -> dict:
    """A ticket for one company's thread, or for the lobby.

    The lobby ticket carries no company: it only ever delivers "something
    changed for X", which is exactly what the unread badge needs and nothing
    an operator is not already allowed to see.
    """
    return {
        "ticket": tickets.mint(
            SOCKET_AUDIENCE, tenantId or "*",
            ttl=SOCKET_TICKET_TTL,
            extra={"r": "operator" if tenantId else "lobby"},
        ),
        "expiresIn": SOCKET_TICKET_TTL,
    }


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


async def _delete(tenant_id: str, message_id: str, by: Author) -> dict:
    """Remove a message from the conversation, for both sides.

    There are two parties and one shared history, so "delete for me" would
    leave the operator answering something the company can no longer see. The
    company may only withdraw its own messages; the operator may remove
    anything, because the operator is the one who has to deal with whatever
    somebody uploaded.
    """
    message = await repo.get(tenant_id, message_id)
    if message is None:
        raise NotFound("Message")
    if by is Author.COMPANY and message.author is not Author.COMPANY:
        raise AppError(403, "You can only delete your own messages.", code="not_yours")

    if message.attachment and message.attachment.reference:
        await storage.delete_object(message.attachment.reference)
    await repo.remove(tenant_id, message_id)
    thread = await repo.recount(tenant_id)
    support_hub.publish(tenant_id, {"type": "deleted", "messageId": message_id,
                                    "thread": thread.public()})
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


@router.get("/attachments/{message_id}")
async def attachment_link(
    tenant: CurrentTenant, message_id: str
) -> dict:
    """A short-lived URL the browser can put in an <img>, <video> or <audio>."""
    message = await repo.get(tenant.phone_number_id, message_id)
    if message is None or message.attachment is None:
        raise NotFound("Attachment")
    return _link(tenant.phone_number_id, message_id, message.attachment)


@platform_router.get("/{tenant_id}/attachments/{message_id}")
async def operator_attachment_link(_: AdminOnly, tenant_id: str, message_id: str) -> dict:
    message = await repo.get(tenant_id, message_id)
    if message is None or message.attachment is None:
        raise NotFound("Attachment")
    return _link(tenant_id, message_id, message.attachment)


def _link(tenant_id: str, message_id: str, attachment: Attachment) -> dict:
    ticket = tickets.mint(
        FILE_AUDIENCE, f"{tenant_id}:{message_id}", ttl=tickets.DEFAULT_TTL_SECONDS,
    )
    return {
        "url": f"/api/support/file/{message_id}?ticket={ticket}",
        "mime": attachment.mime,
        "name": attachment.name,
        "expiresIn": tickets.DEFAULT_TTL_SECONDS,
    }


@socket_router.get("/file/{message_id}")
async def fetch_attachment(message_id: str, ticket: str = Query(default="")):
    """The file itself, for a browser that cannot send a header.

    Where the object is in Supabase the browser is redirected to a signed URL
    and fetches it directly: a 40 MB video does not need to cross this server,
    and seeking in it needs the range requests that storage already serves.
    Only the local-disk fallback is streamed through here.
    """
    body = tickets.verify(ticket, FILE_AUDIENCE)
    subject = str(body["s"])
    tenant_id, _, ticketed_id = subject.partition(":")
    # The id is taken from the ticket, not the path: without this, one ticket
    # would fetch every attachment in the thread.
    if not tenant_id or ticketed_id != message_id:
        raise AppError(403, "This link is no longer valid.", code="bad_ticket")

    message = await repo.get(tenant_id, message_id)
    if message is None or message.attachment is None:
        raise NotFound("Attachment")

    reference = message.attachment.reference
    signed = await storage.signed_url(reference)
    if signed:
        return RedirectResponse(signed, status_code=307)

    data = await storage.get_object(reference)
    if data is None:
        raise NotFound("Attachment")
    return Response(
        content=data,
        media_type=message.attachment.mime,
        headers={
            "Content-Disposition":
                f'inline; filename="{message.attachment.name}"',
            "Cache-Control": "private, max-age=300",
            "Accept-Ranges": "none",
        },
    )


# ---------------------------------------------------------------------------
# The socket
# ---------------------------------------------------------------------------


@socket_router.websocket("/live")
async def support_socket(socket: WebSocket, ticket: str = Query(default="")):
    """One open conversation, or the operator's lobby.

    Sends: hello, message, read, typing, presence, deleted, resync, ping.
    Receives: typing, read, ping. Anything else is ignored rather than
    closing the socket — a newer client talking to an older server should
    lose a feature, not the chat.
    """
    try:
        body = tickets.verify(ticket, SOCKET_AUDIENCE)
    except AppError:
        await socket.close(code=4401)
        return

    subject = str(body["s"])
    role = str(body.get("r") or "")
    if role not in {"company", "operator", "lobby"}:
        await socket.close(code=4401)
        return
    tenant_id = "" if role == "lobby" else subject
    if role != "lobby" and not tenant_id:
        await socket.close(code=4401)
        return

    await socket.accept()
    side = "company" if role == "company" else "operator"
    watcher = support_hub.join(tenant_id, side)

    # Joining is itself news: the other side shows "support is here", and a
    # company's reply can be ticked as read while someone is actually looking.
    if tenant_id:
        support_hub.publish(tenant_id, {
            "type": "presence", "side": side, "online": True,
        }, to_side="company" if side == "operator" else "operator")

    with contextlib.suppress(Exception):
        await socket.send_text(json.dumps({
            "type": "hello",
            "side": side,
            "tenantId": tenant_id,
            "operatorOnline": bool(tenant_id) and support_hub.operator_is_watching(tenant_id),
            "companyOnline": bool(tenant_id) and support_hub.company_is_watching(tenant_id),
        }))

    reader = asyncio.create_task(_read_socket(socket, tenant_id, side))
    try:
        while True:
            if reader.done():
                break
            event = await watcher.next(KEEPALIVE_SECONDS)
            # A proxy between here and the browser will close an idle socket.
            # A ping costs nothing and keeps a quiet conversation open.
            payload = event if event is not None else {"type": "ping"}
            await socket.send_text(json.dumps(payload))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001 — a failed socket is a closed socket
        log.exception("support socket for %s failed", tenant_id or "lobby")
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        support_hub.leave(watcher)
        if tenant_id:
            support_hub.publish(tenant_id, {
                "type": "presence", "side": side,
                "online": (support_hub.operator_is_watching(tenant_id) if side == "operator"
                           else support_hub.company_is_watching(tenant_id)),
            }, to_side="company" if side == "operator" else "operator")
        with contextlib.suppress(Exception):
            await socket.close()


async def _read_socket(socket: WebSocket, tenant_id: str, side: str) -> None:
    """Whatever the browser sends up. Ends when the socket does."""
    author = Author.COMPANY if side == "company" else Author.OPERATOR
    while True:
        try:
            raw = await socket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return
        try:
            frame = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(frame, dict) or not tenant_id:
            continue

        kind = frame.get("type")
        if kind == "typing":
            support_hub.publish(tenant_id, {
                "type": "typing", "side": side, "on": bool(frame.get("on")),
            })
        elif kind == "read":
            changed = await repo.mark_read(tenant_id, author)
            if changed:
                support_hub.publish(tenant_id, {"type": "read", "by": author.value})
