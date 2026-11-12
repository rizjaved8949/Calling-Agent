"""
From the fifth evaluator call: a complaint the agent could not take down.

The caller gave a ten-digit mobile and the agent read it back as complete;
asked for four details in one question; said "Privacy policies ki wajah se";
and, asked "Kya aapko mera mobile number pata hai?", said it did not know a
number the caller had said on this very call.
"""
from __future__ import annotations

import asyncio
import logging
import re

import pytest

from app.models.call import Call, Channel, Direction
from app.models.request import RequestType, missing_fields
from app.models.tenant import Tenant
from app.repositories import requests as request_repo
from app.services.agent import live, phones, prompt, tools
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

L2 = ("caller", "Haan, mujhe apni gadi Atto 3 ke hawale se shikayat darj karni thi. Usne charge "
                "hona band kar diya. Uska charger kaam nahin kar raha. Maine 7 kW charger se "
                "usko charge karne ki koshish ki thi.")
L3 = ("agent", "Aap ki shikayat darj karne ke liye mujhe kuch details chahiye jaise aap ka naam, "
               "mobile number, gaari ka registration number, aur mileage. Kya aap yeh sab bata "
               "sakte hain?")
L4 = ("caller", "Mera naam Suman hai aur mera mobile number hai 0300123456 aur gadi ka "
                "registration number hai 112255 aur mileage 30 hai.")
L5 = ("agent", "Saman sahiba, kya main details read back kar sakti hoon? Aap ka naam Saman, "
               "mobile number 0300 123456, gaari ka registration number 112255, aur mileage 30 "
               "hai. Kya yeh sab sahi hai?")
L12 = ("caller", "Kya aapko mera mobile number pata hai?")
L13 = ("agent", "Mujhe aap ka mobile number maloom nahi hai. Kya aap batana chahenge? Aur kya "
                "main aap ki koi aur help kar sakti hoon?")
# From the second call: a complete number, said the same way.
COMPLETE = ("caller", "Mera naam Alif hai aur mera phone number 03001234567 hai aur main Lahore "
                      "shehar mein gaadi test drive karna chahta hoon Alto ko Alto ko.")


def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923011114604")
    return Call(**{**fields, **extra})


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", recordCalls=False)


class _Ear:
    def __init__(self):
        self.notes: list[str] = []
        self.said: list[str] = []

    def feed(self, _pcm):
        pass

    async def note(self, text):
        self.notes.append(text)

    async def say(self, text):
        self.said.append(text)


def _session(call_id: str, lines=()) -> CallSession:
    session = CallSession(call_id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._agent = _Ear()
    for who, text in lines:
        session._note(who, text)
    return session


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(live, "MOBILE_NOTE_DELAY_SECONDS", 0.1)
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()
    yield
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()


def _watched(call: Call, lines=()) -> CallSession:
    session = _session(call.id, lines)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    return session


def _text() -> str:
    return " ".join(prompt.build(persona="P", context="C").split())


# ---------------------------------------------------------------------------
# A. It knows what it was told; it cannot see the rest
# ---------------------------------------------------------------------------

def test_the_prompt_says_what_the_agent_knows_and_what_it_cannot_see():
    text = _text()
    assert "Everything the caller has told you on this call, you know." in text
    assert "If they ask, repeat it back" in text
    assert "Never say you do not know something they told you on this call" in text
    assert "What you cannot see or look up:" in text
    assert "the number the caller is calling from, earlier calls" in text
    assert "no access to customer records, bookings, complaints or accounts" in text
    assert "Never refuse to repeat a caller's own details to them" in text


def test_the_prompt_never_tells_the_agent_it_lacks_the_callers_details():
    text = _text().lower()
    for wrong in ("you do not have their number", "you do not know their number",
                  "you do not have the caller's number", "you do not know their details",
                  "never say you have their number", "do not know the caller"):
        assert wrong not in text, wrong


# ---------------------------------------------------------------------------
# B. A ten-digit mobile
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said, count", [
    (L4[1], 10),
    ("0300123456", 10),
    ("mera number zero three zero zero one two three four five six hai", 10),
    ("030012345678", 12),
    (COMPLETE[1], None),
    ("03001234567", None),
    ("0300 123 4567", None),
    ("923001234567", None),
    ("+92 300 1234567", None),
    ("35202-1234567-1", None),                       # a CNIC
    ("bajat ₹1,50,00,000 hai", None),
    ("registration number hai 112255 aur mileage 30 hai", None),
    ("", None),
])
def test_what_counts_as_a_mobile_of_the_wrong_length(said, count):
    assert phones.wrong_length_mobile(said, "92") == count


async def test_a_ten_digit_mobile_gets_a_silent_note(quiet, caplog):
    call = _call()
    session = _watched(call, [L2, L3])
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note(*L4)
        await asyncio.sleep(0.3)
    assert session._agent.notes == [live.MOBILE_LENGTH_NOTE.format(n=10)]
    assert "has 10 digits; a Pakistani mobile has 11" in session._agent.notes[0]
    assert session._agent.notes[0].startswith(live.NOTE_PREFIX)
    assert session._agent.said == []
    [line] = [r.getMessage() for r in caplog.records if "mobile number with" in r.getMessage()]
    assert "10 digits" in line and "0300123456" not in line
    session._note("caller", " haan.")                 # more of the same turn
    await asyncio.sleep(0.3)
    assert len(session._agent.notes) == 1


