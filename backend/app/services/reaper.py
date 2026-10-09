"""
Settling calls that never reported how they ended.

A call row reaches a terminal state when the provider tells us it has. That
message can be lost: a webhook that never arrives, a deploy mid-call, a leg the
carrier forgot to report. The row then says `RINGING` or `IN_PROGRESS` for ever.

That is not cosmetic. WhatsApp refuses a second call to someone it believes is
already on one, so a single unsettled row can make a number unreachable — and
it did, for a whole afternoon, before this existed. An account was found with
eight open calls, the oldest twenty-seven days old.

The rule is deliberately conservative: a call is only settled when **this
process has no live session for it** and it is older than the grace period. A
real call in progress always has a session, so an honest conversation is never
cut short by this. What remains is rows nothing will ever update again.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from ..models.call import Call, CallStatus, RecordingState
from ..models.tenant import Tenant
from ..repositories import calls as call_repo
from ..repositories import tenants as tenant_repo

log = logging.getLogger(__name__)

# Longer than any call this platform places: campaigns cap their own calls, and
# the carrier drops a leg long before this. Anything still open past it is a
# row nobody is going to update.
GRACE_SECONDS = 2 * 60 * 60
# How often to look. Rare on purpose — this is a safety net, not a mechanism.
EVERY_SECONDS = 10 * 60

OPEN_STATUSES = {CallStatus.QUEUED, CallStatus.RINGING, CallStatus.IN_PROGRESS}


def _settle(call: Call, now: float) -> Call:
    """Give the row the ending the provider never reported."""
    call.ended_at = call.ended_at or now
    if call.answered_at:
        call.status = CallStatus.COMPLETED
        if not call.duration_seconds:
            call.duration_seconds = max(0, round(call.ended_at - call.answered_at))
    else:
        call.status = CallStatus.NO_ANSWER
    if call.recording_state is RecordingState.PENDING:
        # Audio that has not arrived in two hours is not going to.
        call.recording_state = RecordingState.ABSENT
    call.error = call.error or "the provider never reported how this call ended"
    return call


async def sweep_tenant(tenant: Tenant, *, grace: float = GRACE_SECONDS) -> int:
    """Settle one company's abandoned calls. Returns how many."""
    from .agent import live

    now = time.time()
    settled = 0
    try:
        calls = await call_repo.list_calls(tenant.phone_number_id, limit=500)
    except Exception:  # noqa: BLE001 — one unreadable company must not stop the rest
        log.exception("reaper: could not read calls for %s", tenant.phone_number_id)
        return 0

    for call in calls:
        if call.status not in OPEN_STATUSES:
            continue
        if now - (call.started_at or now) < grace:
            continue
        if live.get(call.id) is not None:
            # Audio is still moving through it. Not abandoned, just long.
            continue
        _settle(call, now)
        with contextlib.suppress(Exception):
            await call_repo.save_call(call)
            settled += 1
            log.warning(
                "reaper: settled call %s (%s, %.0f minutes old) as %s",
                call.id, call.channel.value, (now - call.started_at) / 60,
                call.status.value,
            )
    return settled


async def sweep(*, grace: float = GRACE_SECONDS) -> int:
    total = 0
    try:
        tenants = await tenant_repo.list_all()
    except Exception:  # noqa: BLE001
        log.exception("reaper: could not list companies")
        return 0
    for tenant in tenants:
        total += await sweep_tenant(tenant, grace=grace)
    return total


async def run_forever() -> None:
    """The background loop. Never raises; a failed sweep waits and retries."""
    while True:
        try:
            settled = await sweep()
            if settled:
                log.info("reaper: settled %d abandoned call(s)", settled)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("reaper: sweep failed")
        await asyncio.sleep(EVERY_SECONDS)
