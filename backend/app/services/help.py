"""
The assistant on the Guides page.

Different from the agent in one way that matters: it answers questions about
*this product* — where a setting is, why a number will not verify, what a
message in red means — rather than about the company's own customers. So it is
grounded in the text below rather than in anything a company uploaded, and it
never sees their documents.

It can also be shown a thing: a screenshot of an error, a PDF of a provider's
settings page. That is the question people actually have — "why does this say
this" — and asking them to transcribe an error message before you will look at
it is a poor way to help.
"""
from __future__ import annotations

import logging

from ..config import settings

log = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/gif", "image/heic",
    "application/pdf", "text/plain", "text/csv", "text/markdown",
}

# What the assistant knows. Kept here rather than read from the guides at
# runtime because the guides are written for reading start to finish and this
# needs the facts, stated once, in the order a question arrives in.
PRODUCT_GUIDE = """
# What this product is
A platform where a company connects its own phone and WhatsApp numbers, builds
an AI agent, and has that agent answer or place calls and WhatsApp messages.
Each company brings its own provider accounts, so its traffic and its bill are
its own.

# Setting up, in order
1. Numbers → Connect a number. Choose Phone line (Infobip) or WhatsApp (Meta),
   choose inbound / outbound / both, and paste that provider's credentials.
   The platform asks the provider straight away and marks the number Verified
   or Not verified with the provider's own reason.
2. Paste the webhook URL the number then shows into Infobip, or into the Meta
   app subscribed to both `messages` and `calls`.
3. Agents → New agent. Inbound, outbound or both, with a greeting, a
   description of who it is, a language, a talking speed and a voice.
4. Knowledge → create a knowledge base, upload documents, pick it on the agent.
5. Numbers → choose that agent as the number's inbound agent, outbound agent,
   or both. Until an inbound agent is chosen, incoming calls are declined.

# Numbers
Credentials live on the number, not on the company, so one company can have
several numbers from different accounts.
For Infobip you need the base URL (top of the portal home page) and an API key
with the Voice/Calls scope. The Calls configuration ID is optional.
For Meta you need the Phone number ID and WhatsApp Business Account ID from
API Setup, a System User access token (the temporary one expires in 24 hours),
the App secret from App settings → Basic, and a webhook verify token you invent.
Editing any credential sends the number back to be verified again.
A number with no inbound agent declines incoming calls deliberately: answering
with whatever agent happened to exist is worse.
In the Infobip portal the number's Default inbound configuration must forward
to a Calls API Subscription with your calls configuration selected.

# Agents
An agent is inbound, outbound or both, and only fits a matching slot on a
number. It has an opening line, a description of who it is, tone and hard
rules, escalation rules, a language, a talking speed (slow, natural, brisk)
and a voice (choose a man's or a woman's).
Changes are held as a draft and saved when you press Save.
An agent answers only from its knowledge base. With none, it says it will have
someone call back rather than inventing an answer.

What an agent is for is a set of tick boxes, not one choice: answer calls that
come in, make calls out, reply to WhatsApp messages, or any combination. A
number only offers agents that can do the job being filled.

Under "The exact instructions" every rule the agent is given is shown in plain
text and can be rewritten — how it speaks, how it listens, what it must not
promise, when it hangs up, what it says it is. Leave one alone and the standard
wording is used. "Anything else it must do" is added at the very end, so it
wins any disagreement with the rest. There is also an override for writing the
whole prompt yourself, and a preview that shows the exact text the agent is
given, which is the only way to tell whether a rule survived.

By default an agent cannot transfer a call, put anyone on hold, book or cancel
anything, or look up an individual account. Those limits are in the "What it
must not do" block and can be changed, though nothing stops the agent
promising something it cannot deliver once they are.

# Knowledge
A knowledge base is a named set of documents, and an agent reads exactly one.
That is what lets a support line and a sales campaign answer from different
material on the same account.
PDF, text, markdown and CSV are read. A scanned PDF with no text layer has
nothing to extract.
Write short, direct answers to questions people actually ask on the phone.
Avoid tables and bullet lists — the agent is speaking, not showing a page.

# Calls
Live: the agent calls one person. Needs a verified outbound number with an
outbound agent.
Campaigns: the agent works through a list one at a time with a gap between,
and nobody is redialled automatically.
Dialer: a person calls a customer from the company's number and talks through
their own browser. No agent is involved. Staff land here.
Call recordings: every recorded call, playable in the list or in full on the
call, with the transcript. Deleting the audio and deleting the whole call are
separate choices.

# WhatsApp messages
A free-text message only reaches someone within 24 hours of their last message.
Outside that window Meta accepts the request and delivers nothing, so use an
approved template.
A template's values come from Meta's catalogue: the form asks for exactly as
many as the template takes.
Deleting a message removes it from this workspace only. WhatsApp gives
businesses no way to unsend, so the recipient keeps their copy.

# Team
Invite colleagues from Team. They join as staff: they can use the Dialer, take
over a live call, look up a customer and ask the documents, but cannot see
credentials, change settings or manage the team. There is one owner.
No email is sent — you are given a link to pass on yourself.

# Contacting the platform operator
Contact support, under Manage, is a live chat with the people who run the
platform. Messages arrive instantly while both sides have it open, and a badge
appears on the link when a reply is waiting.
Photos, videos, voice notes, PDFs and text files can be attached, and the
microphone button records a voice note in the browser. Photos and voice notes
are limited to 10 MB, video to 40 MB.
A message can be deleted, and it goes for both sides — there is one shared
history, so hiding it from only yourself would leave the other person
answering something you cannot see. You can only delete your own.
This is the place for anything about your own account. The assistant on the
Guides page answers how the product works and cannot see your account.

# Recordings and privacy
Recording is on by default and can be turned off in Settings, which affects new
calls only. Connect Google Drive and each recording is also copied to a folder
in your own Google account.
Provider credentials are encrypted before storage and no screen or endpoint can
read one back — only the last four characters.

# Common problems
"Not verified" — the provider refused the credentials. The reason shown is the
provider's own words. Check the API key scope, the base URL, and that the token
is a System User token rather than a temporary one.
A call connects then ends after a second — usually the number is not connected
on the Numbers page, so there is no audio endpoint for it.
The caller hears silence — check the speech engine has a key set, under the
platform portal, and press Test there to ask the provider whether the key and
model actually work. Saving a key proves nothing on its own.
"None of your numbers is verified and allowed to make outbound calls" — the
number is inbound-only, or not verified.
WhatsApp "Call already ongoing" — a previous call never settled. It clears by
itself; calls that never report an ending are swept after two hours.
"""


