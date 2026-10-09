"""
Questions the agent could not answer.

When someone asks about something the company has not uploaded, the agent says
it will check and have somebody follow up. That is the right answer, and it is
also a fact worth keeping: it is a question a customer actually asked and the
business has no published answer to.

Collected from what the agent *said*, not from what it was asked. A question
the agent answered correctly is not a gap, and only the reply distinguishes
them — "the fees are 120,000" and "I will check on that" are both responses to
a question about fees.

Stored on the call or message row rather than in a table of its own: a gap
without the conversation around it is a sentence with no context, and nobody
can act on it.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Phrases the prompt tells the agent to use when it has no answer. Matching the
# instruction rather than guessing at intent — these are the exact forms the
# system prompt asks for, in `reply.py` and `live.py`.
_DEFERRALS = (
    "will check",
    "i'll check",
    "have someone follow up",
    "have someone call",
    "someone will get back",
    "take a message",
    "do not have that",
    "don't have that",
    "cannot find that",
    "can't find that",
)

# The same, for the languages this is actually used in. Roman Urdu is how the
# model writes Urdu in a transcript, so both spellings appear.
_DEFERRALS_UR = (
    "check kar",
    "maloom kar",
    "pata kar",
    "rabta kar",
    "معلوم کر",
    "رابطہ کر",
)


def looks_deferred(reply: str) -> bool:
    """Did the agent decline to answer rather than answer?"""
    if not reply:
        return False
    lowered = reply.lower()
    return any(p in lowered for p in _DEFERRALS) or any(
        p in lowered for p in _DEFERRALS_UR
    )


def question_from(text: str) -> str:
    """The part of what was said that reads like the question.

    The last sentence ending in a question mark, or the whole thing if there is
    none — people on the phone rarely punctuate, and a transcript never does.
    """
    text = (text or "").strip()
    if not text:
        return ""
    questions = re.findall(r"[^.!?؟۔]*[?؟]", text)
    if questions:
        return questions[-1].strip()
    return text if len(text) <= 300 else text[-300:].strip()


def find_in_transcript(transcript: str) -> list[dict[str, str]]:
    """Every point in a call where the agent deferred, with what preceded it.

    Pairs each deferral with the caller's previous line, because the deferral
    alone says nothing about what was wanted.
    """
    gaps: list[dict[str, str]] = []
    last_caller = ""
    for line in (transcript or "").splitlines():
        who, _, said = line.partition(":")
        said = said.strip()
        if not said:
            continue
        if who.strip() == "caller":
            last_caller = said
        elif who.strip() == "agent" and looks_deferred(said) and last_caller:
            gaps.append({"question": question_from(last_caller), "reply": said})
            last_caller = ""
    return gaps
