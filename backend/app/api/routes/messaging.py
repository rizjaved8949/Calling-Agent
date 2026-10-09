"""
WhatsApp messaging for a company: send, list, and read the template catalogue.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status

from ...models.call import SendTemplateRequest, SendTextRequest
from ...repositories import calls as call_repo
from ...security.rate_limit import check_expensive
from ...services.whatsapp import WhatsApp, mask
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messaging"])


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


@router.post("/text", status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_expensive)])
async def send_text(tenant: CurrentTenant, payload: SendTextRequest) -> dict:
    """Send a free-text message.

    Only reaches someone inside the 24-hour customer service window. Outside
    it, Meta accepts the request and never delivers the message — which is why
    a first contact has to be a template.
    """
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
async def list_templates(tenant: CurrentTenant) -> dict:
    """The company's approved templates, straight from their WABA.

    Not cached: a template's status changes on Meta's schedule, and showing a
    stale "approved" for one they have since rejected sends the operator to
    debug a message that was never going to arrive.
    """
    templates = await WhatsApp(tenant).templates()
    return {
        "templates": [
            {
                "name": t.get("name"),
                "status": t.get("status"),
                "category": t.get("category"),
                "language": t.get("language"),
            }
            for t in templates
        ]
    }
