"""
From the fourth real test call: the booking that was never recorded.

The caller gave a name, a number, a city and a day for an ATTO 3 test
drive. The agent said "request record kar loongi" (I will record) and
"Request record karne ke liye" (in order to record), both taken for claims
of having recorded; each correction told it to say "not recorded yet", so
it said that aloud and asked to read back again, twice, after the caller
had already said "theek hai" to a full read-back. record_request was never
called. Then a "nahin" to "Kya main aap ko padh kar sunaoon?" let it end
the call as "the caller said goodbye".
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.repositories import requests as request_repo
from app.services.agent import live, phones, prompt, tools, turns
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

# The call, line by line, as transcribed.
L7 = ("agent", "ATTO 3 ki test drive book kar leti hoon. Kya aap mujhe apna naam aur mobile "
               "number batana chahengi? Phir main aap se preferred din aur waqt pooch loongi.")
L8 = ("caller", "Ali of number 03001234567")
L9 = ("agent", "Aap ne mobile number bataya hai, zero three zero zero one two three four five "
               "six seven. Kya yeh sahi hai? Aur aap kis din aur waqt drive karna chahte hain?")
L10 = ("caller", "TSI here or main Lahore or hafte wale din karna chahta hun.")
L11 = ("agent", "Lahore mein hafte wale din test drive, samajh gayi. Mazeed details collect "
                "karne ke liye main aap se do sawal aur poochongi. Kya aap mujhe apni "
                "occupation aur gaari lene ka irada batana chahenge? Yeh zaroori nahin hai. "
                "Uske baad main request record kar loongi. Aur drive ke liye aap ki umr 21 "
                "saal se zyada aur original driving licence aur C-N-I-C ki zaroorat hogi jo "
                "aap ne saath lana hai. Kya aap mujhe bata sakte hain?")
L12 = ("caller", "nahin, yah main baad mein disaaid karoonga.")
L13 = ("agent", "Koi baat nahin, Ali. Kya aap mujhe sirf itna bata sakte hain ke aap gaari kab "
                "tak lene ka irada rakhte hain? Do teen mahine mein ya us se zyada waqt "
                "lagega? Yeh bhi bata den to request record kar loongi.")
L14 = ("caller", "yah main gaari ki test draaiw ke baad dikhaaoonga.")
L15 = ("agent", "Ali, aap ki request abhi record nahin hui. Kya aap mujhe yeh details drive ke "
                "baad batana chahenge? Main aap ki baat samajh gayi. Kya main aap ke liye test "
                "drive book kar loon? Booking confirm karne ke liye mujhe yeh kehna zaroori hai "
                "ke, main ne details aap ko padh kar sunaein aur aap ne tasdeeq ki. Jab aap "
                "kahenge ke details sahi hain, tabhi main booking record kar sakti hoon. Kya "
                "main aap ko padh kar sunaoon?")
L16 = ("caller", "Sí.")
L17 = ("agent", "Ali, main ne details aap ko padh kar sunaein, kya yeh sahi hain? Aap tasdeeq "
                "karenge tabhi main request record kar sakti hoon. Kya main aap ko padh kar "
                "sunaoon?")
L18 = ("caller", "ji.")
L19 = ("agent", "Aap ne details confirm ki hain. Main read kar deti hoon: Aap ka naam Ali, "
                "mobile number zero three zero zero one two three four five six seven, city "
                "Lahore, aur aap hafte wale din ATTO 3 ki test drive karna chahte hain. Kya "
                "yeh sab sahi hai? Aap haan karenge tabhi main request record kar paoongi.")
L20 = ("caller", "theek hai.")
L21 = ("agent", "Aap ne tasdeeq ki, shukriya. Request record karne ke liye mujhe conditions "
                "batana zaroori hain. Kya main aap ko padh kar sunaoon?")
L22 = ("caller", "nahin.")
L23 = ("agent", "Ali, aap ki request abhi record nahin hui. Jab aap tasdeeq karenge, tabhi "
                "main isko record kar sakti hoon. Kya main aap ko padh kar sunaoon?")
L24 = ("caller", "Aí, não é, lá, filho.")
L25 = ("agent", "Koi baat nahin, Ali. BYD Pakistan ko call karne ka shukriya. Allah hafiz.")

BOOKING = [L7, L8, L9, L10, L11, L12, L13, L14, L15, L16, L17, L18, L19, L20, L21, L22, L23, L24]
DETAILS = {
    "type": "test_drive", "name": "Ali", "mobile": "03001234567", "city": "Lahore",
    "vehicle_model": "ATTO 3", "preferred_times": ["hafte wale din"], "details": "test drive",
}
GOODBYE = ("caller", "nahin, shukriya, Allah hafiz.")


def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                  counterparty="+923011114604")
    return Call(**{**fields, **extra})


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", recordCalls=False)


class _Ear:
    """A fake engine that keeps what it was told and what it was asked to say."""

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
    """No real hang-ups, short windows, clean registries."""
    hung: list[str] = []
    ended: list[str] = []

    async def hang_up(_tenant, _call, reason, **_k):
        hung.append(reason)

    async def after_goodbye(_tenant, _call, reason):
        ended.append(reason)

    monkeypatch.setattr(live, "hang_up", hang_up)
    monkeypatch.setattr(live, "_hang_up_after_goodbye", after_goodbye)
    monkeypatch.setattr(live, "CLOSING_END_SECONDS", 0.3)
    monkeypatch.setattr(live, "TRANSCRIPT_WAIT_SECONDS", 0.2)
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()
    yield {"hung": hung, "ended": ended}
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()


def _rows(fake_db):
    return list(fake_db.tables.get(request_repo.TABLE, {}).values())


# ---------------------------------------------------------------------------
# 1. Only a completed claim counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    L13[1],                                            # "request record kar loongi"
    L21[1],                                            # "Request record karne ke liye"
    L11[1], L15[1], L17[1], L19[1], L23[1],
    "Main abhi record kar deti hoon.",
    "Main aap ki request record kar sakti hoon.",
    "Main request record karoongi.",
    "Aap ki booking record kar li jayegi.",
    "Your request will be recorded once you confirm.",
    "Aap tasdeeq karenge tabhi main record kar sakti hoon.",
])
def test_a_plan_an_offer_or_a_purpose_is_not_a_claim(said):
    assert not live._claims_recorded(said), said


@pytest.mark.parametrize("said", [
    "Aap ki request record kar li hai.",
    "Aap ki request record kar li hai, reference TD-20261112-01.",
    "Aapki complaint darj ho gayi hai.",
    "Your request has been recorded.",
    "Test drive book kar liya hai.",
])
def test_a_completed_claim_is_a_claim(said):
    assert live._claims_recorded(said), said


async def test_the_calls_false_alarms_no_longer_fire(quiet, caplog):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        for who, text in BOOKING:
            session._note(who, text)
        await asyncio.sleep(0.05)
    assert session._agent.notes == [] or all(
        n == live.RECORD_NOW_NOTE for n in session._agent.notes)
    assert not any("claimed a request was recorded" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. Read-back, "theek hai", record
# ---------------------------------------------------------------------------

async def test_the_agents_own_read_back_and_a_yes_record_at_once(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L7, L8, L9, L10, L19, L20])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {**DETAILS, "confirmed": True})
    assert re.match(r"^Recorded\. Reference TD-\d{8}-01\. ", answer), answer
    [row] = _rows(fake_db)
    assert row["data"]["mobile"] == "+923001234567"
    assert row["data"]["preferredTimes"] == ["hafte wale din"]


async def test_the_same_request_is_not_recorded_twice(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L7, L8, L9, L10, L19, L20])
    handle = live._tool_handler(_tenant(), call)
    first = await handle("record_request", {**DETAILS, "confirmed": True})
    again = await handle("record_request", {**DETAILS, "confirmed": True})
    reference = re.search(r"TD-\d{8}-\d{2}", first).group(0)
    assert again.startswith(f"Already recorded. Reference {reference}.")
    assert len(_rows(fake_db)) == 1


async def test_a_yes_to_the_read_back_tells_the_agent_to_record_now(quiet, caplog):
    call = _call()
    session = _session(call.id, [L7, L8, L9, L10, L19])
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note("caller", L20[1])
        await asyncio.sleep(0.05)
    assert session._agent.notes == [live.RECORD_NOW_NOTE]
    assert any("told to record the request now" in r.getMessage() for r in caplog.records)
    session._note("caller", " ji")                       # more of the same turn
    await asyncio.sleep(0.05)
    assert len(session._agent.notes) == 1


async def test_a_yes_to_a_whatsapp_number_read_back_is_not_a_request(quiet):
    call = _call()
    session = _session(call.id, [
        ("agent", "Main details WhatsApp par bhej deti hoon. Kaunsa number?"),
        ("caller", "0300 123 4567"),
        ("agent", "Zero three zero zero, one two three, four five six seven. Is number par "
                  "WhatsApp message bhej doon?"),
    ])
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    session._note("caller", "ji bilkul")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


async def test_the_read_back_reply_is_an_instruction_not_a_script(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L7, L8, L9, L10])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {**DETAILS, "confirmed": False})
    assert answer.startswith("Not recorded yet. Read this back once")
    assert "no second read-back" in answer and "no asking permission" in answer
    assert "no extra conditions" in answer and "confirmed true" in answer
    for banned in ("zaroori", "conditions batana", "kehna"):
        assert banned not in answer


# ---------------------------------------------------------------------------
# 3. Optional details, once
# ---------------------------------------------------------------------------

async def test_declined_optional_details_are_not_asked_for_again(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L7, L8, L9, L10, L11, L12])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {**DETAILS, "confirmed": False})
    assert "Ask next for" not in answer, answer
    assert "Read this back" in answer and "Do not ask for optional details" in answer
    for word in ("occupation", "timeline", "irada", "planning to buy"):
        assert word not in answer, answer


def test_the_tool_and_the_prompt_say_so():
    description = tools.RECORD_REQUEST["description"]
    assert "If the caller declines or skips an optional detail, never ask for it again" in description
    assert "call this with confirmed true at once: no second read-back" in description
    text = " ".join(prompt.build(persona="P", context="C").split())
    assert "If the caller declines or skips one, never ask for it again" in text
    assert "no second read-back, no asking permission to read back" in text


# ---------------------------------------------------------------------------
# 4. Ending
# ---------------------------------------------------------------------------

def test_a_no_to_shall_i_read_it_back_does_not_end_the_call():
    assert not turns.may_end([L23, L22])
    assert not turns.may_end([L23, L22, L24])
    assert not turns.may_end([L21, L22])
    # The ends that still always count.
    assert turns.may_end([L23, GOODBYE])
    assert turns.may_end([L23, ("caller", "call end kar dein")])
    assert turns.may_end([("agent", "Aur koi sawal?"), ("caller", "nahin")])
    assert turns.may_end([L25])


async def test_the_models_goodbye_claim_is_checked_against_the_callers_words(quiet, caplog):
    call = _call()
    live._sessions[call.id] = _session(call.id, [
        ("agent", "Kya aap Lahore mein rehte hain?"), ("caller", "nahin.")])
    handle = live._tool_handler(_tenant(), call)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        answer = await handle("end_call", {"reason": "The caller said goodbye."})
    assert answer == live.NOT_GOODBYE
    assert quiet["ended"] == []
    [line] = [r.getMessage() for r in caplog.records if "said the caller said goodbye" in r.getMessage()]
    assert "'nahin.'" in line


async def test_no_end_from_the_model_while_the_booking_is_in_progress(quiet, caplog):
    call = _call()
    session = _session(call.id, BOOKING)
    live._sessions[call.id] = session
    handle = live._tool_handler(_tenant(), call)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        first = await handle("end_call", {"reason": "The caller said goodbye."})
        second = await handle("end_call", {"reason": "The caller said goodbye."})
    assert first == second == live.REQUEST_PENDING
    assert quiet["ended"] == []
    assert any("a request is in progress" in r.getMessage() for r in caplog.records)
    # An explicit goodbye ends it, request or no request.
    session._note(*GOODBYE)
    answer = await handle("end_call", {"reason": "The caller said goodbye."})
    await asyncio.sleep(0)
    assert "The call is ending" in answer
    assert quiet["ended"] == ["The caller said goodbye."]


async def test_asking_to_end_the_call_also_ends_it(quiet):
    call = _call()
    live._sessions[call.id] = _session(call.id, BOOKING + [("caller", "bas, call end kar dein")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("end_call", {"reason": "the caller asked to end the call"})
    assert "The call is ending" in answer


async def test_a_caller_gone_quiet_mid_request_can_still_be_let_go(quiet, caplog):
    call = _call()
    session = _session(call.id, BOOKING)
    live._sessions[call.id] = session
    handle = live._tool_handler(_tenant(), call)
    session.caller_spoke_at = time.time()                # heard just now
    assert await handle("end_call", {"reason": "the caller is not engaging"}) == live.REQUEST_PENDING
    session.caller_spoke_at = time.time() - live.SILENT_CALLER_SECONDS - 1
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        answer = await handle("end_call", {"reason": "the caller is not engaging"})
    assert "The call is ending" in answer
    assert any("has said nothing for" in r.getMessage() for r in caplog.records)


async def test_no_end_from_the_server_while_the_booking_is_in_progress(quiet, caplog):
    call = _call()
    session = _session(call.id, BOOKING)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note(*L25)                              # the closing line
        await asyncio.sleep(0.8)
    assert quiet["hung"] == []
    assert any("not ending the call from the server: a request is in progress" in r.getMessage()
               for r in caplog.records)
    # With a goodbye from the caller, the server end is back.
    session._note(*GOODBYE)
    session._note(*L25)
    await asyncio.sleep(0.8)
    assert quiet["hung"] == [live.CLOSING_END_REASON]


async def test_a_recorded_request_frees_the_end(quiet, fake_db):
    call = _call()
    session = _session(call.id, [L7, L8, L9, L10, L19, L20])
    live._sessions[call.id] = session
    handle = live._tool_handler(_tenant(), call)
    assert (await handle("record_request", {**DETAILS, "confirmed": True})).startswith("Recorded.")
    session._note("agent", "Reference TD mil gaya. Aur koi sawal?")
    session._note("caller", "nahin, bas.")
    answer = await handle("end_call", {"reason": "the caller said there was nothing else"})
    assert "The call is ending" in answer


def test_what_counts_as_a_request_in_progress():
    tenant = _tenant()
    assert live._request_in_progress("c", BOOKING, {}, tenant)
    assert live._request_in_progress("c", [], {"request": "{...}"}, tenant)
    assert live._request_in_progress("c", [], {"request_started": True}, tenant)
    # A number given for a message, and the message sent, is not a request.
    sent = {"confirmed": {"+923001234567"}}
    assert not live._request_in_progress("c", BOOKING, sent, tenant)
    # No number yet: nothing to protect.
    assert not live._request_in_progress("c", [L7], {}, tenant)
    # Just a chat about prices.
    assert not live._request_in_progress("c", [("agent", "SEAL ki price 1 crore 47 lakh hai."),
                                               ("caller", "theek hai")], {}, tenant)


# ---------------------------------------------------------------------------
# 5. The notes are never spoken
# ---------------------------------------------------------------------------

async def test_the_correction_is_a_silent_instruction(quiet):
    call = _call()
    session = _session(call.id)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    session._note("agent", "Aap ki request record kar li hai.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == [live.NOT_RECORDED_CORRECTION]
    assert session._agent.said == []
    for note in (live.NOT_RECORDED_CORRECTION, live.RECORD_NOW_NOTE):
        assert note.startswith(live.NOTE_PREFIX)
        assert "not for the caller" in note and "do not read this out" in note
        assert "Say it is not recorded" not in note
        assert "abhi record nahin hui" not in note


def test_numbers_read_back_in_words_are_recognised():
    assert phones.read_back_in(L19[1], "03001234567")
    assert phones.read_back_in(L9[1], "+923001234567")
    assert phones.read_back_in("sifar teen sifar sifar ek do teen chaar paanch chhe saat", "03001234567")
    assert not phones.read_back_in("Kya main aap ko padh kar sunaoon?", "03001234567")
    assert not phones.read_back_in("do teen mahine mein", "03001234567")
    assert phones.spoken_digits("bhej do, ek minute") == "bhej do, ek minute"


async def test_the_tool_state_is_cleared_when_the_call_is_settled(quiet, fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id)
    live._tool_handler(_tenant(), call)
    assert call.id in live._pending
    await live.finish(_tenant(), call.id, "test over")
    assert call.id not in live._pending and call.id not in live._recorded