def available() -> bool:
    return bool(settings.gemini_api_key.strip())


def _refuse(reason: str) -> dict:
    return {"answered": False, "text": reason}


async def answer(
    question: str,
    *,
    attachment: bytes | None = None,
    attachment_type: str = "",
    attachment_name: str = "",
) -> dict:
    """Answer a question about the product, optionally about an attachment."""
    question = (question or "").strip()

    # The attachment is checked before anything else. "That file type cannot be
    # read" is true whether or not an engine is configured, and it is the more
    # useful thing to be told.
    if attachment is not None:
        if attachment_type not in ALLOWED_TYPES:
            return _refuse(
                f"{attachment_type or 'That file type'} cannot be read. Attach a "
                "screenshot, a PDF or a text file."
            )
        if len(attachment) > MAX_ATTACHMENT_BYTES:
            return _refuse("That file is too large. Keep attachments under 8 MB.")
    if not question and attachment is None:
        return _refuse("Ask a question, or attach a screenshot of what you are seeing.")
    if not available():
        return _refuse(
            "The help assistant is not configured on this deployment. The guides "
            "below still cover everything."
        )

    from google import genai
    from google.genai import types

    parts: list = []
    if attachment is not None:
        parts.append(types.Part.from_bytes(data=attachment, mime_type=attachment_type))
    parts.append(types.Part(text=question or (
        f"Look at {attachment_name or 'this'} and tell me what it says and what to do."
    )))

    instructions = (
        "You are the help assistant inside a calling-agent platform, answering "
        "the people who run it — not their customers.\n"
        "Answer from the documentation below and from what you can see in any "
        "attachment. Be specific: name the screen and the button. Two or three "
        "short sentences is usually right; use a short list only for ordered "
        "steps.\n"
        "If an attachment shows an error, say what it means and the next action.\n"
        "If the documentation does not cover it, say so plainly and suggest "
        "where to look rather than inventing a screen or a setting that does "
        "not exist. Never invent a menu item or a button.\n"
        "Write plain text. No markdown: no asterisks for bold, no hashes for "
        "headings, no backticks. What you write is shown exactly as written, so "
        "'**Numbers**' reaches the reader with the asterisks in it. Name a "
        "screen by writing its name. For steps, start each line with '- '.\n\n"
        "DOCUMENTATION\n" + PRODUCT_GUIDE
    )

    try:
        client = genai.Client(api_key=settings.gemini_api_key.strip())
        response = await client.aio.models.generate_content(
            model=settings.gemini_text_model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=instructions, max_output_tokens=700, temperature=0.2,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a failed lookup must not break the page
        log.warning("help assistant failed (%s: %s)", type(exc).__name__, str(exc)[:200])
        return _refuse("Could not answer that right now. Try again in a moment.")

    text = (getattr(response, "text", "") or "").strip()
    if not text:
        return _refuse("No answer came back. Try rephrasing the question.")
    return {"answered": True, "text": text}
