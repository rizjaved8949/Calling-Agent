"""
Answering a WhatsApp message in the company's own voice.

The agent that answers the number answers the message — same persona, same
rules, in text instead of audio. It used to read the company's tenant-level
persona instead, which meant the agent chosen for messages contributed its
knowledge base and nothing else: the Numbers page promised "this message is
answered by X" and then something else answered.

Three rules shape the prompt, and each comes from what goes wrong without it:

* **Only from the company's material.** A model asked about fees will invent a
  plausible number if it has none. On a price list that is not a wrong answer,
  it is a quoted price the company then has to honour.
* **Written to be read on a phone.** No markdown, no headings, no bullet lists
  — WhatsApp renders none of it, so it arrives as asterisks and hashes.
* **Read, not spoken.** The persona is shared with the voice agent, which is
  told to say figures as words so a phone line does not garble them. In
  writing that produces "ninety-eight percent" where a reader expects 98%.

## Thinking tokens

`gemini-3.5-flash` reasons before it answers and those tokens come out of
`max_output_tokens`. Measured on a real reply with the 66k-character
university knowledge base: 573 of a 600 budget went on thinking, leaving 23
for the answer, which arrived cut mid-sentence — "you may be eligible for an
estimated" — and was sent to the customer like that. Thinking is switched off
here and the ceiling raised; a reply grounded in material to quote from is
not a reasoning problem. If the model refuses the setting the call is retried
without it, and a reply that still runs out of room is cut back to its last
complete sentence rather than sent half-written.
"""
from __future__ import annotations

import logging

from ...config import settings
from ...models.tenant import Tenant

log = logging.getLogger(__name__)

# Enough for a useful answer, short enough to read on a phone. Meta's own cap
# is 4096, but an agent that writes four thousand characters is not answering,
# it is reciting.
MAX_REPLY_CHARS = 900

# Thinking tokens come out of this budget. With it at 600 and thinking on,
# 573 went on reasoning and 23 on the answer, which arrived cut mid-sentence.
# Raised as well as switching thinking off, so a long answer still has room.
MAX_OUTPUT_TOKENS = 1400

_FALLBACK = (
    "You are the assistant answering WhatsApp messages for a business. "
    "Be brief, warm and practical."
)


def build_instructions(
    tenant: Tenant, knowledge: str, voice: dict | None = None
) -> str:
    """The system prompt: who the agent is, and what it may say.

    `voice` is what `routing.persona_for` resolved for this number — the
    agent's own persona, tone and rules. Without it the company's tenant-level
    settings are used, which is what a company that never built an agent has.
    """
    voice = voice or {}
    persona = (str(voice.get("persona") or "").strip()
               or (tenant.persona or "").strip()
               or _FALLBACK)
    language = (str(voice.get("language") or "").strip()
                or (tenant.language or "").strip())
    tone = str(voice.get("tone") or "").strip()
    escalation = str(voice.get("escalation") or "").strip()
    extra = str(voice.get("extraRules") or "").strip()

    rules = [
        persona,
        "",
        "You are replying on WhatsApp, so write the way a person types on a "
        "phone: a few short sentences, no markdown, no bullet points, no "
        "headings, no asterisks. WhatsApp shows none of those and they arrive "
        "as punctuation.",
        # The persona is shared with the voice agent, which is told to say
        # figures as words so a phone line does not garble them. Written down
        # that gives "ninety-eight percent" where a reader expects 98%.
        "This is read, not spoken. Write numbers, prices, percentages and "
        "dates as digits — 98%, Rs 120,000, 15 August — never spelled out as "
        "words.",
        "Finish what you start. A reply that stops mid-sentence is worse than "
        "a shorter one, so keep it short enough to complete.",
        f"Keep replies under {MAX_REPLY_CHARS} characters.",
    ]
    if language:
        rules.append(
            f"The business language is {language}. Otherwise reply in whatever "
            "language the person wrote in."
        )
    else:
        rules.append("Reply in whatever language the person wrote in.")

    if tone:
        rules += ["", "How this company wants you to sound:", tone]
    if escalation:
        rules += ["", "When to hand over or follow up:", escalation]
    if extra:
        rules += ["", "Also, specifically:", extra]

    if knowledge.strip():
        rules += [
            "",
            "Answer only from the material below. If the answer is not in it, "
            "say plainly that you will check and have someone follow up — do "
            "not guess. A number you invent is a price the business has to "
            "honour.",
            "",
            knowledge,
        ]
    else:
        rules += [
            "",
            "You have no reference material, so do not state specific facts "
            "about this business — no prices, dates, addresses or policies. "
            "Offer to have someone follow up instead.",
        ]
    return "\n".join(rules)


def available() -> bool:
    try:
        import google.genai  # noqa: F401
    except ImportError:
        return False
    return bool(settings.gemini_api_key.strip())


