"""
WhatsApp messages from a call go out after the call.

On a real call the caller said "ji haan. balki nahin bheje." The message
left on the "ji haan", before "balki nahin bheje" had been transcribed; the
agent then said it would not send, and later denied the message the caller
was holding. Now the tool only queues, a no at any point cancels, and the
queue is sent once when the call is settled.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.errors import UpstreamError
from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.repositories import calls as call_repo
from app.services.agent import live, prompt, tools, turns
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

TEXT = "BYD SEAL Dynamic: PKR 14,790,000 (1 crore 47 lakh 90 hazar)"
YES_THEN_NO = "ji haan. balki nahin bheje."
OFFERED = [
    ("caller", "SEAL Dynamic ki details WhatsApp par bhej dein."),
    ("agent", "Zaroor. Kis number par bhejun?"),
    ("caller", "isi number par jis se main call kar raha hoon."),
    ("agent", "Kya main details is number par bhej doon jis ke aakhir mein 4604 hai?"),
]


def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923011114604")
    return Call(**{**fields, **extra})


def _tenant(**extra) -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", recordCalls=False,
                  **extra)


class _Ear:
    def __init__(self):
        self.notes: list[tuple[str, bool]] = []
        self.said: list[str] = []

    def feed(self, _pcm):
        pass

    async def note(self, text, urgent=False):
        self.notes.append((text, urgent))

    async def say(self, text):
        self.said.append(text)


def _watched(call: Call, lines=()) -> CallSession:
    session = CallSession(call.id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._agent = _Ear()
    for who, text in lines:
        session._note(who, text)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    return session


@pytest.fixture(autouse=True)
def _clean():
    for registry in (live._sessions, live._pending, live._recorded, live._calculated):
        registry.clear()
    yield
    for registry in (live._sessions, live._pending, live._recorded, live._calculated):
        registry.clear()


@pytest.fixture
def sending(monkeypatch):
    """A WhatsApp sender that records what it was asked to send, or fails
    when told to."""
    from app.services import lines

    class _Sent(list):
        failures: list[Exception]

    sent: _Sent = _Sent()
    failures: list[Exception] = []

    class FakeWhatsApp:
        def __init__(self, tenant):
            self.tenant = tenant

        async def send_text(self, to, body, **kwargs):
            if failures:
                raise failures.pop(0)
            sent.append((to, body, {**kwargs, "kind": "text"}))

        async def send_template(self, to, template, *, language, parameters, **kwargs):
            if failures:
                raise failures.pop(0)
            sent.append((to, parameters[1], {**kwargs, "kind": "template", "template": template,
                                             "language": language, "parameters": parameters}))

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant

    monkeypatch.setattr(lines, "whatsapp_sender", sender)
    sent.failures = failures
    return sent


async def _queue(call: Call, session: CallSession) -> tuple:
    """The two tool calls of a real send: the read-back, then the yes."""
    handle = live._tool_handler(_tenant(), call)
    first = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert "the number ending 4604" in first
    session._note("caller", "ji haan.")
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "calling_number", "number_confirmed": True})
    return handle, answer


# ---------------------------------------------------------------------------
# "ji haan. balki nahin bheje" is a no
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said, verdict", [
    (YES_THEN_NO, "no"),
    ("ji haan", "yes"),
    ("nahi, isi number par bhej dein", "yes"),
    ("ji bhej dein, lekin abhi nahi, rehne dein", "no"),
    ("mat bhejo", "no"),
    ("don't send it, cancel", "no"),
    ("theek hai", "yes"),
    ("Lahore mein", ""),
])
def test_the_last_word_on_sending_wins(said, verdict):
    assert turns.last_word_on_sending(said) == verdict


def test_a_yes_that_ends_in_a_no_is_not_consent():
    assert not turns.consented_to_message(OFFERED + [("caller", YES_THEN_NO)])
    assert turns.consented_to_message(OFFERED + [("caller", "ji haan")])


async def test_the_exact_line_cancels_the_queued_message(fake_db, sending, caplog):
    call = _call()
    await call_repo.save_call(call)
    session = _watched(call, OFFERED)
    handle, answer = await _queue(call, session)
    assert answer == live.QUEUED
    assert len(live.queued_messages(live._pending[call.id])) == 1
    # The rest of the caller's turn arrives.
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note("caller", " balki nahin bheje.")
        await asyncio.sleep(0.05)
    assert live.queued_messages(live._pending[call.id]) == []
    assert session._agent.notes == [(live.CANCELLED_NOTE, False)]
    assert session._agent.said == []
    assert any("cancelled the WhatsApp message" in r.getMessage() for r in caplog.records)
    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4604: cancelled by the caller" in saved.summary
    # Nothing goes out when the call ends.
    assert await live.flush_queued_messages(_tenant(), call, live._pending[call.id]) == []
    assert sending == []
    # And the tool, called again on the same words, says so.
    assert await handle("send_whatsapp_message",
                        {"text": TEXT, "to": "calling_number", "number_confirmed": True}) in (
        live.CANCELLED, live.NOT_ASKED)


async def test_a_plain_no_to_another_question_does_not_cancel(fake_db, sending):
    call = _call()
    session = _watched(call, OFFERED)
    _handle, answer = await _queue(call, session)
    assert answer == live.QUEUED
    session._note("agent", "Kya aap test drive bhi book karwana chahenge?")
    session._note("caller", "nahin.")
    await asyncio.sleep(0.05)
    assert len(live.queued_messages(live._pending[call.id])) == 1
    assert session._agent.notes == []


# ---------------------------------------------------------------------------
# Sent once, when the call ends
# ---------------------------------------------------------------------------

class _Settled:
    handler = type("H", (), {"value": "agent"})()
    frames_out = 10

    def __init__(self, transcript):
        self.transcript = list(transcript)

    def turns(self):
        return turns.turns(self.transcript)

    async def end(self, _reason):
        return None

    def transcript_text(self):
        return "\n".join(f"{who}: {text}" for who, text in self.transcript)


async def test_a_queued_message_is_sent_once_when_the_call_is_settled(fake_db, sending, caplog):
    call = _call(status=CallStatus.IN_PROGRESS, answeredAt=time.time() - 60)
    await call_repo.save_call(call)
    session = _Settled(OFFERED + [("caller", "ji haan.")])
    live._sessions[call.id] = session
    handle = live._tool_handler(_tenant(), call)
    await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert await handle("send_whatsapp_message",
                        {"text": TEXT, "to": "calling_number", "number_confirmed": True}) == live.QUEUED
    assert sending == [], "sent during the call"
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        await live.finish(_tenant(), call.id, "the caller said goodbye")
    assert [(to, body) for to, body, _ in sending] == [("+923011114604", TEXT)]
    saved = await call_repo.get_call("t-1", call.id)
    assert saved.status is CallStatus.COMPLETED
    assert "WhatsApp to …4604: queued, to be sent after the call" in saved.summary
    assert "WhatsApp to …4604: sent after the call (template)" in saved.summary
    [line] = [r.getMessage() for r in caplog.records if "sent the queued WhatsApp" in r.getMessage()]
    assert "+923011114604" not in line and "4604" in line
    # Settling again sends nothing more.
    await live.finish(_tenant(), call.id, "again")
    assert len(sending) == 1


async def test_a_second_queue_for_the_same_number_replaces_the_first(fake_db, sending):
    call = _call()
    session = _watched(call, OFFERED)
    handle, answer = await _queue(call, session)
    assert answer == live.QUEUED
    again = await handle("send_whatsapp_message",
                         {"text": TEXT + " · ATTO 3: PKR 8,990,000", "to": "calling_number",
                          "number_confirmed": True})
    assert again == live.QUEUED_AGAIN
    assert await live.flush_queued_messages(_tenant(), call, live._pending[call.id]) == ["sent (template)"]
    assert len(sending) == 1 and "ATTO 3" in sending[0][1]


async def test_a_failed_send_is_recorded_on_the_call(fake_db, sending, caplog):
    call = _call()
    await call_repo.save_call(call)
    session = _watched(call, OFFERED)
    _handle, answer = await _queue(call, session)
    assert answer == live.QUEUED
    sending.failures.append(UpstreamError("Meta refused: (#131030) recipient not in allowed list"))
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        results = await live.flush_queued_messages(_tenant(), call, live._pending[call.id])
    assert results == ["not sent (could not be sent: Meta refused: (#131030) recipient not in "
                       "allowed list)"]
    assert sending == []
    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4604: not sent (could not be sent: Meta refused" in saved.summary
    [warning] = [r.getMessage() for r in caplog.records if "did not send after the call" in r.getMessage()]
    assert "+923011114604" not in warning and "4604" in warning
    # Not tried again.
    assert await live.flush_queued_messages(_tenant(), call, live._pending[call.id]) == []


# ---------------------------------------------------------------------------
# The agent knows the state
# ---------------------------------------------------------------------------

def test_the_tool_replies_say_queued_cancelled_and_never_sent():
    assert live.QUEUED == ("Queued. Tell the caller the details will be sent on WhatsApp after the "
                           "call. Do not say it has been sent.")
    assert "Nothing is queued and nothing will be sent" in live.CANCELLED
    assert not hasattr(live, "NOT_SENT")
    description = tools.SEND_WHATSAPP_MESSAGE["description"]
    assert "it is sent after the call ends, never during it" in description
    assert "the message is queued only when the result says Queued" in description
    assert "never say it has been sent" in description
    assert "answered Sent" not in description and "says Sent" not in description


async def test_a_message_said_to_have_arrived_gets_the_truth_about_this_call(fake_db, sending):
    call = _call()
    session = _watched(call, OFFERED)
    _handle, answer = await _queue(call, session)
    assert answer == live.QUEUED
    session._note("agent", "Theek hai, details call ke baad bheji jayengi.")
    session._note("caller", "mujhe message aa gaya hai")
    await asyncio.sleep(0.05)
    [(note, urgent)] = session._agent.notes
    assert not urgent and note.startswith(live.NOTE_PREFIX)
    assert "No WhatsApp message has been sent on this call; one message is queued to be sent after the call" in note
    assert "Is call mein koi message abhi bheja nahi gaya; details call ke baad bheji jayengi" in note
    assert "never ask the caller to read a message out" in note
    assert session._agent.said == []


async def test_with_nothing_queued_the_note_says_so(fake_db):
    call = _call()
    session = _watched(call, [("agent", "Aur koi sawal?")])
    session._note("caller", "mujhe aap ka message mil gaya hai")
    await asyncio.sleep(0.05)
    [(note, _urgent)] = session._agent.notes
    assert "on this call; nothing is queued" in note


def test_the_writing_block_describes_the_new_flow():
    text = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True).split())
    assert "Messages from a call go out after the call ends." in text
    assert "When the result says Queued, tell the caller the details will be sent on WhatsApp after the call" in text
    assert "details call ke baad WhatsApp par bheji jayengi" in text
    assert "A yes followed by a no in the same breath is a no" in text
    assert "Is call mein koi message abhi bheja nahi gaya; details call ke baad bheji jayengi" in text
    assert "never ask the caller to read a message out" in text
    assert "Agreeing is not queueing" in text
    assert "answered Sent" not in text and "says Sent" not in text and "say it is sent" not in text


# ---------------------------------------------------------------------------
# The calling-number wording
# ---------------------------------------------------------------------------

def test_the_calling_number_wording_in_urdu_and_english():
    text = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True,
                                 calling_number_ending="4604").split())
    assert ("Aap ka poora number mujhe nazar nahi aata, lekin main us number par bhej sakti hoon "
            "jis se aap call kar rahe hain, jis ke aakhir mein 4604 hai.") in text
    assert ("I can't see your full number, but I can send it to the number you are calling from, "
            "ending 4604.") in text
    assert "never a flat 'I cannot see your number'" in text
    assert "<last four digits>" not in text
    without = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True).split())
    assert "jis ke aakhir mein the last four digits hai" in without


async def test_the_real_call_gets_its_own_last_four_digits(fake_db, monkeypatch):
    async def can_send(_tenant):
        return True

    monkeypatch.setattr(live, "_can_send_whatsapp", can_send)
    persona = await live.persona_for(_tenant(language="ur-PK"), _call(counterparty="+923011114604"))
    assert "jis ke aakhir mein 4604 hai" in persona.instructions
    assert "ending 4604." in persona.instructions
