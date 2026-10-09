"""
Questions nobody could answer.

Read out of the calls and messages that already exist rather than written to a
table of their own. That keeps one source of truth — delete a call and its gaps
go with it — and it means this screen started working the moment the agent did,
with no migration and no backfill.
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Query

from ...models.call import MessageDirection
from ...repositories import calls as call_repo
from ...services.agent import gaps as gap_finder
from ..deps import CurrentTenant

router = APIRouter(prefix="/gaps", tags=["gaps"])

SAMPLE = 500


@router.get("")
async def unanswered(tenant: CurrentTenant, limit: int = Query(50, ge=1, le=200)) -> dict:
    """What people asked that the agent had to defer.

    Each one is a question a customer actually asked and the business has no
    published answer to — which makes this the most direct list of what to
    upload next.
    """
    found: list[dict[str, object]] = []

    calls = await call_repo.list_calls(tenant.phone_number_id, limit=SAMPLE)
    for call in calls:
        for gap in gap_finder.find_in_transcript(call.transcript):
            found.append(
                {
                    "question": gap["question"],
                    "reply": gap["reply"],
                    "source": "call",
                    "sourceId": call.id,
                    "counterparty": call.counterparty,
                    "at": call.started_at,
                }
            )

    # Messages are a thread, so a deferral is paired with the inbound message
    # before it rather than with a line of the same transcript.
    messages = await call_repo.list_messages(tenant.phone_number_id, limit=SAMPLE)
    by_person: dict[str, list] = {}
    for message in messages:
        by_person.setdefault(message.counterparty, []).append(message)
    for counterparty, thread in by_person.items():
        thread.sort(key=lambda m: m.created_at)
        for i, message in enumerate(thread):
            if message.direction is not MessageDirection.OUTBOUND:
                continue
            if not gap_finder.looks_deferred(message.body):
                continue
            asked = next(
                (
                    thread[j].body
                    for j in range(i - 1, -1, -1)
                    if thread[j].direction is MessageDirection.INBOUND
                ),
                "",
            )
            if asked:
                found.append(
                    {
                        "question": gap_finder.question_from(asked),
                        "reply": message.body,
                        "source": "message",
                        "sourceId": message.id,
                        "counterparty": counterparty,
                        "at": message.created_at,
                    }
                )

    found.sort(key=lambda g: g["at"], reverse=True)

    # The same question asked twenty times is one thing to fix, not twenty.
    repeated = Counter(str(g["question"]).lower().strip() for g in found)
    for gap in found:
        gap["askedTimes"] = repeated[str(gap["question"]).lower().strip()]

    return {
        "gaps": found[:limit],
        "total": len(found),
        "callsExamined": len(calls),
        "mostAsked": [
            {"question": question, "times": times}
            for question, times in repeated.most_common(5)
            if times > 1
        ],
    }
