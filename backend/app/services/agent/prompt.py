"""
The instructions an agent is given, in blocks a company can read and change.

All of this used to be written into `live.py` as one long literal. It worked,
but it was invisible: a company could see a greeting and a persona on screen
while a few hundred words of behaviour it had never agreed to — what the agent
may promise, when it hangs up, that it will not claim to be human — were
decided here and could not be read, let alone changed. Someone wanting a
stricter rule had no way to add one, and someone whose business needs a
different rule had no way to remove ours.

So each part of the prompt is a named block with a default. Leave a block
empty and the default is used, which is what every existing agent does and
why none of them changed. Write in it and yours is used instead. `extra` is
appended at the end for anything we never thought of, and `override` replaces
the assembled instructions entirely for someone who would rather write the
whole thing themselves.

## Why the order is what it is

The knowledge base runs to tens of thousands of characters, and rules placed
*before* it were followed about two times in five — the agent would say "I
have sent it on WhatsApp" and send nothing. Measured again with the rules
after it: five times in five. So the material goes in the middle and every
rule goes after it. The identity block is repeated at the end for the same
reason: a persona saying "call it the university, not by name" lost to the
name used throughout the documents, four times out of four, until it was
restated last.

Changing this order will quietly break tool calling. It is load-bearing.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# The blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    """One editable part of the prompt."""

    id: str
    #: What the company sees this called on screen.
    title: str
    #: One line saying what it is for, in the words of the thing it decides.
    help: str
    #: Used when the company has written nothing.
    default: str
    #: Only included when the agent can actually send a WhatsApp message.
    needs_messaging: bool = False


IDENTITY = Block(
    id="identity",
    title="What it says it is",
    help="How the agent answers “are you a real person?”. Callers ask, and the "
         "answer has to be the same every time.",
    default=(
        "You are a virtual assistant answering on this organisation's behalf. "
        "You are not a person, and you are not the organisation itself. If "
        "anyone asks who or what you are, say so plainly and warmly — never "
        "claim to be human, and never pretend the caller has reached a "
        "department or an individual."
    ),
)

SPEAKING = Block(
    id="speaking",
    title="How it speaks",
    help="This is a phone call, not a web page. These are the rules that keep "
         "it sounding like a person rather than a document being read out.",
    default=(
        "You are on a telephone call:\n"
        "- Speak in short, natural sentences, the way people actually talk.\n"
        "- Never read out punctuation, bullet points or formatting. There is "
        "nothing to look at.\n"
        "- Never say a URL or an email address aloud if you can send it instead.\n"
        "- One question at a time. Wait for the answer."
    ),
)

LISTENING = Block(
    id="listening",
    title="How it listens",
    help="What the agent does with what the caller actually said — the "
         "difference between a conversation and a recital.",
    default=(
        "Listen, and reply to what was actually said:\n"
        "- Acknowledge what they told you before you answer. If they gave you "
        "their name, use it.\n"
        "- Never repeat your opening line. You have already said it.\n"
        "- If you did not catch something, say so and ask them to repeat it, "
        "rather than guessing and answering the wrong question.\n"
        "- If they interrupt, stop and listen. What they are saying now matters "
        "more than what you were saying.\n"
        "- Be warm and unhurried. A caller should feel helped, not processed."
    ),
)

WRITING = Block(
    id="writing",
    title="Sending things in writing",
    help="Only used on a number that can send WhatsApp. Without these the "
         "agent says “I have sent it” and sends nothing.",
    needs_messaging=True,
    default=(
        "### Sending something in writing\n"
        "When they ask for anything in writing — a link, an address, a fee, a "
        "summary — you MUST call the send_whatsapp_message function. It is the "
        "only way a message is actually sent.\n"
        "- You already have the number they are calling from. Never ask for it, "
        "and never ask which number to use.\n"
        "- Never tell a caller you have sent something unless you called the "
        "function and it confirmed. Saying 'I have sent it' without calling it "
        "is a lie to someone who will go and look for it.\n"
        "- Call the function first, then tell them it is on its way."
    ),
)

LIMITS = Block(
    id="limits",
    title="What it must not do",
    help="The promises it is not allowed to make. Everything here is something "
         "an agent will otherwise cheerfully offer and cannot deliver.",
    default=(
        "### What you cannot do\n"
        "- You cannot transfer a call, put anyone through, or place them on "
        "hold. There is no switchboard behind you. Saying \"please hold, I will "
        "connect you\" leaves a real person waiting on a line where nothing will "
        "ever happen.\n"
        "- When they ask for a human: say plainly that you cannot put them "
        "through, take their question and their name, and tell them someone "
        "will call them back. Then end the call.\n"
        "- You cannot book, cancel, or change anything, and you cannot check the "
        "status of an individual application or account.\n"
        "- Promise nothing with a time on it — no \"within an hour\", no \"by "
        "tomorrow\" — unless the material you were given says so."
    ),
)

ENDING = Block(
    id="ending",
    title="When it hangs up",
    help="Without this the agent waits for the caller to go first, and a caller "
         "who has finished sits listening to silence.",
    default=(
        "### Ending the call\n"
        "- When their question is answered and they have nothing else, say a "
        "warm goodbye and call end_call in the same turn.\n"
        "- If the call is going nowhere — somebody testing you, saying the same "
        "thing over and over, being abusive, or silent after you have twice "
        "asked whether they are there — close it politely and call end_call.\n"
        "- Never use end_call to escape a question you cannot answer. Offer to "
        "have someone call them back instead."
    ),
)

#: In prompt order.
BLOCKS: tuple[Block, ...] = (IDENTITY, SPEAKING, LISTENING, WRITING, LIMITS, ENDING)
BY_ID = {block.id: block for block in BLOCKS}

#: Used when the company has written no persona at all.
FALLBACK_PERSONA = (
    "You are the voice assistant answering calls for this business. "
    "Be warm, brief and practical."
)

# Said as behaviour rather than a number, because the model has no dial. The
# effect is real: the same sentence at the wrong speed is the difference
# between being understood and being asked to repeat it.
PACE_RULES = {
    "slow": (
        "Speak slowly and leave a clear pause between sentences. Say numbers, "
        "dates and amounts one piece at a time."
    ),
    "natural": "Speak at a normal conversational pace.",
    "brisk": (
        "Speak briskly and keep answers tight. Do not pad, and do not repeat "
        "yourself."
    ),
}


def catalogue(*, can_send_whatsapp: bool = True) -> list[dict]:
    """Every block a company may edit, with the wording used when it does not.

    `can_send_whatsapp` false hides the writing block rather than offering a
    setting that has no effect on a number that cannot send messages.
    """
    return [
        {
            "id": block.id,
            "title": block.title,
            "help": block.help,
            "default": block.default,
            "needsMessaging": block.needs_messaging,
        }
        for block in BLOCKS
        if can_send_whatsapp or not block.needs_messaging
    ]


# ---------------------------------------------------------------------------
# Assembling one
# ---------------------------------------------------------------------------


def _chosen(written: dict[str, str], block: Block) -> str:
    """The company's words for this block, or ours."""
    return (written.get(block.id) or "").strip() or block.default


