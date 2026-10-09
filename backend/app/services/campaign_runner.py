"""
Working through a campaign's list, one call at a time.

Deliberately the most conservative dialler possible:

* **One call at a time, per campaign.** No predictive pacing, no ratio
  dialling. Those exist to keep agents busy by calling more people than can be
  answered, and the cost is abandoned calls — someone picking up to silence.
  There is no agent waiting here, so there is nothing to optimise for.
* **A gap between calls**, with a floor. A list worked through at a human pace
  is the point.
* **No automatic redialling.** Someone who did not answer is left alone. A
  retry policy is a decision about how much a business may pester somebody, and
  that is not a default to pick for them.

The loop lives in this process, so a restart stops it. That is handled rather
than hidden: a campaign that was RUNNING is left RUNNING with its contact back
in WAITING, and resumes when somebody presses start again.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from ..models.call import Call, CallStatus, Channel, Direction, RecordingState
from ..models.campaign import Campaign, CampaignStatus, ContactState
from ..models.tenant import Tenant
from ..repositories import calls as call_repo
from ..repositories import campaigns as campaign_repo
from .telephony import Infobip
from .whatsapp import WhatsApp, to_e164

log = logging.getLogger(__name__)

# How long to let a call live before moving on. A call nobody answers rings
# out; one that connects ends on its own and this never fires.
MAX_CALL_SECONDS = 240

_runners: dict[str, asyncio.Task] = {}


def is_running(campaign_id: str) -> bool:
    task = _runners.get(campaign_id)
    return task is not None and not task.done()


def start(tenant: Tenant, campaign: Campaign) -> None:
    """Begin working through the list, if it is not already being worked."""
    if is_running(campaign.id):
        return
    _runners[campaign.id] = asyncio.create_task(_run(tenant, campaign.id))


async def resume_all() -> None:
    """Put every campaign marked RUNNING back to work after a restart.

    The loop lives in this process, so a deploy or a free-tier spin-down stops
    it — the campaign's row still says RUNNING, but nothing is calling anyone.
    Without this, that looks fixed (the contact goes back to WAITING on the
    next pause, per `_run`'s own docstring) but silently is not: nobody acts
    on WAITING until somebody happens to open the campaign and press start
    again. Called once at boot, across every tenant, so a restart is a blip
    rather than a campaign that quietly stopped.
    """
    from ..repositories import tenants as tenant_repo

    try:
        tenants = await tenant_repo.list_all()
    except Exception:  # noqa: BLE001 — boot must not fail because this listing did
        log.exception("could not list tenants to resume campaigns")
        return

    resumed = 0
    for tenant in tenants:
        try:
            for campaign in await campaign_repo.running(tenant.phone_number_id):
                start(tenant, campaign)
                resumed += 1
        except Exception:  # noqa: BLE001 — one tenant's bad row must not stop the rest
            log.exception(
                "could not resume campaigns for tenant %s", tenant.phone_number_id
            )
    if resumed:
        log.info("resumed %d campaign(s) after restart", resumed)


async def stop(campaign_id: str) -> None:
    task = _runners.pop(campaign_id, None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _run(tenant: Tenant, campaign_id: str) -> None:
    """One campaign, until it is paused, finished, or the process stops."""
    try:
        while True:
            campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
            if campaign is None or campaign.status is not CampaignStatus.RUNNING:
                return

            contact = campaign.next_waiting()
            if contact is None:
                campaign.status = CampaignStatus.DONE
                campaign.finished_at = time.time()
                await campaign_repo.save(campaign)
                log.info("campaign %s finished", campaign_id)
                return

            await _call_one(tenant, campaign, contact)
            # Re-read rather than reuse: somebody may have paused it while that
            # call was in progress, and carrying on would be ignoring them.
            await asyncio.sleep(max(5, campaign.gap_seconds))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a campaign failing must not take the process
        log.exception("campaign %s stopped unexpectedly", campaign_id)
        with contextlib.suppress(Exception):
            campaign = await campaign_repo.get(tenant.phone_number_id, campaign_id)
            if campaign is not None:
                campaign.status = CampaignStatus.PAUSED
                campaign.last_error = "the campaign stopped unexpectedly"
                await campaign_repo.save(campaign)
    finally:
        _runners.pop(campaign_id, None)


async def _update_contact(tenant: Tenant, campaign_id: str, number: str, **changes) -> None:
    """Change one contact on the *current* copy of the campaign.

    Re-read each time rather than saving the copy the loop started with. That
    copy is seconds or minutes old by the time a call ends, and writing it back
    would undo whatever somebody did in between — most visibly, un-pausing a
    campaign they had just paused.
    """
    fresh = await campaign_repo.get(tenant.phone_number_id, campaign_id)
    if fresh is None:
        return
    for contact in fresh.contacts:
        if contact.number == number:
            for field, value in changes.items():
                setattr(contact, field, value)
            break
    await campaign_repo.save(fresh)


async def _hang_up(tenant: Tenant, call: Call) -> None:
    """End a call this process placed. Best effort, and never raises."""
    if not call.provider_call_id:
        return
    with contextlib.suppress(Exception):
        if call.channel is Channel.PHONE:
            await Infobip(tenant).hangup(call.provider_call_id)
        else:
            await WhatsApp(tenant).terminate_call(call.provider_call_id)
        log.info("campaign call %s hung up", call.id)


async def _dial(tenant: Tenant, campaign: Campaign, call: Call, destination: str) -> None:
    """Ask the carrier to place the call, and record what it answered.

    Its own function so it can be shielded: once the carrier has accepted a
    call, recording its id is not optional. A call that is live at the carrier
    and unknown here cannot be hung up, which is the failure this exists to
    prevent.
    """
    if campaign.channel is Channel.PHONE:
        result = await Infobip(tenant).place_call(destination)
        call.provider_call_id = str(result.get("id") or result.get("callId") or "")
    else:
        from .agent import whatsapp_media

        if not whatsapp_media.available():
            raise RuntimeError("this deployment cannot place WhatsApp calls")
        bridge = whatsapp_media.WhatsAppBridge(call.id, tenant)
        whatsapp_media.register(call.id, bridge)
        result = await WhatsApp(tenant).place_call(destination, await bridge.offer())
        call.provider_call_id = str(
            (result.get("calls") or [{}])[0].get("id") or result.get("id") or ""
        )
    call.status = CallStatus.RINGING
    await call_repo.save_call(call)


async def _call_one(tenant: Tenant, campaign: Campaign, contact) -> None:
    """Place one call and wait for it to finish."""
    from ..repositories import numbers as number_repo

    line = (
        await number_repo.get(tenant.phone_number_id, campaign.line_id)
        if campaign.line_id else None
    )
    if line is not None:
        # Same company, speaking with this number's credentials.
        tenant = number_repo.as_tenant(tenant, line)
    await _update_contact(tenant, campaign.id, contact.number,
                          state=ContactState.CALLING, attempts=contact.attempts + 1)

    call = Call(
        tenantId=tenant.phone_number_id,
        channel=campaign.channel,
        direction=Direction.OUTBOUND,
        status=CallStatus.QUEUED,
        counterparty=contact.number,
        fromNumber=tenant.infobip_phone_number or tenant.display_phone_number,
        recordingState=RecordingState.PENDING if tenant.record_calls else RecordingState.NONE,
        # The campaign's own agent and knowledge, so a list worked from the
        # price list is not answered from the support handbook.
        agentId=campaign.agent_id,
        knowledgeBaseId=campaign.knowledge_base_id,
        lineId=campaign.line_id,
        metadata={"campaignId": campaign.id, "campaignName": campaign.name},
    )
    await call_repo.save_call(call)
    await _update_contact(tenant, campaign.id, contact.number, call_id=call.id)

    dial = asyncio.ensure_future(
        _dial(tenant, campaign, call, to_e164(contact.number, tenant))
    )
    try:
        # Shielded: if this task is cancelled mid-dial, the dial still finishes
        # and records the carrier's call id, so there is something to hang up.
        await asyncio.shield(dial)
        log.info("campaign %s: calling …%s", campaign.id, contact.number[-4:])
    except asyncio.CancelledError:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await dial
        await _hang_up(tenant, call)
        call.status = CallStatus.FAILED
        call.error = "the campaign was stopped while this call was being placed"
        call.ended_at = time.time()
        with contextlib.suppress(Exception):
            await call_repo.save_call(call)
        await _update_contact(tenant, campaign.id, contact.number,
                              state=ContactState.WAITING, note="stopped while dialling")
        raise
    except Exception as exc:  # noqa: BLE001 — one bad number is not a failed campaign
        call.status = CallStatus.FAILED
        call.error = str(exc)[:200]
        call.ended_at = time.time()
        await call_repo.save_call(call)
        await _update_contact(tenant, campaign.id, contact.number,
                              state=ContactState.FAILED, note=str(exc)[:160])
        return

    # Wait for the call to reach an end state. The webhooks settle the row; all
    # this does is notice. If this task is cancelled while waiting, nobody is
    # supervising the call any more, so it is ended rather than left running.
    final_state, note = ContactState.FAILED, "the call did not finish in time"
    deadline = time.time() + MAX_CALL_SECONDS
    try:
        while time.time() < deadline:
            await asyncio.sleep(5)
            current = await call_repo.get_call(tenant.phone_number_id, call.id)
            if current is None:
                break
            if current.status in {CallStatus.COMPLETED, CallStatus.FAILED,
                                  CallStatus.NO_ANSWER, CallStatus.HANDED_OFF}:
                final_state = (
                    ContactState.DONE
                    if current.status in {CallStatus.COMPLETED, CallStatus.HANDED_OFF}
                    else ContactState.FAILED
                )
                note = current.error or ""
                break
    except asyncio.CancelledError:
        await _hang_up(tenant, call)
        await _update_contact(tenant, campaign.id, contact.number,
                              state=ContactState.WAITING, note="stopped during the call")
        raise

    await _update_contact(tenant, campaign.id, contact.number, state=final_state, note=note)


def parse_numbers(text: str) -> list[tuple[str, str]]:
    """One contact per line: `number` or `number, name`.

    Tolerant on purpose — this is pasted from a spreadsheet, and a trailing
    comma or a quoted name should not cost somebody their list.
    """
    contacts: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip().strip('"').strip()
        if not line:
            continue
        number, _, name = line.partition(",")
        number = number.strip().strip('"')
        if not number:
            continue
        contacts.append((number, name.strip().strip('"')))
    return contacts
