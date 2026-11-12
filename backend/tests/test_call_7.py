"""
From the seventh real call: a complaint that was "sent to the team" and
never recorded.

The agent opened with "mujhe maloom nahi ke problem kya hai", asked for three
details in one turn, never asked for the mobile number, never called
record_request, and closed with "main team ko bhej rahi hoon. Woh aapse
rabta karenge".
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, prompt, tools
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

L2 = ("caller", "Meri auto theek charge nahi ho rahi, shikayat darj karni hai. Mera naam Suman hai.")
L3 = ("agent", "Suman, mujhe maloom nahi ke problem kya hai, isliye main complaint darj nahi kar "
               "sakti. Kya aap mujhe exact message batayengi jo gaari show kar rahi hai?")
L9 = ("agent", "Pehli dafa hua hai, theek hai. Please mujhe apni gaari ka model aur registration "
               "number batayengi, aur yeh bhi ke gaari kitni chal chuki hai?")
L10 = ("caller", "Gadi auto 3 hai aur registration number 112255 hai aur gadi almost 40,000 km "
                 "chal chuki hai.")
L11 = ("agent", "Acha, gaari ATTO 3 hai, registration number 112255 hai, aur 40,000 kilometres "
                "chal chuki hai. Mujhe maloom nahi ke system kab tak shikayat darj karega, lekin "
                "main team ko bhej rahi hoon. Woh aapse rabta karenge, normally ek working day "
                "mein. Kya main aapki kisi aur baat mein madad kar sakti hoon?")


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
                          persona=AgentPersona(instructions=""), record=False)
    session._agent = _Ear()
    for who, text in lines:
        session._note(who, text)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    return session


@pytest.fixture(autouse=True)
def _clean():
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()
    yield
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()


def _text() -> str:
    return " ".join(prompt.build(persona="P", context="C").split())


# 1. The first reply
def test_a_complaint_is_met_with_an_apology_and_one_question():
    text = _text()
    assert "When a caller opens with a complaint, first say sorry for the trouble" in text
    assert "then ask one question about what happened" in text
    assert "Never say you do not know what the problem is" in text


# 2. "Team ko bhej rahi hoon" with nothing recorded
@pytest.mark.parametrize("said", [
    L11[1],
    "Main aap ki shikayat team ko forward kar rahi hoon.",
    "Maine aap ki request team ko bhej di hai.",
    "Aap ki complaint team tak pohncha di hai.",
    "Your complaint has been forwarded to the team.",
    "Main yeh concerned team ko bhej rahi hoon.",
])
def test_sending_and_forwarding_claims_are_claims(said):
    assert live._claims_forwarded(said), said


@pytest.mark.parametrize("said", [
    "Kya main yeh team ko bhej doon?",
    "Agar aap chahen to main team ko bhej sakti hoon.",
    "Record hone ke baad team ko bhej di jayegi.",
    "Maine aap ko WhatsApp par details bhej di hain.",
    "Main abhi aap ko bhej rahi hoon.",
])
def test_offers_and_whatsapp_sends_are_not_forwarding_claims(said):
    assert not live._claims_forwarded(said), said


async def test_the_calls_forwarding_claim_gets_the_silent_nudge(caplog):
    call = _call()
    session = _watched(call, [L2, L3, L9, L10])
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note(*L11)
        await asyncio.sleep(0.05)
    assert session._agent.notes == [(live.NOTHING_SENT_CORRECTION, False)]
    assert session._agent.said == []
    note = live.NOTHING_SENT_CORRECTION
    assert note.startswith(live.NOTE_PREFIX)
    assert "Nothing has been recorded or sent. Ask for any missing details (mobile first), " \
           "read back, then call record_request." in note
    [warning] = [r.getMessage() for r in caplog.records if "claimed a request was" in r.getMessage()]
    assert "sent on" in warning and "Mujhe maloom nahi" in warning


async def test_a_forwarding_claim_with_a_reference_is_left_alone():
    call = _call()
    session = _watched(call, [L2, L10])
    live._recorded[call.id] = ["CMP-20261112-01"]
    session._note("agent", "Aap ki shikayat team ko bhej di hai, reference CMP-20261112-01.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


def test_the_prompt_forbids_sent_or_forwarded_without_a_reference():
    text = _text()
    assert ("Never say a request has been sent, forwarded or passed to the team unless "
            "record_request returned a reference on this call") in text


# 3. Three details in one turn
def test_the_prompt_keeps_optional_details_out_of_a_question():
    text = _text()
    assert "Never add an optional detail (mileage, occupation, when they plan to buy) to a question about required ones" in text
    assert "Ask for at most two details per turn, in one question" in text
    assert "Include every detail the caller has already given on this call" in tools.RECORD_REQUEST["description"]
