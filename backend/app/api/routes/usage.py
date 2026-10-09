"""
What a company has actually used.

Counted from the call and message logs rather than from a meter, because a
separate meter is a second source of truth that drifts from the first. The
answer to "how many minutes did we use" should be the same rows the history
screen shows, added up.

No billing attached. These are the numbers a plan would be measured against,
and they are worth showing before anyone is charged for them — a company that
cannot see its usage has no way to question an invoice.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from ...models.call import CallStatus, MessageDirection, RecordingState
from ...repositories import calls as call_repo
from ...repositories import knowledge
from ..deps import CurrentTenant

router = APIRouter(prefix="/usage", tags=["usage"])

# A sample big enough for a year of ordinary traffic. Beyond it the summary
# would need aggregating in the database, which is a different job.
SAMPLE = 5000


def _month(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m")


@router.get("")
async def usage(tenant: CurrentTenant, months: int = Query(6, ge=1, le=24)) -> dict:
    """Calls, minutes, messages and stored audio, this period and by month."""
    calls = await call_repo.list_calls(tenant.phone_number_id, limit=SAMPLE)
    messages = await call_repo.list_messages(tenant.phone_number_id, limit=SAMPLE)
    documents = await knowledge.listing(tenant.phone_number_id)

    now = time.time()
    this_month = _month(now)

    by_month: dict[str, dict[str, float]] = defaultdict(
        lambda: {"calls": 0, "seconds": 0, "recorded": 0, "messagesIn": 0, "messagesOut": 0}
    )
    answered = 0
    for call in calls:
        bucket = by_month[_month(call.started_at)]
        bucket["calls"] += 1
        bucket["seconds"] += call.duration_seconds
        if call.recording_state is RecordingState.READY:
            bucket["recorded"] += 1
        if call.answered_at:
            answered += 1
    for message in messages:
        bucket = by_month[_month(message.created_at)]
        key = "messagesIn" if message.direction is MessageDirection.INBOUND else "messagesOut"
        bucket[key] += 1

    ordered = sorted(by_month.items(), reverse=True)[:months]
    series = [
        {
            "month": month,
            "calls": int(v["calls"]),
            # Rounded up: a 20-second call is a minute to anyone billing it,
            # and showing 0.3 invites an argument later.
            "minutes": int(-(-v["seconds"] // 60)),
            "recorded": int(v["recorded"]),
            "messagesIn": int(v["messagesIn"]),
            "messagesOut": int(v["messagesOut"]),
        }
        for month, v in ordered
    ]

    current = next((row for row in series if row["month"] == this_month), None)
    stored_bytes = sum(c.recording_bytes for c in calls if c.recording_bytes)

    return {
        "thisMonth": current or {
            "month": this_month, "calls": 0, "minutes": 0,
            "recorded": 0, "messagesIn": 0, "messagesOut": 0,
        },
        "byMonth": series,
        "allTime": {
            "calls": len(calls),
            "answered": answered,
            "minutes": int(-(-sum(c.duration_seconds for c in calls) // 60)),
            "messages": len(messages),
            "recordings": sum(
                1 for c in calls if c.recording_state is RecordingState.READY
            ),
            "failed": sum(1 for c in calls if c.status is CallStatus.FAILED),
        },
        "storage": {
            # Where it lives matters more than the number: audio in the
            # company's own Drive is their quota, not ours.
            "recordingBytes": stored_bytes,
            "inGoogleDrive": tenant.google_drive.connected,
            "knowledgeDocuments": len(documents),
            "knowledgeChars": sum(d["chars"] for d in documents),
        },
        "sampled": len(calls) >= SAMPLE,
    }
