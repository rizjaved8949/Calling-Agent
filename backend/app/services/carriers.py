"""
Which carrier a phone call goes through, decided in one place.

A phone line is either Infobip or Twilio. Most of the code that touches a
call — the dialer, campaigns, the hang-up button, the agent ending a call —
does not care which: it wants the call placed or ended. It holds a tenant
overlay (`repositories/numbers.as_tenant`), and the overlay's `carrier` field
says which provider that number is on. This module reads it so nothing else
has to.

Blank `carrier` means Infobip. That is the company row itself, and every phone
line that existed before Twilio was added, so the old path stays the default.

What stays carrier-specific, and deliberately not here: Infobip's websocket
configurations, dialogs and recording downloads, which have no Twilio
equivalent. Those are only ever reached from Infobip's own webhooks.
"""
from __future__ import annotations

import asyncio
import time

from ..errors import AppError, UpstreamError
from ..models.call import Call
from ..models.tenant import Tenant
from .telephony import Infobip
from .twilio_client import Twilio

TWILIO = "twilio"


def is_twilio(tenant: Tenant) -> bool:
    return tenant.carrier == TWILIO


def carrier_for(tenant: Tenant) -> Infobip | Twilio:
    """The client for whichever carrier this tenant overlay's line is on."""
    return Twilio(tenant) if is_twilio(tenant) else Infobip(tenant)


def twilio_urls(number_id: str, call_id: str) -> tuple[str, str]:
    """(answer URL, status URL) for an outbound Twilio call.

    The answer URL names the call so the webhook needs no lookup by Twilio's
    id to know which of our calls just picked up; the status URL names only
    the number, and is the same one verification sets on the number itself.
    """
    from .lines import webhook_base

    base = webhook_base()
    if not base.startswith("https://"):
        # Twilio can fetch plain http, but the audio socket it is then sent to
        # would be ws://, which it refuses: the call would connect and say
        # nothing. Better refused here, before anyone's phone rings.
        raise AppError(
            503,
            "PUBLIC_BASE_URL must be the server's https:// address before a Twilio "
            "call can be placed, so Twilio can reach it.",
            code="no_public_base_url",
        )
    hooks = f"{base}/api/webhooks/twilio"
    return f"{hooks}/outbound/{number_id}/{call_id}", f"{hooks}/status/{number_id}"


#: The least time between two calls placed on one Twilio account. Twilio's
#: default is one new outbound call per second per account ("CPS"); faster
#: than that and it queues them itself, then refuses with 20429 once its queue
#: is full. Spaced here instead, so a campaign and the dialer on the same
#: account, or two campaigns, take turns rather than tripping it.
TWILIO_CALL_SPACING_SECONDS = 1.0

_twilio_last: dict[str, float] = {}
_twilio_locks: dict[str, asyncio.Lock] = {}


async def _twilio_turn(account_sid: str) -> None:
    """Wait until this account may place another call, then claim the slot.

    Per account, not per number: the limit is Twilio's per account, and
    several numbers can share one. Per process too, which is all there is:
    one web service places every call.
    """
    lock = _twilio_locks.setdefault(account_sid, asyncio.Lock())
    async with lock:
        wait = _twilio_last.get(account_sid, 0.0) + TWILIO_CALL_SPACING_SECONDS - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _twilio_last[account_sid] = time.monotonic()


#: What the Numbers screen says when Infobip refused the key on a call.
INFOBIP_KEY_REJECTED = "Infobip rejected the API key. Update it on Numbers and press Verify."


async def _line_must_be_verified(tenant: Tenant, call: Call) -> None:
    """Refuse to dial on a line the provider has not accepted.

    The dialer checks this when a line is chosen; a campaign did not, and a
    line that failed verification yesterday would still be dialled today.
    One chokepoint for every path, at the cost of one read per call.
    """
    from ..repositories import numbers as number_repo

    line_id = tenant.line_id or call.line_id
    if not line_id:
        return
    number = await number_repo.get_safe(tenant.phone_number_id, line_id)
    if number is not None and not number.verified:
        raise AppError(
            409,
            f"{number.label or number.phone_number} is not verified"
            f"{': ' + number.status_detail if number.status_detail else ''}. "
            "Press Verify on the Numbers page before calling from it.",
            code="number_not_verified",
        )


async def _mark_line_failed(tenant: Tenant, call: Call, detail: str) -> None:
    """Record on the line that its credentials were refused. Never raises."""
    from ..repositories import numbers as number_repo
    from ..models.number import NumberStatus

    line_id = tenant.line_id or call.line_id
    if not line_id:
        return
    try:
        number = await number_repo.get(tenant.phone_number_id, line_id)
        if number is None:
            return
        number.status = NumberStatus.FAILED
        number.status_detail = detail
        number.verified_at = None
        await number_repo.save(number)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception(
            "number %s: could not mark it failed after the carrier refused its key", line_id)


async def place_call(tenant: Tenant, call: Call, destination: str) -> dict:
    """Ring `destination` for `call`, and record the carrier's call id on it.

    One function rather than `carrier_for(tenant).place_call(...)` because the
    two carriers need different things: Infobip only the number, Twilio also
    the addresses it should come back to, which are built from this call.
    The call row is updated in memory; saving it is the caller's job, as it
    was before. The carrier's raw answer is returned for anything else the
    caller wants from it (the dialer keeps Infobip's dialog id).

    A line the provider has not verified is refused before anything is
    dialled, and a key Infobip rejects (a 401 on the call itself) marks the
    line failed with what to do, instead of a generic 502 that said nothing.
    """
    await _line_must_be_verified(tenant, call)
    carrier = carrier_for(tenant)
    if is_twilio(tenant):
        number_id = tenant.line_id or call.line_id
        if not number_id:
            raise AppError(409, "This call has no Twilio line to go out on.",
                           code="no_line")
        answer_url, status_url = twilio_urls(number_id, call.id)
        await _twilio_turn(tenant.twilio_account_sid)
        result = await carrier.place_call(
            destination, from_number=call.from_number,
            answer_url=answer_url, status_url=status_url,
        )
        call.provider_call_id = str(result.get("sid") or "")
        return result
    try:
        result = await carrier.place_call(destination)
    except UpstreamError as exc:
        if exc.http_status in (401, 403):
            await _mark_line_failed(tenant, call, INFOBIP_KEY_REJECTED)
            raise AppError(409, INFOBIP_KEY_REJECTED, code="infobip_auth") from exc
        raise
    call.provider_call_id = str(result.get("id") or result.get("callId") or "")
    return result


async def hang_up(tenant: Tenant, provider_call_id: str) -> None:
    """End a phone call with whichever carrier is carrying it. May raise."""
    await carrier_for(tenant).hangup(provider_call_id)
