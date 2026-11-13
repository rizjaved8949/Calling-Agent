"""
A number is a number however it is written, and an amount is said only as
given.

On a real call the agent said "20 lakh 10 hazar" where the calculator had
given 210000. The stored transcript read "2 lakh 10 hazar". Numbers written
as words now count in the respelling check, the calculator says the words to
use, and an amount the agent says that matches nothing it was given is logged
and corrected with a silent note.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import amounts, live, prompt, script
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

MATERIAL = ("SEAL Dynamic: PKR 14,790,000 (1 crore 47 lakh 90 hazar). ATTO 3 Advance: PKR "
            "8,990,000 (89 lakh 90 hazar). Advance at booking: PKR 1,750,000 (17 lakh 50 hazar).")
BUDGET = ("caller", "40 km ghar mein paarking hai, paanch log aur bajat ₹1,50,00,000 hai.")
SAID_WRONG = ("agent", "Aap ke budget mein BYD SEAL Dynamic bahut achi rahegi. Iski keemat 1 crore "
                       "47 lakh 90 hazar rupay hai, jo aap ke budget se 20 lakh 10 hazar rupay "
                       "kam hai.")
SAID_RIGHT = ("agent", "Aap ke budget mein BYD SEAL Dynamic bahut achi rahegi. Iski keemat 1 crore "
                       "47 lakh 90 hazar rupay hai, jo aap ke budget se 2 lakh 10 hazar rupay "
                       "kam hai.")


# ---------------------------------------------------------------------------
# 1. Numbers as values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, values", [
    ("बीस लाख दस हज़ार", [2010000]),
    ("2 lakh 10 hazar", [210000]),
    ("bees lakh das hazar", [2010000]),
    ("بیس لاکھ دس ہزار", [2010000]),
    ("1 crore 47 lakh 90 hazar", [14790000]),
    ("ek crore sentalis lakh", [10000000, 100000]),       # an unknown word splits the number
    ("PKR 8,990,000", [8990000]),
    ("₹1,50,00,000", [15000000]),
    ("paanch sau rupay", [500]),
    ("do sau pachas", [250]),
    ("dedh lakh", [150000]),
    ("0300 123 4567", [300, 123, 4567]),
    ("gyarah baje", [11]),
    ("११ बजे", [11]),
    ("koi number nahi", []),
])
def test_numbers_are_read_as_values_in_any_script(text, values):
    assert amounts.values_in(text) == values


def test_amounts_said_with_a_scale_are_found_with_their_words():
    assert amounts.spoken_amounts(SAID_WRONG[1]) == [
        ("1 crore 47 lakh 90 hazar", 14790000), ("20 lakh 10 hazar", 2010000)]
    assert amounts.spoken_amounts("bees lakh das hazar rupay") == [("bees lakh das hazar", 2010000)]
    assert amounts.spoken_amounts("gyarah baje, 500 rupay") == []


def test_the_materials_amounts_are_known_as_values():
    assert amounts.material_values(MATERIAL) == {14790000, 8990000, 1750000}


# ---------------------------------------------------------------------------
# The Roman Urdu step never changes a number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("original, changed", [
    ("बीस लाख दस हज़ार", "2 lakh 10 hazar"),
    ("बीस लाख दस हज़ार", "do lakh das hazar"),
    ("2 लाख 10 हज़ार", "bees lakh das hazar"),
    ("जो आपके बजट से बीस लाख दस हज़ार रुपये कम है", "jo aap ke budget se 2 lakh 10 hazar rupay kam hai"),
    ("بیس لاکھ دس ہزار", "2 lakh 10 hazar"),
])
def test_a_changed_number_is_never_accepted(original, changed):
    assert not script.faithful(original, changed)


@pytest.mark.parametrize("original, respelled", [
    ("बीस लाख दस हज़ार", "bees lakh das hazar"),
    ("जो आपके बजट से बीस लाख दस हज़ार रुपये कम है", "jo aap ke budget se bees lakh das hazar rupay kam hai"),
    ("2 लाख 10 हज़ार रुपये", "2 lakh 10 hazar rupay"),
])
def test_the_same_number_respelled_is_accepted(original, respelled):
    assert script.faithful(original, respelled)


async def test_bees_lakh_never_becomes_2_lakh(monkeypatch):
    original = "जो आपके बजट से बीस लाख दस हज़ार रुपये कम है"

    async def model(_lines, **_k):
        return ["jo aap ke budget se 2 lakh 10 hazar rupay kam hai"]

    monkeypatch.setattr(script, "_ask_model", model)
    [out] = await script.to_roman_urdu([original], api_key="k", model="m")
    assert "2 lakh" not in out and "bees laakh das hazaar" in out
    assert out == script.romanize(original)


# ---------------------------------------------------------------------------
# 2. The calculator says the words
# ---------------------------------------------------------------------------

def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923011114604")
    return Call(**{**fields, **extra})


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", recordCalls=False)


@pytest.fixture(autouse=True)
def _clean():
    live._sessions.clear()
    live._calculated.clear()
    live._pending.clear()
    live._recorded.clear()
    yield
    live._sessions.clear()
    live._calculated.clear()
    live._pending.clear()
    live._recorded.clear()


async def test_the_calculate_reply_gives_the_exact_words_to_say():
    call = _call()
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("calculate", {"expression": "15000000-14790000"})
    assert answer == "210000. Say exactly: 2 lakh 10 hazar rupay. Never re-word or round it."
    assert await handle("calculate", {"expression": "1500*12.2/100"}) == (
        "183. Say exactly: 183. Never re-word or round it.")
    assert await handle("calculate", {"expression": "14790000*5/100"}) == (
        "739500. Say exactly: 7 lakh 39 hazar 500 rupay. Never re-word or round it.")
    assert live._calculated[call.id] == [15000000.0, 14790000.0, 210000.0, 1500.0, 12.2, 100.0,
                                         183.0, 14790000.0, 5.0, 100.0, 739500.0]


def test_the_prompt_says_amounts_are_spoken_only_as_given():
    text = " ".join(prompt.build(persona="P", context="C").split())
    assert "Say rupee amounts using the words in brackets after them, exactly." in text
    assert "or the 'Say exactly' words of a calculate result" in text
    assert "Never re-word, round or convert an amount, and never say a figure any other way" in text


# ---------------------------------------------------------------------------
# 3 and 4. An amount that matches nothing it was given
# ---------------------------------------------------------------------------

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


def _watched(call: Call, lines=(), material: str = MATERIAL) -> CallSession:
    session = CallSession(call.id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=material), record=False)
    session._agent = _Ear()
    for who, text in lines:
        session._note(who, text)
    session.on_transcript = live._transcript_watch(_tenant(), call)
    live._sessions[call.id] = session
    return session


async def test_a_wrong_amount_after_a_calculation_is_logged_and_corrected(caplog):
    call = _call()
    session = _watched(call, [("caller", "Mujhe family ke liye gadi chahiye."), BUDGET])
    await live._tool_handler(_tenant(), call)("calculate", {"expression": "15000000-14790000"})
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note(*SAID_WRONG)
        await asyncio.sleep(0.05)
    [warning] = [r.getMessage() for r in caplog.records if "said an amount" in r.getMessage()]
    assert "2010000" in warning and "210000" in warning
    assert "lakh" not in warning                       # the number only
    assert session._agent.notes == [(live.AMOUNT_CORRECTION.format(
        wrong="20 lakh 10 hazar rupay", right="2 lakh 10 hazar rupay"), False)]
    note = session._agent.notes[0][0]
    assert note.startswith(live.NOTE_PREFIX)
    assert "You just said 20 lakh 10 hazar rupay; the correct figure is 2 lakh 10 hazar rupay." in note
    assert "'Maazrat, sahi raqam 2 lakh 10 hazar rupay hai.'" in note
    assert session._agent.said == []
    # The same turn is judged once.
    session._note("agent", " Kya aap test drive book karwana chahti hain?")
    await asyncio.sleep(0.05)
    assert len(session._agent.notes) == 1


async def test_amounts_from_the_material_the_calculator_and_the_caller_pass(caplog):
    call = _call()
    session = _watched(call, [("caller", "Mujhe family ke liye gadi chahiye."), BUDGET])
    await live._tool_handler(_tenant(), call)("calculate", {"expression": "15000000-14790000"})
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note(*SAID_RIGHT)
        session._note("agent", " Aap ka budget 1 crore 50 lakh hai aur advance 17 lakh 50 hazar "
                               "rupay hai.")
        await asyncio.sleep(0.05)
    assert not any("said an amount" in r.getMessage() for r in caplog.records)
    assert session._agent.notes == []


async def test_a_wrong_amount_with_no_calculation_is_matched_to_the_material(caplog):
    call = _call()
    session = _watched(call)
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note("agent", "ATTO 3 ki price 9 lakh 90 hazar rupay hai, registration alag.")
        await asyncio.sleep(0.05)
    assert any("990000" in r.getMessage() and "8990000" in r.getMessage() for r in caplog.records)
    assert session._agent.notes == [(live.AMOUNT_CORRECTION.format(
        wrong="9 lakh 90 hazar rupay", right="89 lakh 90 hazar rupay"), False)]


async def test_an_amount_unlike_anything_known_is_logged_but_not_corrected(caplog):
    call = _call()
    session = _watched(call)
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note("agent", "Yeh 3 crore 33 lakh rupay ka package hai, theek hai?")
        await asyncio.sleep(0.05)
    assert any("33300000" in r.getMessage() for r in caplog.records)
    assert session._agent.notes == []


async def test_an_amount_still_being_said_is_not_judged_yet():
    call = _call()
    session = _watched(call)
    session._note("agent", "Iski keemat 1 crore 47 lakh")       # the "90 hazar" is still coming
    await asyncio.sleep(0.05)
    assert session._agent.notes == []
    session._note("agent", " 90 hazar rupay hai.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


def test_the_original_transcript_is_in_the_calls_public_shape():
    call = _call(transcript="agent: 2 lakh 10 hazar", transcriptOriginal="agent: 2 लाख 10 हज़ार")
    assert call.public()["transcriptOriginal"] == "agent: 2 लाख 10 हज़ार"
    assert _call().public()["transcriptOriginal"] is None
