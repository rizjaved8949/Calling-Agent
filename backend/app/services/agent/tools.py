"""
The things the agent can *do* on a call, rather than say.

Two, deliberately. Each one is a promise to the caller that something will
happen after they hang up, so each is only worth declaring if it genuinely
happens — a tool the model can call and we cannot deliver is worse than no
tool, because the caller is told it is done.

* **send_whatsapp_message** — the caller asks for something in writing. The
  number defaults to the one on the call, because asking somebody to recite
  the number they are calling from is the single most irritating thing a
  phone agent does.
* **end_call** — the business is finished, or the caller is not going to let
  it be. The model decides; this ends the line.

Declarations are Gemini's `function_declarations` shape, which is a subset of
JSON Schema: object types, named properties, and `required`.
"""
from __future__ import annotations

from typing import Any

SEND_WHATSAPP_MESSAGE: dict[str, Any] = {
    "name": "send_whatsapp_message",
    "description": (
        "Send a WhatsApp message to the person you are on the call with. Use this "
        "whenever they ask for something in writing — a link, an address, a price "
        "list, a summary of what you agreed. Do NOT ask them for their number: you "
        "already have it. Only pass 'to' if they explicitly ask you to send it to a "
        "different number and read that number out to you."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "The message, written to be read on a phone screen. Keep it short "
                    "and concrete. Never promise anything the company has not said."
                ),
            },
            "to": {
                "type": "string",
                "description": (
                    "Only when sending to a different number than the one on the call. "
                    "Leave empty otherwise."
                ),
            },
        },
        "required": ["text"],
    },
}

END_CALL: dict[str, Any] = {
    "name": "end_call",
    "description": (
        "End the call. Say your goodbye first, in the same turn, then call this. "
        "Use it when the caller's question is answered and they have nothing further, "
        "when they say goodbye, or when the call is going nowhere — somebody testing "
        "you, repeating themselves, being abusive, or silent after you have twice "
        "asked whether they are still there. Do not use it in the middle of helping "
        "someone, and never as a way to avoid a question you cannot answer: offer to "
        "have a person call back instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "A few words for the record: 'the caller's question was answered', "
                    "'the caller rang off', 'the caller was not engaging'."
                ),
            },
        },
        "required": [],
    },
}


def for_call(*, can_send_whatsapp: bool) -> list[dict[str, Any]]:
    """What this particular call may do.

    Messaging is offered only when the company actually has a WhatsApp number
    that could carry it. Declaring it regardless would have the agent promise
    to text people from a company that cannot.
    """
    tools = [END_CALL]
    if can_send_whatsapp:
        tools.insert(0, SEND_WHATSAPP_MESSAGE)
    return tools
