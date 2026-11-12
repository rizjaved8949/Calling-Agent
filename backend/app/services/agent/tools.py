"""
The things the agent can *do* on a call, rather than say.

Each one is a promise to the caller that something will happen after they
hang up, so each is only worth declaring if it genuinely happens — a tool the
model can call and we cannot deliver is worse than no tool, because the caller
is told it is done.

* **send_whatsapp_message** — only after the caller asked, or said yes to an
  offer, and only to a number they said and confirmed. The server checks all
  of that against the transcript; the description here only explains it.
* **record_request** — a test drive, a service booking, a complaint, a
  callback, a cancellation, or a question nobody could answer. Stored, with a
  reference the server generates. Before this the agent said "submitted" and
  invented a reference number.
* **calculate** — arithmetic, so a shortfall or a refund is never a guess.
* **end_call** — the business is finished. The server checks the transcript
  agrees before it hangs up.

Declarations are Gemini's `function_declarations` shape, which is a subset of
JSON Schema: object types, named properties, enums, arrays, and `required`.
"""
from __future__ import annotations

from typing import Any

SEND_WHATSAPP_MESSAGE: dict[str, Any] = {
    "name": "send_whatsapp_message",
    "description": (
        "Send a WhatsApp message to the caller. Give the information on the call "
        "first; a message is an extra, never a substitute. Use this only after "
        "this exact sequence: (1) you offered to send the details on WhatsApp and "
        "the caller clearly said yes, or the caller asked for it in writing "
        "themselves; (2) you asked which number to send it to and they said it, "
        "or said to use the number they are calling from; (3) you read the number "
        "back in groups and they confirmed it. First call this with "
        "number_confirmed false: the result gives you the number grouped for "
        "reading back. Read it back, wait for their yes, then call again with "
        "number_confirmed true. Saying yes is not sending: the message goes only "
        "when the result says Sent, and only then may you say it is sent. Never "
        "send after a no, and never say a message is on its way before the "
        "result says Sent. The text is only the details themselves: no greeting, "
        "no self-introduction, no caller's name, no sign-off (no 'Drive safe', "
        "'Allah hafiz', 'Thank you'); the message template already greets and "
        "closes. When the caller asks for 'these details' or 'yeh details', "
        "include every item you told them on the call for that topic (for "
        "example, every model and price you quoted), not a selection; stay "
        "within 900 characters by writing each item compactly ('ATTO 2: PKR "
        "7,290,000'). Only if it truly cannot fit, send the items the caller "
        "asked about first and say on the call that the rest is available on "
        "request."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Only the details, written compactly, one item per line: "
                    "'ATTO 2: PKR 7,290,000'. No greeting, no name, no sign-off. "
                    "Never promise anything the company has not said."
                ),
            },
            "to": {
                "type": "string",
                "description": (
                    "The number the caller gave, with the digits as they said them, "
                    "or the word calling_number if they said to use the number they "
                    "are calling from."
                ),
            },
            "number_confirmed": {
                "type": "boolean",
                "description": (
                    "true only after you have read the number back to the caller "
                    "and they said it is right. false the first time."
                ),
            },
        },
        "required": ["text", "to"],
    },
}

