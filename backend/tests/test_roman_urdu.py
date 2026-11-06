"""
Urdu written down in Hindi script is shown as Roman Urdu.

From production: a caller spoke Urdu, the speech recogniser wrote it in
Devanagari, and the Unanswered page showed "बस मुझे इतना बता दें कि
रजिस्ट्रेशन…" to people who cannot read it. Other languages keep their own
script.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.call import Call, CallStatus, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, script
from app.services.agent.gemini import LiveSettings

ASKED = ("बस मुझे इतना बता दें कि रजिस्ट्रेशन, टोकन टैक्स और नंबर प्लेट "
         "के अख़राजात की कीमत क्या है?")
#: What a faithful respelling of ASKED looks like: every word kept, only spelled.
RESPELLED = ("Bas mujhe itna bata dein ke registration, token tax aur number "
             "plate ke akhrajaat ki qeemat kya hai?")
DEFERRED = ("Main maazrat chahti hoon, mere paas registration ke exact charges "
            "nahi hain. Aapko care centre par rabta karna parega.")


# ---------------------------------------------------------------------------
# Letter by letter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hindi, roman", [
    ("बस मुझे इतना बता दें", "bas mujhe itna bata dein"),
    ("मैं आपकी क्या मदद कर सकता हूँ", "main aapki kya madad kar sakta hoon"),
    ("समझ गया", "samajh gaya"),
    ("हैं में नहीं", "hain mein nahin"),
    ("कितने पैसे लगेंगे", "kitne paise lagenge"),
    ("ज़रूरत", "zaroorat"),
    ("नंबर", "nambar"),
    ("मेरा नाम अली है।", "mera naam ali hai."),
])
def test_hindi_script_reads_as_roman_urdu(hindi, roman):
    assert script.romanize(hindi) == roman


def test_the_question_from_production_has_no_hindi_script_left():
    roman = script.romanize(ASKED)
    assert not script.has_devanagari(roman)
    assert roman.startswith("bas mujhe itna bata dein ki ")
    assert roman.endswith("kya hai?")


@pytest.mark.parametrize("text", [
    "What are the registration charges?",
    "مجھے رجسٹریشن کی فیس بتائیں",          # Urdu in its own script
    "ما هي رسوم التسجيل؟",                     # Arabic
    "Registration ki fees kitni hai?",          # already Roman Urdu
    "",
])
def test_every_other_language_is_left_as_it_was(text):
    assert script.readable(text) == text


def test_a_mixed_line_only_changes_the_hindi_script():
    assert script.romanize("hello मेरा नाम Ali है") == "hello mera naam Ali hai"


# ---------------------------------------------------------------------------
# With the text model
# ---------------------------------------------------------------------------

async def test_the_model_spells_it_and_other_lines_are_untouched(monkeypatch):
    sent: list[list[str]] = []

    async def model(lines, **_k):
        sent.append(lines)
        return [RESPELLED]

    monkeypatch.setattr(script, "_ask_model", model)
    out = await script.to_roman_urdu(["Assalam o alaikum", ASKED], api_key="k", model="m")
    assert sent == [[ASKED]], "only the Hindi-script line goes to the model"
    assert out == ["Assalam o alaikum", RESPELLED]


@pytest.mark.parametrize("answer", [
    None,                                   # unreadable reply
    [],                                     # wrong length
    ["बस मुझे"],                            # still Hindi script
    [""],
])
async def test_a_bad_model_answer_falls_back_to_letter_by_letter(monkeypatch, answer):
    async def model(_lines, **_k):
        return answer

    monkeypatch.setattr(script, "_ask_model", model)
    [out] = await script.to_roman_urdu([ASKED], api_key="k", model="m")
    assert out == script.romanize(ASKED)


async def test_a_model_that_fails_or_hangs_falls_back(monkeypatch):
    async def broken(_lines, **_k):
        raise RuntimeError("OUT OF QUOTA")

    monkeypatch.setattr(script, "_ask_model", broken)
    assert await script.to_roman_urdu([ASKED], api_key="k", model="m") == [
        script.romanize(ASKED)]

    async def hangs(_lines, **_k):
        await asyncio.sleep(10)

    monkeypatch.setattr(script, "_ask_model", hangs)
    monkeypatch.setattr(script, "MODEL_TIMEOUT_SECONDS", 0.05)
    assert await script.to_roman_urdu([ASKED], api_key="k", model="m") == [
        script.romanize(ASKED)]


async def test_no_hindi_script_means_no_model_call(monkeypatch):
    async def model(_lines, **_k):
        raise AssertionError("called for nothing")

    monkeypatch.setattr(script, "_ask_model", model)
    assert await script.to_roman_urdu(["hello"], api_key="k", model="m") == ["hello"]


# ---------------------------------------------------------------------------
# The model may only change spelling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("original, respelled", [
    (ASKED, RESPELLED),
    ("अस्सलाम ओ अलैकुम, मैं आपकी क्या मदद कर सकती हूँ?",
     "Assalam o alaikum, main aap ki kya madad kar sakti hoon?"),
    ("ज़रूरत नहीं है शुक्रिया", "Zaroorat nahi hai shukriya"),
    ("जी मुझे डॉक्टर का टाइम चाहिए कल सुबह का",
     "Ji mujhe doctor ka time chahiye kal subah ka"),
    ("मुझे पाँच सौ रुपये देने हैं", "Mujhe paanch sau rupay dene hain"),
    ("मेरा नाम अली है, Order 4512", "Mera naam Ali hai, Order 4512"),
])
def test_a_respelling_is_accepted(original, respelled):
    assert script.faithful(original, respelled)


@pytest.mark.parametrize("original, changed, why", [
    ("मुझे पाँच सौ रुपये देने हैं", "Mujhe 500 rupay dene hain",
     "a spoken amount turned into a figure"),
    ("मुझे पाँच सौ रुपये देने हैं", "Mujhe chhe sau rupay dene hain",
     "a different amount"),
    ("मेरा नाम अली है, Order 4512", "Mera naam Ali hai, Order 4521",
     "a different order number"),
    ("मेरा नाम अली है, Order 4512", "Mera naam Ahmed hai, Order 4512",
     "a different name"),
    ("मेरा नाम अली है, Order 4512", "Mera naam Ali hai, Order",
     "a number dropped"),
    (ASKED, "Just tell me the cost of registration, token tax and number plate expenses?",
     "translated into English"),
    (ASKED, "Bas mujhe registration ki qeemat bata dein?",
     "shortened"),
    (ASKED, RESPELLED[:-1] + ", aur kitne din lagenge?",
     "words added"),
    (ASKED, RESPELLED[:-1] + ".",
     "a question turned into a statement"),
    (ASKED, "", "empty"),
    (ASKED, "बस मुझे इतना बता दें", "still Hindi script"),
])
def test_anything_more_than_a_respelling_is_refused(original, changed, why):
    assert not script.faithful(original, changed), why


async def test_a_changed_line_falls_back_and_a_faithful_one_is_kept(monkeypatch):
    """Judged line by line: one bad line does not cost the others."""
    amount = "मुझे पाँच सौ रुपये देने हैं"

    async def model(_lines, **_k):
        return [RESPELLED, "Mujhe 500 rupay dene hain"]

    monkeypatch.setattr(script, "_ask_model", model)
    out = await script.to_roman_urdu([ASKED, amount], api_key="k", model="m")
    assert out == [RESPELLED, script.romanize(amount)]
    assert out[1].startswith("mujhe paanch sau ") and "500" not in out[1]


# ---------------------------------------------------------------------------
# Where people see it
# ---------------------------------------------------------------------------

class _Session:
    """Stands in for a call's agent session at the moment it ends."""

    handler = type("H", (), {"value": "agent"})()
    frames_out = 10

    def __init__(self, transcript: str):
        self._transcript = transcript

    async def end(self, _reason):
        return None

    def transcript_text(self) -> str:
        return self._transcript


