"""
Ending the call, read from what the caller actually said.

The exact lines from two real calls, in which the agent refused to end four
times and once: "ji nahin, bahut shukriyaa", "ji naheen.", "Allah Hafiz.",
"ji haan, bilkula. Call and ki nikal rahi." and "ji, kuchh nahin poochhna
chaahtaa. aap kol end kar den." — the live transcription had written them
in Devanagari, which the guard could not read, and the letter-by-letter
Roman that was stored spells "nahin" and "naheen" both ways.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, prompt, script, turns
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

ANYTHING_ELSE = "Kya iske alawa koi aur baat poochhna chahte hain?"
ANSWER = "Shared parking mein installation ke liye unki manzuri zaroori hai."
CLOSING = "Theek hai. BYD Pakistan ko call karne ka shukriya. Allah hafiz."
ASKED_TO_END = "Allah hafiz. Kya main call end kar doon?"

#: (the agent's turn before, the caller's words) — as they were on the calls.
ENDINGS = [
    (ANSWER, "ji nahin, bahut shukriyaa"),
    (ANSWER, "Ji nahi, bohat shukriya."),
    (ANYTHING_ELSE, "ji naheen."),
    (ANYTHING_ELSE, "Ji nahi."),
    (CLOSING, "Allah Hafiz."),
    (ASKED_TO_END, "ji haan, bilkula. Call and ki nikal rahi."),
    (ASKED_TO_END, "Ji haan, bilkul. Call and ki nikal rahi."),
    (ANYTHING_ELSE, "ji, kuchh nahin poochhna chaahtaa. aap kol end kar den."),
    (ANYTHING_ELSE, "Ji, kuch nahi poochna chahta. Aap call end kar dein."),
    # The same in Devanagari, as the recogniser wrote them live.
    (ANSWER, "जी नहीं, बहुत शुक्रिया"),
    (ANYTHING_ELSE, "जी नहीं।"),
    (CLOSING, "अल्लाह हाफ़िज़।"),
    (CLOSING, "अल्लाह हाफिज।"),
    (ASKED_TO_END, "जी हाँ, बिल्कुल। कॉल एंड कर दें।"),
    (ANYTHING_ELSE, "जी, कुछ नहीं पूछना चाहता। आप कॉल एंड कर दें।"),
    # And in Urdu script.
    (ANSWER, "جی نہیں، بہت شکریہ"),
    (ANYTHING_ELSE, "جی نہیں۔"),
    (CLOSING, "اللہ حافظ۔"),
    (ASKED_TO_END, "جی ہاں، بالکل۔ کال اینڈ کر دیں۔"),
    (ANYTHING_ELSE, "جی، کچھ نہیں پوچھنا چاہتا۔ آپ کال اینڈ کر دیں۔"),
    # English.
    (ANSWER, "No, that's all, thank you."),
    (CLOSING, "Bye."),
    (ANSWER, "Ok you can end the call now."),
]


class _Line:
    def __init__(self, transcript, spoke_ago=None):
        self.transcript = list(transcript)
        self.caller_spoke_at = 0.0 if spoke_ago is None else time.time() - spoke_ago

    def caller_spoke_within(self, seconds):
        return bool(self.caller_spoke_at) and time.time() - self.caller_spoke_at <= seconds


@pytest.fixture
def ending(monkeypatch):
    hung: list[str] = []

    async def hang_up_after_goodbye(_tenant, _call, reason):
        hung.append(reason)

    monkeypatch.setattr(live, "_hang_up_after_goodbye", hang_up_after_goodbye)
    call = Call(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923001112222")
    yield Tenant(phoneNumberId="t-1", name="Acme"), call, hung
    live._sessions.pop(call.id, None)


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_before, said", ENDINGS)
def test_every_real_ending_is_read_as_the_caller_being_done(agent_before, said):
    assert turns.may_end([("agent", agent_before), ("caller", said)]), said


@pytest.mark.parametrize("said", [
    "Please 100% kar dijiye, kindly.", "ji bilkul yahi nambar hai", "Ji bilkul yahi number hai.",
    "teek hai", "haa ji", "bilkula", "ji haan", "जी हाँ, बिल्कुल", "جی بالکل", "yes please",
    "kar dijiye", "sau fisad",
])
def test_every_way_of_saying_yes_counts(said):
    assert turns.is_affirmative(said), said


@pytest.mark.parametrize("said", ["nahi", "ji nahin", "naheen", "nai", "नहीं", "نہیں", "no", "mat bhejo"])
def test_every_way_of_saying_no_counts(said):
    assert turns.is_negative(said) and not turns.is_affirmative(said), said


@pytest.mark.parametrize("said", [
    "aap call end kar den", "call band kar dein", "phone rakh dein", "kol end kar do",
    "Call and ki nikal rahi", "end the call please", "कॉल बंद कर दें", "کال بند کر دیں",
])
def test_asking_to_end_is_recognised_in_every_form(said):
    assert turns.asked_to_end(said), said
    assert turns.is_closing(said), said


def test_a_question_after_the_closing_line_keeps_the_call_open():
    assert not turns.may_end([("agent", CLOSING), ("caller", "aur SEAL ki price kya hai?")])
    assert turns.may_end([("agent", CLOSING)])
    assert turns.may_end([("agent", CLOSING), ("caller", "theek hai")])


def test_judged_text_never_carries_a_number():
    assert turns.judged("mera number 03001234567 hai, bas") == "mera number … hai, bas"
    assert len(turns.judged("x" * 500)) == 120


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

async def _end(tenant, call, reason="the caller said goodbye", handle=None):
    handle = handle or live._tool_handler(tenant, call)
    answer = await handle("end_call", {"reason": reason})
    await asyncio.sleep(0)
    return answer, handle


@pytest.mark.parametrize("agent_before, said", ENDINGS)
async def test_end_call_is_allowed_for_every_real_ending(ending, agent_before, said):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", agent_before), ("caller", said)])
    answer, _ = await _end(tenant, call)
    assert "The call is ending" in answer, said
    assert hung == ["the caller said goodbye"]


async def test_a_second_end_call_is_always_allowed(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("caller", "SEAL ki range kitni hai?"),
                                     ("agent", "510 kilometre. Allah hafiz.")])
    first, handle = await _end(tenant, call, "the caller's question was answered")
    assert first == live.NOT_YET and hung == []
    second, _ = await _end(tenant, call, "the caller's question was answered", handle)
    assert "The call is ending" in second
    assert hung == ["the caller's question was answered"]


def test_the_refusal_says_only_to_answer_and_close():
    text = live.NOT_YET.lower()
    assert "answer what they actually said" in text
    assert "say the closing line once and call end_call again" in text
    for banned in ("anything else", "whether to end", "fallback", "whatsapp", "koi aur"):
        assert banned not in text, banned


async def test_the_refusal_logs_what_the_caller_said_without_the_number(ending, caplog):
    tenant, call, _hung = ending
    live._sessions[call.id] = _Line([("caller", "mera number 03001234567 hai, aur price kya hai?")])
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        answer, _ = await _end(tenant, call)
    assert answer == live.NOT_GOODBYE          # the reason claimed a goodbye; the words did not
    [line] = [r.getMessage() for r in caplog.records if "last words were" in r.getMessage()]
    assert "aur price kya hai?" in line and "03001234567" not in line
    answer, _ = await _end(tenant, call, reason="the caller's question was answered",
                           handle=live._tool_handler(tenant, call))
    assert answer == live.NOT_YET


async def test_the_guard_waits_for_the_callers_last_words_to_be_transcribed(ending):
    """The goodbye had been said; its text had not arrived yet."""
    tenant, call, hung = ending
    session = CallSession(call.id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._note("caller", "SEAL ki price?")
    session._note("agent", "1 crore 69 lakh 90 hazar. " + CLOSING)
    session.caller_spoke_at = time.time()          # they have just spoken...
    session.caller_transcribed_at = time.time() - 5  # ...and the text is not in yet
    live._sessions[call.id] = session

    async def transcription_arrives():
        await asyncio.sleep(0.3)
        session._note("caller", "Allah hafiz")

    asyncio.create_task(transcription_arrives())
    started = time.time()
    answer, _ = await _end(tenant, call)
    assert "The call is ending" in answer
    assert 0.25 <= time.time() - started < 1.5
    assert hung


async def test_the_wait_is_bounded(ending):
    tenant, call, _hung = ending
    session = CallSession(call.id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._note("caller", "price kya hai?")
    session.caller_spoke_at = time.time()
    session.caller_transcribed_at = time.time() - 5
    live._sessions[call.id] = session
    started = time.time()
    answer, _ = await _end(tenant, call)
    assert answer == live.NOT_GOODBYE
    assert 1.4 <= time.time() - started < 2.5


async def test_after_its_own_closing_line_the_agent_may_end(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("caller", "Ji nahi."), ("agent", CLOSING)])
    answer, _ = await _end(tenant, call)
    assert "The call is ending" in answer and hung


async def test_a_new_question_after_the_closing_line_is_answered_first(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", CLOSING), ("caller", "aur SEAL ki price kya hai?")])
    answer, _ = await _end(tenant, call)
    assert answer == live.NOT_GOODBYE and hung == []
    answer, _ = await _end(tenant, call, reason="the caller's question was answered",
                           handle=live._tool_handler(tenant, call))
    assert answer == live.NOT_YET and hung == []


# ---------------------------------------------------------------------------
# What the prompt says
# ---------------------------------------------------------------------------

def test_the_fallback_line_is_only_for_a_question_the_material_does_not_answer():
    text = " ".join(prompt.build(persona="You answer calls.", context="Fees: 50,000.",
                                 fallback_line="I do not have that detail in front of me.").split())
    assert "Only when the caller asks something your material does not answer, say this" in text
    assert "I do not have that detail in front of me." in text
    assert "Never say it at any other moment: not after a goodbye" in text
    assert "offer to take a message" not in text


def test_without_the_whatsapp_tool_the_prompt_says_so():
    without = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=False).split())
    withit = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True).split())
    assert "You cannot send WhatsApp on this call. Never offer it or agree to it" in without
    assert "Do not mention technical problems" in without
    assert "You cannot send WhatsApp on this call" not in withit


async def test_a_call_with_a_dead_token_is_told_whatsapp_is_off(fake_db, monkeypatch):
    from app.services import lines

    async def none(_tenant):
        return None

    monkeypatch.setattr(lines, "whatsapp_sender", none)
    call = Call(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923001112222")
    persona = await live.persona_for(Tenant(phoneNumberId="t-1", name="Acme"), call)
    assert "You cannot send WhatsApp on this call" in persona.instructions
    assert "send_whatsapp_message" not in [t["name"] for t in persona.tools]


def test_the_ending_rules_say_goodbye_means_end_now():
    text = " ".join(prompt.build(persona="P", context="").split())
    assert "If they say goodbye or ask you to end it, say the closing line once and call end_call" in text
    assert "do not ask anything first" in text


# ---------------------------------------------------------------------------
# Model meta-phrases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said, clean", [
    ("Aap ka bhi Allah hafiz. BYD Pakistan ko call karne ka shukriya. Final output generated.",
     "Aap ka bhi Allah hafiz. BYD Pakistan ko call karne ka shukriya."),
    ("Final answer: SEAL Premium 1 crore 69 lakh.", "SEAL Premium 1 crore 69 lakh."),
    ("Output: 510 kilometre. [END]", "510 kilometre."),
    ("Theek hai. </output>", "Theek hai."),
    ("Nothing to strip here.", "Nothing to strip here."),
])
def test_meta_phrases_are_stripped(said, clean):
    assert script.strip_meta(said) == clean


def test_the_stored_transcript_and_the_shown_one_have_no_meta_phrases():
    session = CallSession("c", send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._note("caller", "Allah hafiz.")
    session._note("agent", "Aap ka bhi Allah hafiz.")
    session._note("agent", " Final output generated.")
    assert session.transcript_text() == "caller: Allah hafiz.\nagent: Aap ka bhi Allah hafiz."
    assert script.readable("agent: Shukriya. Final output generated.") == "agent: Shukriya."