RECORD_REQUEST: dict[str, Any] = {
    "name": "record_request",
    "description": (
        "Record a request for the team: a test drive, a service booking, a "
        "complaint, a callback, a cancellation, a question you could not answer "
        "from your material, or something else. The team reads it and contacts "
        "the caller. Nothing is booked, confirmed or cancelled by this; say so. "
        "A request exists only after this returns a reference: never say "
        "recorded, noted, submitted or booked before that, and never read back "
        "the details yourself in place of calling this. Collect the details "
        "first, asking for at most two per turn and never listing everything at "
        "once, then call this with confirmed false: the result names the next "
        "one or two details still needed, or gives you a read-back. Read the "
        "read-back to the caller, and when they say it is right call again with "
        "confirmed true. The result then gives a reference, which you read out "
        "exactly as given. For a question: offer to pass it to the team, who "
        "will answer on WhatsApp; only after a yes, ask for their WhatsApp "
        "number, read it back, and put the question in details. A name is "
        "optional for a question; do not insist on one. Occupation and when "
        "they plan to buy are optional for every type: record them only if the "
        "caller volunteers them, never insist, and never call them a policy."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["test_drive", "service", "complaint", "callback", "question",
                         "cancellation", "other"],
                "description": "What kind of request this is.",
            },
            "name": {"type": "string", "description": "The caller's name, as they said it."},
            "mobile": {
                "type": "string",
                "description": (
                    "The mobile number they want to be contacted on, digits as they "
                    "said them. A Pakistani mobile has 11 digits starting 03."
                ),
            },
            "city": {"type": "string", "description": "The caller's city."},
            "location": {
                "type": "string",
                "description": "The branch, showroom or care centre, if they named one.",
            },
            "preferred_times": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to two preferred days and times, in their words.",
            },
            "vehicle_model": {"type": "string", "description": "The model, if relevant."},
            "registration": {
                "type": "string",
                "description": "The vehicle's registration number, if relevant.",
            },
            "mileage": {"type": "string", "description": "The mileage, if relevant."},
            "occupation": {
                "type": "string",
                "description": "Optional: what the caller does, only if they said it.",
            },
            "purchase_timeline": {
                "type": "string",
                "description": "Optional: when they plan to buy, only if they said it.",
            },
            "details": {
                "type": "string",
                "description": (
                    "What the request is about, in one or two sentences: the "
                    "complaint, the question, the reason for the callback."
                ),
            },
            "reply_by": {
                "type": "string",
                "enum": ["call", "whatsapp"],
                "description": "How the team should reply. Default call.",
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "true only after you have read the details back and the caller "
                    "said they are right. false the first time."
                ),
            },
        },
        "required": ["type"],
    },
}

CALCULATE: dict[str, Any] = {
    "name": "calculate",
    "description": (
        "Work out any arithmetic before you say a figure: totals, differences, "
        "balances, shortfalls, monthly costs, percentages, refunds, instalments. "
        "Never do sums in your head. Give an expression with numbers and "
        "+ - * / % ** and brackets, for example 8990000-1750000, "
        "14790000*5/100, or 1500*12.2/100*55. min, max and round are allowed. "
        "The result is exact; use it as given."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The sum, with numbers and operators only.",
            },
        },
        "required": ["expression"],
    },
}

END_CALL: dict[str, Any] = {
    "name": "end_call",
    "description": (
        "End the call. Say your goodbye first, in the same turn, then call this. "
        "If the caller says goodbye or asks you to end the call, say the closing "
        "line once and call this at once; do not ask anything first. Otherwise "
        "use it only when the caller has said there is nothing else, or when you "
        "asked whether there is anything else and they said no, or when the call "
        "is going nowhere: somebody testing you, repeating "
        "themselves, being abusive, or silent after you have twice asked whether "
        "they are still there. Never end right after answering a question, never "
        "ask the caller whether to end the call, and never as a way to avoid a "
        "question you cannot answer: offer to record a request for a callback "
        "instead. Never call this while something you agreed to send is still "
        "unsent: if they asked for anything in writing and you said yes, call "
        "send_whatsapp_message and let it confirm before you end. Agreeing and "
        "then ending leaves them waiting for a message that never comes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "A few words for the record: 'the caller's question was answered', "
                    "'the caller said goodbye', 'the caller was not engaging'. Never "
                    "'the caller rang off': if they hang up, the call ends by itself."
                ),
            },
        },
        "required": [],
    },
}


def for_call(*, can_send_whatsapp: bool) -> list[dict[str, Any]]:
    """What this particular call may do.

    Messaging is offered only when the company actually has a WhatsApp number
    that could carry it right now. Declaring it regardless would have the
    agent promise to text people from a company that cannot.
    """
    tools = [RECORD_REQUEST, CALCULATE, END_CALL]
    if can_send_whatsapp:
        tools.insert(0, SEND_WHATSAPP_MESSAGE)
    return tools