async def _ended_call(monkeypatch, transcript: str, model_reply):
    from app.repositories import calls as call_repo

    async def settings_now(_tenant=None):
        return LiveSettings(model="live", api_key="k")

    async def model(_lines, **_k):
        return model_reply

    monkeypatch.setattr(live, "live_settings_now", settings_now)
    monkeypatch.setattr(script, "_ask_model", model)

    tenant = Tenant(phoneNumberId="900", name="Acme", recordCalls=False)
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.IN_PROGRESS, counterparty="+923001112222",
                answeredAt=1.0)
    await call_repo.save_call(call)
    live._sessions[call.id] = _Session(transcript)
    await live.finish(tenant, call.id, "caller hung up")
    saved_now = await call_repo.get_call("900", call.id)
    # Kept from the moment the call is saved, before any respelling.
    assert saved_now.transcript_original == (
        transcript if script.has_devanagari(transcript) else "")
    await asyncio.gather(*list(live._respelling))
    saved = await call_repo.get_call("900", call.id)
    return saved_now.transcript, saved.transcript, saved.transcript_original


async def test_a_finished_call_is_saved_in_roman_urdu(fake_db, monkeypatch):
    transcript = f"caller: {ASKED}\nagent: {DEFERRED}"
    at_once, later, original = await _ended_call(monkeypatch, transcript, [RESPELLED])

    assert not script.has_devanagari(at_once), "saved in Hindi script"
    assert later == f"caller: {RESPELLED}\nagent: {DEFERRED}"
    assert original == transcript, "what the speech model heard was not kept"


async def test_a_respelling_that_changed_the_meaning_is_not_saved(fake_db, monkeypatch):
    transcript = f"caller: {ASKED}\nagent: {DEFERRED}"
    _, later, original = await _ended_call(
        monkeypatch, transcript,
        ["Just tell me the cost of registration, token tax and number plate expenses?"])

    assert later == f"caller: {script.romanize(ASKED)}\nagent: {DEFERRED}"
    assert original == transcript


async def test_a_call_without_hindi_script_is_saved_exactly_and_not_sent_anywhere(
        fake_db, monkeypatch):
    transcript = "caller: What are the charges?\nagent: Let me check."
    at_once, later, original = await _ended_call(monkeypatch, transcript, None)
    assert at_once == later == transcript
    assert original == "", "nothing was rewritten, so there is no second copy"
    assert not live._respelling


def test_the_unanswered_page_shows_roman_urdu_for_an_old_call(client, tenant_factory, auth):
    """Calls saved before this change still hold Hindi script."""
    from app.repositories import calls as call_repo

    _, key = tenant_factory("910")
    auth_headers = auth(key)
    call = Call(tenantId="910", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.COMPLETED, counterparty="+923379723102",
                transcript=f"caller: {ASKED}\nagent: {DEFERRED}")
    asyncio.run(call_repo.save_call(call))

    [gap] = client.get("/api/gaps", headers=auth_headers).json()["gaps"]
    assert not script.has_devanagari(gap["question"])
    assert gap["question"].endswith("kya hai?")

    shown = client.get(f"/api/calls/{call.id}/transcript", headers=auth_headers).json()
    assert not script.has_devanagari(shown["transcript"])

    # Shown in Roman Urdu, stored as it was.
    assert asyncio.run(call_repo.get_call("910", call.id)).transcript == call.transcript
