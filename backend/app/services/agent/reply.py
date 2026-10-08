"""
Answering a WhatsApp message in the company's own voice.

The same persona the voice agent uses, answering in text instead of audio —
one place where a company describes its agent, not two that can disagree.

Two rules shape the prompt, and both come from what goes wrong without them:

* **Only from the company's material.** A model asked about fees will invent a
  plausible number if it has none. On a price list that is not a wrong answer,
  it is a quoted price the company then has to honour.
* **Written to be read on a phone.** No markdown, no headings, no bullet lists
  — WhatsApp renders none of it, so it arrives as asterisks and hashes.
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

_FALLBACK = (
    "You are the assistant answering WhatsApp messages for a business. "
    "Be brief, warm and practical."
)


def build_instructions(tenant: Tenant, knowledge: str) -> str:
    """The system prompt: who the agent is, and what it may say."""
    persona = (tenant.persona or "").strip() or _FALLBACK
    language = (tenant.language or "").strip()

    rules = [
        persona,
        "",
        "You are replying on WhatsApp, so write the way a person types on a "
        "phone: a few short sentences, no markdown, no bullet points, no "
        "headings, no asterisks. WhatsApp shows none of those and they arrive "
        "as punctuation.",
        f"Keep replies under {MAX_REPLY_CHARS} characters.",
    ]
    if language:
        rules.append(
            f"The business language is {language}. Otherwise reply in whatever "
            "language the person wrote in."
        )
    else:
        rules.append("Reply in whatever language the person wrote in.")

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

    try:
        response = await client.aio.models.generate_content(
            model=settings.gemini_text_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=build_instructions(tenant, knowledge),
                max_output_tokens=600,
                temperature=0.4,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a failed reply must not break the webhook
        log.warning("tenant %s: could not compose a reply (%s: %s)",
                    tenant.phone_number_id, type(exc).__name__, str(exc)[:160])
        return ""

    text = (getattr(response, "text", "") or "").strip()
    if len(text) > MAX_REPLY_CHARS:
        # Cut at a sentence rather than mid-word.
        cut = text.rfind(". ", 0, MAX_REPLY_CHARS)
        text = text[: cut + 1] if cut > MAX_REPLY_CHARS // 2 else text[:MAX_REPLY_CHARS]
    return text
