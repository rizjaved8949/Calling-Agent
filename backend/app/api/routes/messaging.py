"""
WhatsApp messaging for a company: send, list, and read the template catalogue.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response, status

from ...errors import AppError, NotFound
from ...models.call import SendTemplateRequest, SendTextRequest
from ...models.number import NumberKind
from ...models.tenant import Tenant
from ...repositories import calls as call_repo
from ...repositories import numbers as number_repo
from ...security.rate_limit import check_expensive
from ...services.whatsapp import WhatsApp, mask
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messaging"])


async def _speaking_as(tenant: Tenant, line_id: str) -> Tenant:
    """The WhatsApp number to send from: the one named, else the first verified."""
    whatsapp = [
        n for n in await number_repo.list_for(tenant.phone_number_id)
        if n.kind is NumberKind.WHATSAPP
    ]
    if line_id:
        line = next((n for n in whatsapp if n.id == line_id), None)
        if line is None:
            raise NotFound("WhatsApp number")
    else:
        line = next((n for n in whatsapp if n.verified), None)
        if line is None and whatsapp:
            raise AppError(409, "None of your WhatsApp numbers is verified yet.",
                           code="number_not_verified")
    return number_repo.as_tenant(tenant, line) if line else tenant


@router.get("")
async def list_messages(
    tenant: CurrentTenant,
    counterparty: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    messages = await call_repo.list_messages(
        tenant.phone_number_id, counterparty=counterparty, limit=limit
    )
    return {"messages": [m.public() for m in messages]}


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response, response_model=None)
async def delete_message(tenant: CurrentTenant, message_id: str) -> None:
    """Remove a message from this company's log.

    Only our own record: WhatsApp has no API for unsending, and pretending
    otherwise would be the worst kind of button. The UI says so.
    """
    message = await call_repo.get_message(tenant.phone_number_id, message_id)
    if message is None:
        raise NotFound("Message")
    await call_repo.delete_message(tenant.phone_number_id, message_id)
    log.info("tenant %s deleted message %s from its log", tenant.phone_number_id, message_id)


@router.post("/{message_id}/read")
async def mark_read(tenant: CurrentTenant, message_id: str) -> dict:
    """Show the sender a read receipt, the way opening a chat does."""
    message = await call_repo.get_message(tenant.phone_number_id, message_id)
    if message is None:
        raise NotFound("Message")
    if not message.provider_message_id:
        return {"status": "skipped"}
    speaking = await _speaking_as(tenant, message.line_id)
    await WhatsApp(speaking).mark_read(message.provider_message_id)
    return {"status": "read"}


@router.post("/text", status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_expensive)])
async def send_text(tenant: CurrentTenant, payload: SendTextRequest) -> dict:
    """Send a free-text message.

    Only reaches someone inside the 24-hour customer service window. Outside
    it, Meta accepts the request and never delivers the message — which is why
    a first contact has to be a template.
    """
    tenant = await _speaking_as(tenant, payload.line_id)
    message = await WhatsApp(tenant).send_text(
        payload.to, payload.body, call_id=payload.call_id or ""
    )
    log.info("sent a WhatsApp text to %s for %s", mask(payload.to), tenant.phone_number_id)
    return message.public()


@router.post(
    "/template", status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_expensive)]
)
async def send_template(tenant: CurrentTenant, payload: SendTemplateRequest) -> dict:
    """Send an approved template — the only way to open a conversation."""
    tenant = await _speaking_as(tenant, payload.line_id)
    message = await WhatsApp(tenant).send_template(
        payload.to,
        payload.template,
        language=payload.language,
        parameters=payload.parameters,
        call_id=payload.call_id or "",
    )
    log.info(
        "sent template %r to %s for %s",
        payload.template, mask(payload.to), tenant.phone_number_id,
    )
    return message.public()


@router.get("/templates")
async def list_templates(tenant: CurrentTenant, lineId: str = "") -> dict:
    """The company's approved templates, straight from their WABA.

    Not cached: a template's status changes on Meta's schedule, and showing a
    stale "approved" for one they have since rejected sends the operator to
    debug a message that was never going to arrive.
    """
    tenant = await _speaking_as(tenant, lineId)
    return {"templates": await WhatsApp(tenant).templates()}