async def test_an_eleven_digit_mobile_gets_no_note(quiet):
    call = _call()
    session = _watched(call)
    session._note(*COMPLETE)
    await asyncio.sleep(0.3)
    assert session._agent.notes == []


async def test_a_number_still_being_said_is_not_counted_early(quiet):
    call = _call()
    session = _watched(call)
    session._note("caller", "mera mobile number hai 03001234")
    await asyncio.sleep(0.02)
    session._note("caller", "567")
    await asyncio.sleep(0.3)
    assert session._agent.notes == []


async def test_the_tool_refuses_the_ten_digit_number_too(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L2, L3, L4])
    answer = await live._tool_handler(_tenant(), call)("record_request", {
        "type": "complaint", "name": "Suman", "mobile": "0300123456",
        "registration": "112255", "details": L2[1]})
    assert "looks incomplete" in answer and "11 digits" in answer


def test_the_prompt_says_never_to_read_a_short_number_back_as_complete():
    text = _text()
    assert "A Pakistani mobile has 11 digits; if you hear fewer, ask again" in text
    assert "Never read a short or long number back as complete" in text


# ---------------------------------------------------------------------------
# C. One question per turn; a complaint two details at a time
# ---------------------------------------------------------------------------

def test_the_prompt_asks_one_question_per_turn():
    text = _text()
    assert "Ask for at most two details per turn, in one question" in text
    assert "one step at a time: ask, wait for the answer, then the next" in text
    assert "Never put several checks, or a check and a request for details, in one turn" in text
    assert "End each turn with at most one question" in text


def test_a_complaint_is_asked_for_in_this_order():
    assert missing_fields(RequestType.COMPLAINT, {"details": L2[1]}) == [
        "the caller's name", "a mobile number",
        "the vehicle model or the registration number"]
    assert missing_fields(RequestType.COMPLAINT, {
        "details": L2[1], "name": "Suman", "mobile": "+923001234567"}) == [
        "the vehicle model or the registration number"]
    assert missing_fields(RequestType.COMPLAINT, {
        "details": L2[1], "name": "Suman", "mobile": "+923001234567",
        "registration": "112255"}) == []
    # A complaint does not wait for the city.
    assert "the city" not in missing_fields(RequestType.COMPLAINT, {})
    assert "the city" in missing_fields(RequestType.TEST_DRIVE, {})


async def test_the_tool_names_at_most_two_missing_details(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L2])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {"type": "complaint", "details": L2[1]})
    assert answer.startswith("Not recorded yet. Ask next for the caller's name and a mobile "
                             "number; 1 more after that.")


async def test_the_complaint_is_read_back_and_recorded_without_a_city(quiet, fake_db):
    """The call's details with the number the caller should have given."""
    call = _call()
    live._sessions[call.id] = _session(call.id, [
        L2, L3, ("caller", "Mera naam Suman hai aur mera mobile number hai 0300 1234567 aur "
                           "gadi ka registration number hai 112255 aur mileage 30 hai.")])
    handle = live._tool_handler(_tenant(), call)
    args = {"type": "complaint", "name": "Suman", "mobile": "0300 1234567",
            "registration": "112255", "mileage": "30", "details": L2[1]}
    first = await handle("record_request", args)
    assert first.startswith("Not recorded yet. Read this back once")
    assert "A complaint request, for Suman" in first and "registration 112255" in first
    live._sessions[call.id]._note("agent", "Suman, mobile zero three zero zero one two three four "
                                           "five six seven, registration 112255, charger kaam nahi "
                                           "kar raha. Kya yeh sahi hai?")
    live._sessions[call.id]._note("caller", "ji bilkul")
    answer = await handle("record_request", {**args, "confirmed": True})
    assert re.match(r"^Recorded\. Reference CMP-\d{8}-01\. ", answer), answer
    [row] = list(fake_db.tables.get(request_repo.TABLE, {}).values())
    assert row["data"]["type"] == "complaint" and row["data"]["city"] == ""


def test_the_tool_and_prompt_say_how_a_complaint_is_taken():
    assert ("For a complaint: the name and mobile first, then the registration or model and "
            "what happened, then read back and record it before anything else"
            in tools.RECORD_REQUEST["description"])
    text = _text()
    assert "For a complaint: ask for the name and mobile first" in text
    assert "A complaint is registered only when record_request returns a reference" in text


# ---------------------------------------------------------------------------
# D. No privacy policy
# ---------------------------------------------------------------------------

def test_the_prompt_never_mentions_a_privacy_policy():
    text = _text()
    assert "privacy" not in text.lower().replace("never mention a privacy policy", "")
    assert "say only that you cannot share another customer's information" in text
    assert "Never mention a privacy policy or data rules" in text
