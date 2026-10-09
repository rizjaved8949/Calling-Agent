"""
A company's numbers: connect one, prove it works, choose who answers it.

The order the screen walks through is the order this enforces:

    add (credentials) → verify (provider says yes) → assign agents → live

A number that has not been verified cannot be given an agent, and editing any
credential sends it back to pending, so "verified" always means "these exact
credentials were accepted".
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status

from ...errors import AppError, Conflict, NotFound
from ...models.agent import AgentMode
from ...models.number import (
    CREDENTIAL_FIELDS,
    SECRET_ATTRS,
    NumberCreate,
    NumberKind,
    NumberStatus,
    NumberUpdate,
    PhoneNumber,
)
from ...models.tenant import Tenant
from ...repositories import agents as agent_repo
from ...repositories import numbers as repo
from ...security.rate_limit import check_expensive
from ...services import lines
from ...services.whatsapp import to_e164
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/numbers", tags=["numbers"])


def _out(number: PhoneNumber) -> dict:
    return number.public(base_url=lines.webhook_base())


@router.get("")
async def list_numbers(tenant: CurrentTenant) -> dict:
    numbers = await repo.list_for(tenant.phone_number_id)
    return {
        "numbers": [_out(n) for n in numbers],
        "platformCredentialsAllowed": tenant.allow_platform_credentials,
        "whatsappWebhookUrl": (
            f"{lines.webhook_base()}/api/webhooks/whatsapp" if lines.webhook_base() else None
        ),
    }


@router.get("/{number_id}")
async def get_number(tenant: CurrentTenant, number_id: str) -> dict:
    return _out(await _require(tenant, number_id))


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_number(tenant: CurrentTenant, payload: NumberCreate) -> dict:
    if payload.use_platform_credentials and not tenant.allow_platform_credentials:
        raise AppError(
            403, "This account is not allowed to use the platform's own credentials.",
            code="platform_credentials_not_allowed",
        )
    phone = to_e164(payload.phone_number, tenant)
    if await repo.by_phone(tenant.phone_number_id, phone):
        raise Conflict(f"{phone} is already connected to this account.")
    data = payload.model_dump()
    data["phone_number"] = phone
    data["infobip_base_url"] = lines.normalise_base_url(data["infobip_base_url"])
    for key, value in list(data.items()):
        if isinstance(value, str):
            data[key] = value.strip()
    number = PhoneNumber(tenant_id=tenant.phone_number_id, **data)
    if not number.label:
        number.label = ("WhatsApp " if number.kind is NumberKind.WHATSAPP else "Phone ") + phone
    if number.kind is NumberKind.WHATSAPP and number.meta_phone_number_id:
        clash = await repo.by_meta_phone_number_id(number.meta_phone_number_id)
        if clash and clash.tenant_id != tenant.phone_number_id:
            raise Conflict("That WhatsApp number is already connected to another account.")
    await repo.save(number)
    log.info("tenant %s: added %s number %s", tenant.phone_number_id, number.kind.value, number.id)
    # Verified straight away: the person has the provider console open now,
    # and this is the moment a wrong value is easiest to fix.
    number = await lines.verify(tenant, number)
    return _out(number)


@router.patch("/{number_id}")
async def update_number(tenant: CurrentTenant, number_id: str, payload: NumberUpdate) -> dict:
    number = await _require(tenant, number_id)
    changes = payload.model_dump(exclude_unset=True)
    credentials_changed = False
    for field, value in changes.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        if field in SECRET_ATTRS and not value:
            continue  # the form never shows a secret back; blank means unchanged
        if field == "phone_number":
            value = to_e164(value, tenant)
        if field == "infobip_base_url":
            value = lines.normalise_base_url(value)
        if field == "use_platform_credentials" and value and not tenant.allow_platform_credentials:
            raise AppError(403, "This account is not allowed to use the platform's credentials.",
                           code="platform_credentials_not_allowed")
        if field in CREDENTIAL_FIELDS and getattr(number, field) != value:
            credentials_changed = True
        setattr(number, field, value)

    await _check_assignment(tenant, number)
    if credentials_changed:
        number.status = NumberStatus.PENDING
        number.status_detail = "Credentials changed — verify again."
        number.verified_at = None
    await repo.save(number)
    if credentials_changed:
        number = await lines.verify(tenant, number)
    return _out(number)


@router.post("/{number_id}/verify", dependencies=[Depends(check_expensive)])
async def verify_number(tenant: CurrentTenant, number_id: str) -> dict:
    number = await _require(tenant, number_id)
    return _out(await lines.verify(tenant, number))


@router.delete("/{number_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_number(tenant: CurrentTenant, number_id: str) -> None:
    await _require(tenant, number_id)
    await repo.delete(tenant.phone_number_id, number_id)
    log.info("tenant %s: removed number %s", tenant.phone_number_id, number_id)


async def _require(tenant: Tenant, number_id: str) -> PhoneNumber:
    number = await repo.get(tenant.phone_number_id, number_id)
    if number is None:
        raise NotFound("Number")
    return number


async def _check_assignment(tenant: Tenant, number: PhoneNumber) -> None:
    """An agent can only be put on a verified number, in a slot it fits."""
    for slot, agent_id, allowed, enabled in (
        ("inbound", number.inbound_agent_id, {AgentMode.INBOUND, AgentMode.BOTH}, number.takes_inbound),
        ("outbound", number.outbound_agent_id, {AgentMode.OUTBOUND, AgentMode.BOTH}, number.makes_outbound),
    ):
        if not agent_id:
            continue
        if not number.verified:
            raise AppError(409, "Verify this number before giving it an agent.",
                           code="number_not_verified")
        if not enabled:
            raise AppError(422, f"This number is not set up for {slot} calls. Change its mode first.",
                           code="mode_mismatch")
        agent = await agent_repo.get_agent(tenant.phone_number_id, agent_id)
        if agent is None:
            raise AppError(422, "That agent does not exist.", code="no_such_agent")
        if agent.mode not in allowed:
            raise AppError(
                422, f"{agent.name} is an {agent.mode.value}-only agent and cannot take {slot} calls.",
                code="agent_mode_mismatch",
            )


async def clear_agent(tenant_id: str, agent_id: str) -> None:
    """Called when an agent is deleted, so no number points at nothing."""
    for number in await repo.list_for(tenant_id):
        changed = False
        if number.inbound_agent_id == agent_id:
            number.inbound_agent_id = ""
            changed = True
        if number.outbound_agent_id == agent_id:
            number.outbound_agent_id = ""
            changed = True
        if changed:
            await repo.save(number)


async def pick_line(tenant: Tenant, line_id: str, *, whatsapp: bool) -> PhoneNumber | None:
    """The number an outbound call goes out on.

    Named explicitly, it must exist, be verified, allow outbound and be the
    right kind. Not named, the first number that qualifies. None only when the
    company has no numbers at all — the pre-numbers single-credential setup.
    """
    kind = NumberKind.WHATSAPP if whatsapp else NumberKind.SIM
    numbers = await repo.list_for(tenant.phone_number_id)
    if line_id:
        number = next((n for n in numbers if n.id == line_id), None)
        if number is None:
            raise NotFound("Number")
        if number.kind is not kind:
            raise AppError(422, f"{number.label} is a {number.kind.value} number.", code="wrong_kind")
        if not number.verified:
            raise AppError(409, f"{number.label} is not verified yet.", code="number_not_verified")
        if not number.makes_outbound:
            raise AppError(409, f"{number.label} is inbound-only.", code="inbound_only")
        return number
    fitting = [n for n in numbers if n.kind is kind and n.verified and n.makes_outbound]
    if fitting:
        return fitting[0]
    if any(n.kind is kind for n in numbers):
        raise AppError(
            409, "None of your " + ("WhatsApp" if whatsapp else "phone")
            + " numbers is verified and allowed to make outbound calls.",
            code="no_outbound_number",
        )
    return None