async def compose(
    tenant: Tenant,
    question: str,
    knowledge: str,
    history: list[tuple[str, str]] | None = None,
    voice: dict | None = None,
) -> str:
    """The agent's reply, or an empty string if it has nothing to say.

    Empty rather than an apology: a message that adds nothing is worse than
    silence, because the person then waits for a real answer that is not
    coming.
    """
    if not available() or not question.strip():
        return ""

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key.strip())

    contents = []
    for who, text in (history or [])[-8:]:
        contents.append(
            types.Content(
                role="user" if who == "them" else "model",
                parts=[types.Part(text=text)],
            )
        )
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

    instructions = build_instructions(tenant, knowledge, voice)

    async def generate(thinking: bool):
        config: dict = {
            "system_instruction": instructions,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0.4,
        }
        if not thinking:
            config["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return await client.aio.models.generate_content(
            model=settings.gemini_text_model,
            contents=contents,
            config=types.GenerateContentConfig(**config),
        )

    try:
        response = await generate(thinking=False)
    except Exception as exc:  # noqa: BLE001
        # A model that will not take the setting must still answer. Retried
        # once with thinking left on rather than leaving the person unanswered.
        log.info("tenant %s: retrying the reply with thinking on (%s)",
                 tenant.phone_number_id, type(exc).__name__)
        try:
            response = await generate(thinking=True)
        except Exception as second:  # noqa: BLE001 — a failed reply must not break the webhook
            log.warning("tenant %s: could not compose a reply (%s: %s)",
                        tenant.phone_number_id, type(second).__name__, str(second)[:160])
            return ""

    text = (getattr(response, "text", "") or "").strip()
    if _ran_out_of_room(response):
        log.warning("tenant %s: the reply hit the token ceiling and was trimmed",
                    tenant.phone_number_id)
        text = _last_whole_sentence(text)
    if len(text) > MAX_REPLY_CHARS:
        text = _trim_to(text, MAX_REPLY_CHARS)
    return text


def _ran_out_of_room(response) -> bool:
    """Whether the model was cut off rather than finishing.

    A reply that stops mid-sentence is sent to a customer who then asks
    "estimated what?" — which is exactly what happened before this was here.
    """
    for candidate in getattr(response, "candidates", None) or []:
        if str(getattr(candidate, "finish_reason", "")).upper().endswith("MAX_TOKENS"):
            return True
    return False


def _last_whole_sentence(text: str) -> str:
    """Everything up to the last sentence that actually finished."""
    cut = max(text.rfind(". "), text.rfind("? "), text.rfind("! "),
              text.rfind("."), text.rfind("?"), text.rfind("!"))
    if cut <= 0:
        # Nothing finished. Better to say nothing than half a thought: the
        # caller treats "" as "the agent had nothing to say".
        return ""
    return text[: cut + 1].strip()


def _trim_to(text: str, limit: int) -> str:
    """Cut at a sentence rather than mid-word."""
    cut = text.rfind(". ", 0, limit)
    return text[: cut + 1] if cut > limit // 2 else text[:limit]


_UNKNOWN_MARK = "NOT-IN-MATERIAL"


async def ask(tenant: Tenant, question: str, knowledge: str) -> dict:
    """What a staff member looking something up mid-call gets.

    The same discipline as a WhatsApp reply — answer only from the material,
    say plainly when it isn't there — but asked for directly rather than
    folded into a conversational reply, and without the "write like a text
    message" instructions that belong to that other job, not this one.

    Returns `{"answered": bool, "text": str}` rather than just a string: a
    person reading this mid-call needs to know instantly whether what
    follows is grounded or an admission, and that is an easy thing to miss
    in a sentence but not in a flag the UI can colour differently.
    """
    if not available() or not question.strip():
        return {"answered": False, "text": ""}
    if not knowledge.strip():
        return {
            "answered": False,
            "text": "There is no material to check yet for this knowledge base.",
        }

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key.strip())
    instructions = (
        "You help a staff member look up facts from the material below, mid-call "
        "or mid-conversation with a customer. Answer in one or two short, plain "
        f"sentences — no markdown. If the material does not contain the answer, "
        f"reply with exactly the single word {_UNKNOWN_MARK} and nothing else; do "
        "not guess, approximate or invent a figure, date or policy.\n\n"
        + knowledge
    )
    try:
        response = await client.aio.models.generate_content(
            model=settings.gemini_text_model,
            contents=[types.Content(role="user", parts=[types.Part(text=question)])],
            config=types.GenerateContentConfig(
                system_instruction=instructions, max_output_tokens=300, temperature=0.1,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — one failed lookup must not break the page
        log.warning("tenant %s: ask failed (%s: %s)",
                    tenant.phone_number_id, type(exc).__name__, str(exc)[:160])
        return {"answered": False, "text": "Could not check that right now. Try again."}

    text = (getattr(response, "text", "") or "").strip()
    if not text or _UNKNOWN_MARK in text:
        return {
            "answered": False,
            "text": "That isn't in this knowledge base. Tell the caller you will "
                    "check and have someone follow up.",
        }
    return {"answered": True, "text": text}
