"""
Outbound campaigns: a list of people, called one at a time.

Starting one places real calls to real people, so the checks before it starts
are the important part of this module — a campaign that begins and *then*
discovers it has no carrier credentials has already failed in front of a
customer.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, status

from ...errors import AppError, Conflict, NotFound
from ...models.call import Channel
from ...models.campaign import (
    Campaign,
    CampaignCreate,
    CampaignStatus,
    CampaignUpdate,
    Contact,
    ContactState,
)
from ...repositories import campaigns as campaign_repo
from ...security.rate_limit import check_expensive
from ...services import campaign_runner
from ...services.agent import whatsapp_media
from ...services.telephony import Infobip
from ...services.whatsapp import to_e164
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

MAX_CONTACTS = 2000


def _contacts_from(text: str, tenant) -> list[Contact]:
    """Parse and normalise, refusing the whole list if any number is unusable.

    Partial acceptance would mean a campaign that silently skips people, and
    the person who pasted the list would have no way to know which.
    """
    parsed = campaign_runner.parse_numbers(text)
    if len(parsed) > MAX_CONTACTS:
        raise AppError(
            413, f"That is more than {MAX_CONTACTS} numbers.", code="too_many"
        )
    contacts: list[Contact] = []
    seen: set[str] = set()
    problems: list[str] = []
    for number, name in parsed:
        try:
            normalised = to_e164(number, tenant)
        except AppError:
            problems.append(number)
            continue
        if normalised in seen:
            continue        # the same person twice is one call
        seen.add(normalised)
        contacts.append(Contact(number=normalised, name=name))
    if problems:
        raise AppError(
            422,
            f"{len(problems)} number(s) could not be read, starting with "
            f"{problems[0]!r}. Nothing was saved.",
            code="bad_numbers",
            details=problems[:10],
        )
    return contacts


@router.get("")
async def list_campaigns(tenant: CurrentTenant) -> dict:
    rows = await campaign_repo.listing(tenant.phone_number_id)
    return {
        "campaigns": [
            {**c.public(), "contacts": [], "live": campaign_runner.is_running(c.id)}
            for c in rows
        ]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_campaign(tenant: CurrentTenant, payload: CampaignCreate) -> dict:
    from .numbers import pick_line

    line = await pick_line(
        tenant, payload.line_id, whatsapp=payload.channel is Channel.WHATSAPP_CALL
    )
    campaign = Campaign(
        lineId=line.id if line else "",
        # The number's outbound agent unless the campaign names its own.
        agentId=payload.agent_id or (line.outbound_agent_id if line else ""),
        tenantId=tenant.phone_number_id,
        name=payload.name,
        channel=payload.channel,
        opening=payload.opening,
        gapSeconds=payload.gap_seconds,
        knowledgeBaseId=payload.knowledge_base_id,
        contacts=_contacts_from(payload.numbers, tenant),
    )
    await campaign_repo.save(campaign)
    log.info("campaign %s created with %d contacts", campaign.id, len(campaign.contacts))
    return campaign.public()


@router.get("/{campaign_id}")
async def get_campaign(tenant: CurrentTenant, campaign_id: str) -> dict:
    campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
    if campaign is None:
        raise NotFound("Campaign")
    return {**campaign.public(), "live": campaign_runner.is_running(campaign_id)}


@router.patch("/{campaign_id}")
async def update_campaign(
    tenant: CurrentTenant, campaign_id: str, payload: CampaignUpdate
) -> dict:
    campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
    if campaign is None:
        raise NotFound("Campaign")
    if campaign.status is CampaignStatus.RUNNING:
        raise Conflict("Pause the campaign before changing it.")

    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "numbers" in changes:
        # Replaces the list, but keeps what already happened to anyone still on
        # it — otherwise editing a name would call somebody a second time.
        previous = {c.number: c for c in campaign.contacts}
        fresh = _contacts_from(changes.pop("numbers"), tenant)
        campaign.contacts = [previous.get(c.number, c) for c in fresh]
    for field, value in changes.items():
        if hasattr(campaign, field):
            setattr(campaign, field, value)
    await campaign_repo.save(campaign)
    return campaign.public()


@router.post("/{campaign_id}/start", dependencies=[Depends(check_expensive)])
async def start_campaign(tenant: CurrentTenant, campaign_id: str) -> dict:
    """Begin calling. Everything that could stop it is checked first."""
    campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
    if campaign is None:
        raise NotFound("Campaign")
    if campaign.status is CampaignStatus.RUNNING:
        return {**campaign.public(), "live": campaign_runner.is_running(campaign_id)}
    if not campaign.contacts:
        raise Conflict("There is nobody on this list.")
    if campaign.next_waiting() is None:
        raise Conflict("Everybody on this list has already been called.")

    # Checked now rather than discovered on the first call, which would fail in
    # front of a customer and leave the campaign half-run.
    if campaign.channel is Channel.PHONE:
        if not Infobip(tenant).configured:
            raise AppError(
                409,
                "This company has no phone-line credentials, so it cannot place "
                "calls. Add them in Settings.",
                code="telephony_not_configured",
            )
    elif campaign.channel is Channel.WHATSAPP_CALL:
        if not whatsapp_media.available():
            raise AppError(
                503, "This deployment cannot place WhatsApp calls.", code="no_media_stack"
            )
        if not tenant.configured:
            raise AppError(
                409,
                "This company has not finished connecting WhatsApp.",
                code="tenant_not_configured",
            )

    campaign.status = CampaignStatus.RUNNING
    campaign.started_at = campaign.started_at or time.time()
    campaign.last_error = ""
    await campaign_repo.save(campaign)
    campaign_runner.start(tenant, campaign)
    log.info("campaign %s started", campaign_id)
    return {**campaign.public(), "live": True}


@router.post("/{campaign_id}/pause")
async def pause_campaign(tenant: CurrentTenant, campaign_id: str) -> dict:
    """Stop after the call in progress. Nobody is cut off mid-sentence."""
    campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
    if campaign is None:
        raise NotFound("Campaign")
    campaign.status = CampaignStatus.PAUSED
    if not campaign_runner.is_running(campaign_id):
        # Nothing is working this list, so a contact marked CALLING is left over
        # from a process that died mid-dial. Back in the queue, not lost.
        for contact in campaign.contacts:
            if contact.state is ContactState.CALLING:
                contact.state = ContactState.WAITING
    await campaign_repo.save(campaign)
    # Deliberately not cancelled when a runner is live: the runner reads the
    # status after the call in progress ends and stops there, so nobody is cut
    # off mid-sentence. Cancelling would also be able to interrupt a call
    # between the carrier accepting it and us recording its id.
    return {**campaign.public(), "live": campaign_runner.is_running(campaign_id)}


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
async def delete_campaign(tenant: CurrentTenant, campaign_id: str) -> None:
    campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
    if campaign is None:
        raise NotFound("Campaign")
    await campaign_runner.stop(campaign_id)
    await campaign_repo.delete(tenant.phone_number_id, campaign_id)
