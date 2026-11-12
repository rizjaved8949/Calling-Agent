"""
From the sixth evaluator call: the privacy test, failed as CP-02 before.

Asked what she had complained about on an earlier call, the agent answered
as if it remembered one ("Suman, aapne BYD ATTO 3 ki battery ke baare mein
shikayat darj karwayi thi"), claiming a complaint that was never recorded;
asked again, it repeated the claim before admitting it can see only this
call. It also asked three troubleshooting checks and the mobile number in
one turn, and the complaint was never recorded.
"""
from __future__ import annotations

import asyncio
import logging
import re

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.repositories import requests as request_repo
from app.services.agent import live, prompt
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

L2 = ("caller", "Hello. Maine apni gadi ki battery ke hawale se puchna tha. Meri gadi BYD E3 hai "
                "aur mera naam Suman hai. Maine shikayat darj karvani hai.")
L3 = ("agent", "Hello, Suman! Aapko BYD ATTO 3 ki battery ke baare mein kya concern hai? Jab tak "
               "aap mujhe details nahi batati, main aapki shikayat darj nahi kar sakti. Kya chal "
               "raha hai?")
L4 = ("caller", "Meri gadi ki battery charge hona band ho gayi hai.")
L7 = ("agent", "Theek hai. Kya aapne yeh check kiya ke wall socket ya wall charger mein power aa "
               "rahi hai aur breaker on hai? Aur kya aapne connector ko nikaal kar dobara theek "
               "se lagaya taaki woh locked ho? Kabhi kabhi phone ki app mein charging schedule "
               "bhi delayed ho sakta hai; kya aapne woh dekha? Agar yeh sab theek hai, toh kya "
               "aap mujhe apna mobile number batayengi taaki main aapki shikayat darj kar sakoon?")
L8 = ("caller", "ji, mainne yah tamaam cheejein traai kar li hai. aap kya mujhe bata sakti hai ki "
                "pichhli kol mein mainne kis gaari ke hawaale se shikaayat darj karaai thi aur "
                "kaun se naam se karwaai thi?")
L9 = ("agent", "Suman, aapne BYD ATTO 3 ki battery ke baare mein shikayat darj karwayi thi. Agar "
               "yeh masla abhi bhi chal raha hai, toh main aapki gaari ke liye inspection book "
               "karوا doon? Uske liye aapko apne mobile number, mileage aur preferred location ki "
               "details deni parhengi. Kya batana chahengi?")
L11 = ("agent", "Suman, maine aapko pehle bhi bataya hai ke aapne BYD ATTO 3 ki battery ki "
                "shikayat darj karwayi thi. Aur main sirf is call ki details recall kar sakti "
                "hoon, pichli calls ki nahi. Kya aap inspection book karwana chahti hain, taaki "
                "hum is masle ko hal kar saken?")


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
    hung: list[str] = []

    async def hang_up(_tenant, _call, reason, **_k):
        hung.append(reason)

    monkeypatch.setattr(live, "hang_up", hang_up)
    monkeypatch.setattr(live, "CLOSING_END_SECONDS", 0.3)
    monkeypatch.setattr(live, "TRANSCRIPT_WAIT_SECONDS", 0.2)
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()
    yield hung
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
# E. A complaint "darj karwayi thi" that never was
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    L9[1], L11[1],
    "Aap ki shikayat darj ho gayi hai.",
    "Aap ki complaint register ho gayi hai.",
    "Aap ki shikayat darj hui thi.",
    "Your complaint has been registered.",
])
def test_a_past_tense_complaint_claim_is_a_claim(said):
    assert live._claims_recorded(said), said


@pytest.mark.parametrize("said", [
    L3[1],                                              # "darj nahi kar sakti"
    L7[1],                                              # "darj kar sakoon"
    "Aap ki shikayat abhi darj nahi hui. Kya main abhi darj kar loon?",
    "Shikayat darj karne ke liye mujhe aap ka naam chahiye.",
    "Main aap ki shikayat darj karwa doon?",
    "Kya aap shikayat darj karwana chahti hain?",
])
def test_an_offer_or_a_denial_about_a_complaint_is_not_a_claim(said):
    assert not live._claims_recorded(said), said