def build(
    *,
    persona: str,
    context: str,
    written: dict[str, str] | None = None,
    language_rule: str = "",
    pace: str = "",
    tone: str = "",
    escalation: str = "",
    extra: str = "",
    override: str = "",
    can_send_whatsapp: bool = False,
) -> str:
    """The whole instruction text for one call.

    `override` short-circuits everything: somebody who wants to write the
    entire prompt gets exactly what they wrote, with the material appended so
    they do not have to paste their own documents into it.
    """
    persona = (persona or "").strip() or FALLBACK_PERSONA
    written = written or {}

    if override.strip():
        parts = [override.strip()]
        if context:
            parts += ["", "## The material you answer from", "", context]
        return "\n".join(parts)

    # Who it is. First, because everything after is about how to behave.
    out = [persona, "", _chosen(written, IDENTITY)]

    if context:
        out += [
            "",
            "## The material you answer from",
            "Answer only from this. If the answer is not here, say you will "
            "check and have someone call back — never invent a figure, a date "
            "or a policy.",
            "",
            context,
        ]
    else:
        out += [
            "",
            "You have no reference material, so do not state specific facts "
            "about this business. Offer to take a message instead.",
        ]

    # Everything below comes after the material on purpose. See the module
    # docstring — this ordering is what makes tool calls actually happen.
    out += ["", "## How to behave on this call", ""]
    out += [_chosen(written, SPEAKING), "", _chosen(written, LISTENING)]

    if language_rule:
        out.append("- " + language_rule)
    if pace in PACE_RULES:
        out.append("- " + PACE_RULES[pace])

    if tone.strip():
        out += ["", "How this company wants you to sound:", tone.strip()]
    if escalation.strip():
        out += ["", "When to hand over or follow up:", escalation.strip()]

    if can_send_whatsapp:
        out += ["", _chosen(written, WRITING)]

    # The company's own words again, at the end. Identity suffers the same
    # burial as everything else placed before a large knowledge base.
    out += ["", "### Who you are, once more", persona]
    out += ["", _chosen(written, LIMITS)]
    out += ["", _chosen(written, ENDING)]

    if extra.strip():
        # Last, so it wins any disagreement with the blocks above — which is
        # what somebody writing their own rule is asking for.
        out += ["", "### Also, specifically", extra.strip()]

    return "\n".join(out)
