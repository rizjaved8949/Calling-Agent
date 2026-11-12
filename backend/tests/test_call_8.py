"""
From the eighth real call: a complaint recorded in the end, the hard way.

The ten-digit note arrived after the read-back, twice; a function call was
spoken ("calculate{expression:1Shukriya..."); a missing detail became
"shikayat darj nahi ho saki"; the model the caller had given was asked for
again; the details were read back twice with a permission question in
between; "ATTO 3 Advance" was added; the closing line was said twice.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import numpy as np
import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.repositories import requests as request_repo
from app.services.agent import live, prompt, script, tools
from app.services.agent.audio_rate import FRAME_BYTES
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

L2 = ("caller", "meri Auto 3 chaarj nahin ho rahi, shikaayat darj karni hai. mera naam suman hai.")
L3 = ("agent", "Suman, sun kar maazrat chahti hoon keh gaari charge nahi ho rahi. Kya aap mujhe "
               "apna mobile number batayengi? Uske baad main aapki shikayat darj kar sakti hoon.")
L4 = ("caller", "0300123456")
L5 = ("agent", "Aap ka mobile number hai zero three double zero, one two three four five six? Kya "
               "yeh number theek hai?")
L8 = ("caller", "0300134567")
L12 = ("caller", "03001234567")
L13 = ("agent", "calculate{expression:1Shukriya. Aap ka mobile number hai zero three double zero, "
                "one two three four five six seven? Kya yeh number theek hai?")
L15 = ("agent", "Maazrat chahti hoon, shikayat darj nahi ho saki. Kya aap mujhe apni gaari ka "
                "model ya registration number batayengi? Taake main dobara koshish kar sakoon?")
L16 = ("caller", "Gadi ka registration number 112255 hai. Aur main model aapko pahle hi bata "
                 "chuka hun.")
L30 = ("caller", "Nahin shukriya, Allah Hafiz.")
CLOSING = "BYD Pakistan ko call karne ka shukriya. Allah hafiz."


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


def _session(call_id: str, lines=()) -> CallSession:
    session = CallSession(call_id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=""), record=False)
    session._agent = _Ear()
    for who, text in lines:
        session._note(who, text)
    return session


def _watched(call: Call, lines=()) -> CallSession:
    session = _session(call.id, lines)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    return session


def _voice(dbfs: float = -25.0) -> bytes:
    samples = FRAME_BYTES // 2
    amplitude = 10 ** (dbfs / 20) * 32767
    return (np.sin(np.linspace(0, 40 * np.pi, samples)) * amplitude).astype(np.int16).tobytes()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(live, "MOBILE_NOTE_DELAY_SECONDS", 0.3)
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()
    yield
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()


def _text() -> str:
    return " ".join(prompt.build(persona="P", context="C").split())


def _rows(fake_db):
    return list(fake_db.tables.get(request_repo.TABLE, {}).values())


# ---------------------------------------------------------------------------
# 4. The ten-digit note, before the read-back
# ---------------------------------------------------------------------------

async def test_the_note_goes_out_the_moment_the_callers_turn_is_finished(caplog):
    call = _call()
    session = _watched(call, [L2, L3])
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note(*L4)
        session._note("caller:done", "")               # the recogniser: that turn is done
        await asyncio.sleep(0.02)                      # no delay: well under the fallback's
    assert session._agent.notes == [(live.MOBILE_LENGTH_NOTE.format(n=10), True)]
    assert session._agent.said == []
    assert any("10 digits" in r.getMessage() for r in caplog.records)
    # The second short number, later in the call, is counted too.
    session._note("agent", L5[1])
    session._note("caller", "ji haan.")
    session._note("agent", "Maazrat, is number mein das digits hain. Dobara bata sakti hain?")
    session._note(*L8)
    session._note("caller:done", "")
    await asyncio.sleep(0.02)
    assert session._agent.notes[-1] == (live.MOBILE_LENGTH_NOTE.format(n=10), True)
    # And the complete number gets nothing.
    session._note("agent", "Dobara batayein?")
    session._note(*L12)
    session._note("caller:done", "")
    await asyncio.sleep(0.5)
    assert len(session._agent.notes) == 2


async def test_the_fallback_delay_still_counts_when_no_finish_arrives():
    call = _call()
    session = _watched(call, [L2, L3])
    session._note(*L4)
    await asyncio.sleep(0.05)
    assert session._agent.notes == []
    await asyncio.sleep(0.5)
    assert session._agent.notes == [(live.MOBILE_LENGTH_NOTE.format(n=10), True)]


async def test_an_urgent_note_closes_the_turn_on_gemini():
    from app.services.agent.gemini import GeminiLiveSession

    sent: list[tuple[str, bool]] = []

    class FakeLive:
        async def send_client_content(self, *, turns, turn_complete):
            sent.append((turns.parts[0].text, turn_complete))

    engine = GeminiLiveSession(LiveSettings(model="m", api_key="k"), AgentPersona(instructions=""))
    engine._session = FakeLive()
    await engine.note("a quiet note")
    await engine.note("a loud one", urgent=True)
    assert sent == [("a quiet note", False), ("a loud one", True)]


def test_the_engine_marks_the_callers_turn_finished():
    from app.services.agent.gemini import GeminiLiveSession

    seen: list[tuple[str, str]] = []
    engine = GeminiLiveSession(LiveSettings(model="m", api_key="k"), AgentPersona(instructions=""),
                               on_transcript=lambda who, text: seen.append((who, text)))

    class Heard:
        def __init__(self, text, finished):
            self.text, self.finished = text, finished

    class Content:
        interrupted = False
        model_turn = None

        def __init__(self, heard):
            self.input_transcription = heard
            self.output_transcription = None

    class Message:
        tool_call = None

        def __init__(self, content):
            self.server_content = content

    class FakeLive:
        async def receive(self):
            yield Message(Content(Heard("0300", False)))
            yield Message(Content(Heard("123456", True)))
            engine._closed = True

    asyncio.run(engine._receive(FakeLive()))
    assert seen == [("caller", "0300"), ("caller", "123456"), ("caller:done", "")]


def test_the_prompt_says_count_before_reading_back():
    text = _text()
    assert "Count the digits before you read a number back" in text


# ---------------------------------------------------------------------------
# 5. A function call spoken
# ---------------------------------------------------------------------------

def test_the_spoken_function_call_is_taken_out():
    cleaned, count = script.strip_tool_calls(L13[1])
    assert count == 1
    assert cleaned == ("Shukriya. Aap ka mobile number hai zero three double zero, one two three "
                       "four five six seven? Kya yeh number theek hai?")
    assert script.strip_tool_calls("calculate{expression: 8990000-1750000} Baaki 72 lakh 40 hazar.") \
        == ("Baaki 72 lakh 40 hazar.", 1)
    assert script.strip_tool_calls("Main record_request(type=complaint) karti hoon.") \
        == ("Main karti hoon.", 1)
    assert script.strip_tool_calls("Main hisaab calculate kar leti hoon.") \
        == ("Main hisaab calculate kar leti hoon.", 0)
    assert script.strip_meta(L13[1]).startswith("Shukriya. Aap ka mobile")


async def test_the_session_strips_it_and_warns(caplog):
    call = _call()
    session = _session(call.id, [L12])
    with caplog.at_level(logging.WARNING, logger="app.services.agent.session"):
        session._note(*L13)
    assert session.transcript[-1] == ("agent", "Shukriya. Aap ka mobile number hai zero three "
                                      "double zero, one two three four five six seven? Kya yeh "
                                      "number theek hai?")
    assert "calculate" not in session.transcript_text()
    [warning] = [r.getMessage() for r in caplog.records if "spoke a function call" in r.getMessage()]
    assert "taken out of the transcript" in warning


def test_the_prompt_forbids_tool_names_in_speech():
    text = _text()
    assert "Never say a tool's name or write a function call in your speech" in text
    assert "Tools are called silently; the caller hears only words" in text


# ---------------------------------------------------------------------------
# 6. A missing detail is not a failure
# ---------------------------------------------------------------------------

async def test_a_missing_detail_is_an_instruction_not_a_failure(fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L2, L3, L12, ("caller", "theek hai.")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {"type": "complaint", "name": "Suman",
                                             "mobile": "03001234567", "details": L2[1]})
    assert answer.startswith("Ask next for the vehicle model or the registration number.")
    assert "Nothing has failed" in answer
    for wrong in ("Not recorded", "could not", "nahi ho saki"):
        assert wrong not in answer
    assert "failed" not in answer.replace("Nothing has failed", "")
    assert "Ask for these, at most two per turn, then call record_request again" in answer


def test_the_prompt_says_a_missing_detail_is_simply_asked_for():
    text = _text()
    assert "A missing detail is simply asked for, never announced as a failure" in text
    assert "'Ask next for' means nothing failed" in tools.RECORD_REQUEST["description"]


# ---------------------------------------------------------------------------
# 7. What the caller already said is kept
# ---------------------------------------------------------------------------

async def test_details_given_earlier_in_the_call_are_kept_by_the_tool(fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [L2, L3, L12, ("caller", "theek hai."), L15, L16])
    handle = live._tool_handler(_tenant(), call)
    first = await handle("record_request", {"type": "complaint", "name": "Suman",
                                            "vehicle_model": "ATTO 3", "details": L2[1]})
    assert first.startswith("Ask next for a mobile number.")
    # The model's second call drops the model name; the tool still has it.
    second = await handle("record_request", {"type": "complaint", "mobile": "03001234567",
                                             "registration": "112255"})
    assert "Ask next for" not in second, second
    assert "Read this back" in second and "for the ATTO 3" in second and "for Suman" in second
    assert "registration 112255" in second


def test_the_prompt_says_use_what_the_caller_said():
    text = _text()
    assert "Before asking for a detail, use what the caller already said on this call" in text
    assert "every record_request call includes every detail they have given so far" in text


# ---------------------------------------------------------------------------
# 8 and 9. Read back once, in the caller's words; never ask permission
# ---------------------------------------------------------------------------

def test_the_prompt_rules_for_the_read_back():
    text = _text()
    assert "Collect, read back once, the caller says yes, record" in text
    assert "Never ask permission to record" in text
    assert "If the caller corrects one detail, repeat only the corrected detail and record" in text
    assert "Read back exactly what the caller said. Never add a variant, a trim level or a detail they did not say" in text
    assert "Never ask permission to record" in tools.RECORD_REQUEST["description"]


# ---------------------------------------------------------------------------
# 10. The next step after a reference
# ---------------------------------------------------------------------------

async def test_a_recorded_complaint_tells_the_agent_to_give_the_next_step(fake_db):
    call = _call()
    live._sessions[call.id] = _session(call.id, [
        L2, L3, L12, ("caller", "theek hai."), L16,
        ("agent", "Suman, mobile zero three double zero, one two three four five six seven, "
                  "ATTO 3, registration 112255, charge nahi ho rahi. Kya yeh sahi hai?"),
        ("caller", "theek hai.")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {
        "type": "complaint", "name": "Suman", "mobile": "03001234567", "vehicle_model": "ATTO 3",
        "registration": "112255", "details": "charge nahi ho rahi", "confirmed": True})
    assert re.match(r"^Recorded\. Reference CMP-\d{8}-01\. ", answer)
    assert "give the next step from your material: who will contact them and by when" in answer
    assert "customer-care route" in answer
    assert "After giving a reference, tell the caller the next step from your material" in _text()


# ---------------------------------------------------------------------------
# 11. The closing line once
# ---------------------------------------------------------------------------

async def test_what_the_model_says_after_the_goodbye_is_held_and_dropped(monkeypatch, caplog):
    """The call's ending: the closing line, end_call, then the model said it
    again. The second one is held, never heard, and the hang-up drops it."""
    hung: list[str] = []

    async def hang_up(_tenant, _call, reason, **_k):
        hung.append(reason)

    monkeypatch.setattr(live, "hang_up", hang_up)
    monkeypatch.setattr(live, "GOODBYE_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(live, "TRANSCRIPT_WAIT_SECONDS", 0.1)
    call = _call()
    session = _session(call.id, [L30, ("agent", CLOSING)])
    live._sessions[call.id] = session
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("end_call", {"reason": "the caller said goodbye"})
    assert "The call is ending" in answer
    assert session.holding_after_goodbye
    # The model says the closing line again: audio and words both held.
    session._on_agent_audio(_voice(), 24000)
    session._note("agent", CLOSING)
    assert session._pacer.queued == 0
    assert session.transcript_text().count("Allah hafiz") == 1
    await asyncio.sleep(1.2)
    assert hung == ["the caller said goodbye"]
    assert session.transcript_text().count("Allah hafiz") == 1


async def test_an_answer_to_a_real_question_after_the_goodbye_is_released(monkeypatch):
    hung: list[str] = []

    async def hang_up(_tenant, _call, reason, **_k):
        hung.append(reason)

    monkeypatch.setattr(live, "hang_up", hang_up)
    monkeypatch.setattr(live, "GOODBYE_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(live, "TRANSCRIPT_WAIT_SECONDS", 0.1)
    call = _call()
    session = _session(call.id, [L30, ("agent", CLOSING)])
    live._sessions[call.id] = session
    handle = live._tool_handler(_tenant(), call)
    await handle("end_call", {"reason": "the caller said goodbye"})
    await asyncio.sleep(0.05)
    session.caller_spoke_at = time.time()                       # the caller speaks...
    session._note("caller", "ek minute, SEAL ki price kya hai?")  # ...and asks something
    for _ in range(6):
        session._on_agent_audio(_voice(), 24000)                 # the model answers
    session._note("agent", "SEAL ki price 1 crore 47 lakh 90 hazar hai.")
    assert session._pacer.queued == 0 and session.holding_after_goodbye
    await asyncio.sleep(1.2)
    assert hung == []
    assert not session.holding_after_goodbye
    assert session._pacer.queued > 0
    assert "1 crore 47 lakh" in session.transcript_text()


async def test_the_hold_is_not_set_when_no_closing_line_was_said(monkeypatch):
    monkeypatch.setattr(live, "_hang_up_after_goodbye", _no_hang_up)
    call = _call()
    session = _session(call.id, [("agent", "Aur koi sawal?"), ("caller", "nahin, bas.")])
    live._sessions[call.id] = session
    answer = await live._tool_handler(_tenant(), call)("end_call", {"reason": "nothing else"})
    assert "The call is ending" in answer
    assert not session.holding_after_goodbye


async def _no_hang_up(_tenant, _call, _reason):
    return None


# ---------------------------------------------------------------------------
# 12. The opening line
# ---------------------------------------------------------------------------

async def test_the_opening_line_is_the_agents_field_word_for_word(fake_db, monkeypatch):
    async def no_whatsapp(_tenant):
        return False

    monkeypatch.setattr(live, "_can_send_whatsapp", no_whatsapp)
    tenant = _tenant(agentGreeting="Thank you for contacting BYD Pakistan. How may I help you?",
                     language="ur-PK")
    persona = await live.persona_for(tenant)
    assert persona.greeting == "Thank you for contacting BYD Pakistan. How may I help you?"


async def test_without_an_opening_line_the_default_follows_the_language(fake_db, monkeypatch):
    async def no_whatsapp(_tenant):
        return False

    monkeypatch.setattr(live, "_can_send_whatsapp", no_whatsapp)
    urdu = await live.persona_for(_tenant(language="ur-PK", ttsVoice="Aoede"))
    assert urdu.greeting == ("Assalam o Alaikum, Acme mein khush aamdeed. Main aap ki kya madad "
                             "kar sakti hoon?")
    english = await live.persona_for(_tenant(language="en-GB"))
    assert english.greeting == "Thank you for calling Acme. How can I help you?"


def test_the_default_greeting_forms():
    assert prompt.default_greeting("Acme", "ur", "male") == (
        "Assalam o Alaikum, Acme mein khush aamdeed. Main aap ki kya madad kar sakta hoon?")
    assert prompt.default_greeting("", "ur", "") == "Assalam o Alaikum, Aap ki kya madad ho sakti hai?"
    assert prompt.default_greeting("", "", "") == "Thank you for calling. How can I help you?"