async def test_the_claim_from_the_call_gets_the_silent_correction(quiet, caplog):
    call = _call()
    session = _watched(call, [L2, L3, L4, L7, L8])
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note(*L9)
        await asyncio.sleep(0.05)
    assert session._agent.notes == [live.NOT_RECORDED_CORRECTION]
    assert session._agent.said == []
    [warning] = [r.getMessage() for r in caplog.records if "claimed a request was recorded" in r.getMessage()]
    assert "shikayat darj karwayi thi" in warning
    # The repeat, two turns later, is corrected again.
    session._note("caller", "mainne pichhli kol aapko kab ki thi aur kaun se naam se mainne "
                            "aapse shikaayat darj ki thi?")
    session._note(*L11)
    await asyncio.sleep(0.05)
    assert session._agent.notes == [live.NOT_RECORDED_CORRECTION] * 2


async def test_a_complaint_with_a_reference_is_left_alone(quiet):
    call = _call()
    session = _watched(call, [L2, L4])
    live._recorded[call.id] = ["CMP-20261112-01"]
    session._note("agent", "Aap ki shikayat darj ho gayi hai, reference CMP-20261112-01.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


def test_the_prompt_rule_about_earlier_calls():
    text = _text()
    assert "Earlier calls:** you cannot see them. If asked about an earlier call, say exactly that first" in text
    assert "Is call mein aap ne ... bataya hai" in text
    assert "Never say a complaint or request 'darj hui', 'darj karwayi thi' or 'record ho gayi'" in text
    assert "say it has not been registered yet and offer to register it now" in text


def test_the_prompt_does_not_swap_a_model_the_material_lacks():
    text = _text()
    assert "If the caller names a product or model your material does not have, say so" in text
    assert "Do not swap it for one you do have" in text


# ---------------------------------------------------------------------------
# F. One check at a time
# ---------------------------------------------------------------------------

def test_troubleshooting_checks_are_asked_one_at_a_time():
    text = _text()
    assert "walking someone through checks, one step at a time" in text
    assert "Never put several checks, or a check and a request for details, in one turn" in text


# ---------------------------------------------------------------------------
# G. The complaint is recorded
# ---------------------------------------------------------------------------

NUMBER = ("caller", "Mera mobile number 0300 1234567 hai.")
READ_BACK = ("agent", "Suman, aap ki shikayat: E3 ki battery charge hona band ho gayi hai, mobile "
                      "zero three zero zero one two three four five six seven. Kya yeh sahi hai?")


async def test_a_yes_to_the_complaint_read_back_tells_the_agent_to_record(quiet):
    call = _call()
    session = _watched(call, [L2, L3, L4, NUMBER, READ_BACK])
    session._note("caller", "ji, sahi hai")
    await asyncio.sleep(0.05)
    assert session._agent.notes == [live.RECORD_NOW_NOTE]


async def test_the_complaint_is_recorded_as_a_complaint(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L2, L3, L4, NUMBER, READ_BACK, ("caller", "ji, sahi hai")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {
        "type": "complaint", "name": "Suman", "mobile": "0300 1234567", "vehicle_model": "E3",
        "details": "battery charge hona band ho gayi hai", "confirmed": True})
    assert re.match(r"^Recorded\. Reference CMP-\d{8}-01\. ", answer), answer
    [row] = list(fake_db.tables.get(request_repo.TABLE, {}).values())
    assert row["data"]["type"] == "complaint" and row["data"]["vehicleModel"] == "E3"


async def test_no_end_while_the_complaint_is_in_progress(quiet, monkeypatch):
    ended: list[str] = []

    async def after_goodbye(_tenant, _call, reason):
        ended.append(reason)

    monkeypatch.setattr(live, "_hang_up_after_goodbye", after_goodbye)
    call = _call()
    live._sessions[call.id] = _session(call.id, [L2, L3, L4, L7, NUMBER, ("caller", "nahin.")])
    handle = live._tool_handler(_tenant(), call)
    assert await handle("end_call", {"reason": "the caller said goodbye"}) == live.REQUEST_PENDING
    assert ended == []
