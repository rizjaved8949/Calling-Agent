"""
From the tenth real call: a message asked for, agreed to twice, queued, and
then cancelled by a "Nahin" that answered "anything else?".

The caller asked for the price on WhatsApp in their first sentence. The agent
offered the line ending 4604; the caller said "Thik hai ji, send karte hain"
and "ji bilkul"; the tool answered that the caller had not chosen the line,
and the agent said it "could not confirm" the number. Later the message was
queued, the agent asked "Kya main aapki kisi aur tarah se madad kar sakti
hoon?", the caller said "Nahin, woh chukriya. Allah Hafiz.", and the queue
was cancelled. The price was never said on the call.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, prompt, tools, turns
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

L2 = ("caller", "Jio C-Line 7 key price number per send kar de WhatsApp per.")
L3 = ("agent", "Main samajh gayi. Kya main price aapko WhatsApp par bhej doon? Aap ka poora "
               "number mujhe nazar nahi aata, lekin main us number par bhej sakti hoon jis se "
               "aap call kar rahe hain, jis ke aakhir mein 4604 hai.")
L4 = ("caller", "Thik hai ji, send karte hain.")
L5 = ("agent", "Aap ka poora number mujhe nazar nahi aata, lekin main us number par bhej sakti "
               "hoon jis se aap call kar rahe hain, jis ke aakhir mein 4604 hai. Kya us par bhej "
               "doon?")
L6 = ("caller", "ji bilkul.")
L8 = ("caller", "Isi number par jis par aapne repeat kiya hai.")
L9 = ("agent", "Theek hai, number jis ke aakhir mein 4604 hai. Kya yeh sahi hai?")
L10 = ("caller", "Bilkul sahi hai. Isi pe bhej de jaldi se.")
L11 = ("agent", "Details call ke baad WhatsApp par bhej di jayengi. Kya main aapki kisi aur tarah "
                "se madad kar sakti hoon?")
L12 = ("caller", "Nahin, woh chukriya. Allah Hafiz.")
TEXT = "SEALION 7 Premium: PKR 15,490,000 (1 crore 54 lakh 90 hazar)"
TOOLS = [{"name": "send_whatsapp_message"}, {"name": "end_call"}]


def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923011114604")
    return Call(**{**fields, **extra})


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", recordCalls=False)


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
                          persona=AgentPersona(instructions="", tools=TOOLS), record=False)
    session._agent = _Ear()
    for who, text in lines:
        session._note(who, text)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    return session


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from app.services import lines

    async def sender(tenant):
        return tenant

    monkeypatch.setattr(lines, "whatsapp_sender", sender)
    for registry in (live._sessions, live._pending, live._recorded, live._calculated):
        registry.clear()
    yield
    for registry in (live._sessions, live._pending, live._recorded, live._calculated):
        registry.clear()


@pytest.fixture
def sending(monkeypatch):
    sent: list[tuple[str, str]] = []

    class FakeWhatsApp:
        def __init__(self, tenant):
            self.tenant = tenant

        async def send_text(self, to, body, **kwargs):
            sent.append((to, body))

        async def send_template(self, to, template, *, language, parameters, **kwargs):
            sent.append((to, parameters[1]))

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)
    return sent


# ---------------------------------------------------------------------------
# Fix 2: one offer, one yes, queued
# ---------------------------------------------------------------------------

async def test_thik_hai_ji_after_the_line_is_offered_queues_at_once(fake_db):
    call = _call()
    _watched(call, [L2, L3, L4])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert answer == live.QUEUED, answer
    assert live.queued_messages(live._pending[call.id])[0]["to"] == "+923011114604"


async def test_ji_bilkul_after_the_line_is_offered_queues_at_once(fake_db):
    call = _call()
    _watched(call, [L2, L3, L4, L5, L6])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "calling_number", "number_confirmed": True})
    assert answer == live.QUEUED, answer
    assert answer != live.NOT_SAME_NUMBER
    assert "could not" not in answer and "Read this back" not in answer


async def test_a_yes_to_something_else_does_not_choose_the_line(fake_db):
    call = _call()
    _watched(call, [("agent", "Kya aap test drive karna chahenge?"), ("caller", "ji bilkul")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert answer in (live.NOT_ASKED, live.NOT_SAME_NUMBER)


def test_the_prompt_and_tool_say_one_offer_one_yes():
    text = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True).split())
    assert "Offer the number once." in text
    assert "One offer, one yes, done: never ask for the number again after a yes" in text
    description = tools.SEND_WHATSAPP_MESSAGE["description"]
    assert "Once you have named the caller's line by its last four digits and they said yes" in description


# ---------------------------------------------------------------------------
# Fix 1: the cancel rule
# ---------------------------------------------------------------------------

async def _queued_call(sending) -> tuple[Call, CallSession, object]:
    call = _call()
    session = _watched(call, [L2, L3, L4, L5, L6, ("agent", "Theek hai."), L8, L9, L10])
    handle = live._tool_handler(_tenant(), call)
    assert await handle("send_whatsapp_message",
                        {"text": TEXT, "to": "calling_number", "number_confirmed": True}) == live.QUEUED
    return call, session, handle


async def test_nahin_shukriya_allah_hafiz_after_anything_else_does_not_cancel(fake_db, sending, caplog):
    call, session, _handle = await _queued_call(sending)
    session._note(*L11)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note(*L12)
        await asyncio.sleep(0.05)
    assert len(live.queued_messages(live._pending[call.id])) == 1
    assert not any("cancelled" in r.getMessage() for r in caplog.records)
    assert session._agent.notes == []
    await live.flush_queued_messages(_tenant(), call, live._pending[call.id])
    assert [to for to, _ in sending] == ["+923011114604"]


@pytest.mark.parametrize("said", [
    "nahi shukriya", "bas, theek hai", "Allah hafiz", "nahi, aur kuch nahi", "no thanks, bye",
])
async def test_other_nos_thanks_and_goodbyes_never_cancel(fake_db, sending, said):
    call, session, _handle = await _queued_call(sending)
    session._note(*L11)
    session._note("caller", said)
    await asyncio.sleep(0.05)
    assert len(live.queued_messages(live._pending[call.id])) == 1, said


async def test_a_no_to_another_question_never_cancels(fake_db, sending):
    call, session, _handle = await _queued_call(sending)
    session._note("agent", "Bhej di jayegi. Kya aap test drive bhi book karwana chahenge?")
    session._note("caller", "nahi")
    await asyncio.sleep(0.05)
    assert len(live.queued_messages(live._pending[call.id])) == 1


async def test_mat_bhejein_cancels_and_the_words_are_logged(fake_db, sending, caplog):
    call, session, handle = await _queued_call(sending)
    session._note(*L11)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note("caller", "mat bhejein")
        await asyncio.sleep(0.05)
    assert live.queued_messages(live._pending[call.id]) == []
    [line] = [r.getMessage() for r in caplog.records if "said not to send" in r.getMessage()]
    assert "'mat bhejein'" in line
    assert session._agent.notes == [(live.CANCELLED_NOTE, False)]
    assert await live.flush_queued_messages(_tenant(), call, live._pending[call.id]) == []
    assert sending == []


async def test_a_bare_no_to_shall_i_send_it_cancels(fake_db, sending):
    call, session, _handle = await _queued_call(sending)
    session._note("agent", "Kya main yeh WhatsApp par bhej doon?")       # asked again, after queueing
    session._note("caller", "nahi")
    await asyncio.sleep(0.05)
    assert live.queued_messages(live._pending[call.id]) == []


async def test_a_bare_no_to_the_offer_with_nothing_queued_is_a_no(fake_db):
    call = _call()
    _watched(call, [("agent", "Kya main WhatsApp par bhej doon?"), ("caller", "nahi")])
    conversation = live._sessions[call.id].turns()
    assert live._caller_cancelled(conversation, {}) == "said no to sending"
    assert not turns.consented_to_message(conversation)
    handle = live._tool_handler(_tenant(), call)
    assert await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"}) == live.NOT_ASKED


def test_what_the_agents_last_question_was_about():
    assert turns.question_about_sending(L5[1])
    assert turns.question_about_sending("Kya main WhatsApp par bhej doon?")
    assert not turns.question_about_sending(L11[1])
    assert not turns.question_about_sending("Kya main yeh details bhej doon, ya aur kuch?")
    assert not turns.question_about_sending("Details bhej di jayengi.")
    assert turns.is_bare_no("nahi")
    assert turns.is_bare_no("Nahin.")
    assert not turns.is_bare_no(L12[1])
    assert not turns.is_bare_no("nahi shukriya")
    assert not turns.is_bare_no("bas")


async def test_a_no_to_the_offer_does_not_cancel_what_was_queued_after_it(fake_db, sending):
    """The question came first, the queue after it; the bare no that follows
    answers something else."""
    call = _call()
    session = _watched(call, [L2, L3])
    handle = live._tool_handler(_tenant(), call)
    session._note(*L4)
    assert await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"}) == live.QUEUED
    session._note("caller", "nahi")                       # a stray no, same question before it
    await asyncio.sleep(0.05)
    assert len(live.queued_messages(live._pending[call.id])) == 1


# ---------------------------------------------------------------------------
# Fix 3: answer on the call first
# ---------------------------------------------------------------------------

def test_the_prompt_says_confirm_the_name_answer_then_queue():
    text = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True).split())
    assert "When the caller asks for something on WhatsApp straight away, still answer on the call first" in text
    assert "If the model or item they named is unclear or matches nothing in your material, confirm it first" in text
    assert "then say the answer on the call; then queue the message" in text
    assert "Never queue a message instead of answering" in text
    assert "an unclear model or item name is confirmed before anything is answered or queued" in tools.SEND_WHATSAPP_MESSAGE["description"]
