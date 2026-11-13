"""
From the ninth real call, inbound on Twilio from a line ending 4604.

The server knew the number. The agent said "Mere paas aap ka number nahi hai"
and "nahi dikh raha" four times, never offered the number ending 4604, and
ignored "Jis number se main call kar raha hun, isi pe bhej de" and "Number
ends with 4604, ispe bhej de". Earlier it had recommended "SEAL aur SEALION
7" to a caller with a budget of 1 crore 50 lakh, SEALION 7 being above it.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import amounts, live, prompt
from app.services.agent.gemini import AgentPersona, LiveSettings
from app.services.agent.session import CallSession

L2 = ("caller", "mujhe ek faimili ke lie gaari leni hai. mera bajat ₹1,50,00,000 hai. mera roj ka "
                "safar 40 ki.mi. hai aur haan, mere paas paarking ki jagah hai.")
L3 = ("agent", "Aap ke budget aur daily safar ke hisab se, main aap ko SEAL aur SEALION 7 "
               "recommend karti hoon. Yeh dono family ke liye bahut acchi gaariyan hain aur "
               "features se bhari hui hain. Kya aap in mein se kisi ek ke bare mein mazeed janna "
               "chahte hain? Ya koi aur detail batana chahenge?")
L4 = ("caller", "Aap yeh details mujhe mere number par bhej sakte hain?")
L5 = ("agent", "Ji bilkul main yeh details aap ko WhatsApp par bhej sakti hoon. Aap mujhe bata "
               "sakte hain ke kis number par bhejna chahiye, jis number par aap call kar rahe "
               "hain usi par, ya kisi aur par?")
L6 = ("caller", "Aapke paas mera number hai?")
L7 = ("agent", "Mere paas aap ka number nahi hai. Kya aap mujhe bata sakte hain ke kis number par "
               "main details bhejoon? Aap read back karwa sakte hain, us ke baad main bhej doongi.")
L15 = ("agent", "Maazrat chahti hoon, mujhe abhi bhi aap ka number nahi dikh raha. Kya aap mujhe "
                "bata sakte hain ke kis number par main details bhejoon?")
L16 = ("caller", "Jis number se main call kar raha hun, isi pe bhej de.")
L18 = ("caller", "Number ends with 4604, ispe bhej de.")
L19 = ("agent", "Pura number batana chahenge read back ke liye? Taake main confirm kar ke bhej "
                "sakoon, kyun ke mere paas is call par number nahi hai. Kya aap mujhe bata sakte "
                "hain?")
TEXT = "SEAL Dynamic: PKR 14,790,000 · SEALION 7 Premium: PKR 15,490,000"
MATERIAL = (
    "## Prices\n"
    "- BYD SEAL Dynamic: PKR 14,790,000 (1 crore 47 lakh 90 hazar)\n"
    "- BYD SEAL Premium: PKR 16,990,000 (1 crore 69 lakh 90 hazar)\n"
    "| Model | Price |\n"
    "| SEALION 7 Premium | PKR 15,490,000 (1 crore 54 lakh 90 hazar) |\n"
    "| ATTO 3 Advance | PKR 8,990,000 (89 lakh 90 hazar) |\n"
    "The price of SHARK 6 is PKR 19,990,000 (1 crore 99 lakh 90 hazar).\n"
    "Booking advance: PKR 1,750,000 (17 lakh 50 hazar).\n"
)
WHATSAPP_TOOL = [{"name": "send_whatsapp_message"}, {"name": "end_call"}]


def _inbound(**extra) -> Call:
    """The call as the Twilio voice webhook creates it."""
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923011114604", fromNumber="+923000000001")
    return Call(**{**fields, **extra})


def _outbound(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.OUTBOUND,
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


def _watched(call: Call, lines=(), *, material: str = MATERIAL, tools=WHATSAPP_TOOL) -> CallSession:
    session = CallSession(call.id, send_to_caller=lambda _f: None,
                          live_settings=LiveSettings(model="m", api_key="k"),
                          persona=AgentPersona(instructions=material, tools=tools), record=False)
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
def can_send(monkeypatch):
    from app.services import lines

    async def yes(_tenant):
        return True

    async def sender(tenant):
        return tenant

    monkeypatch.setattr(live, "_can_send_whatsapp", yes)
    monkeypatch.setattr(lines, "whatsapp_sender", sender)


# ---------------------------------------------------------------------------
# 1. The calling line reaches the prompt on every call
# ---------------------------------------------------------------------------

async def test_an_inbound_twilio_call_tells_the_agent_the_line_ending(fake_db, can_send):
    persona = await live.persona_for(_tenant(language="ur-PK"), _inbound())
    assert "The caller is on a line ending 4604" in persona.instructions
    assert "jis ke aakhir mein 4604 hai" in persona.instructions
    assert "Never say you do not have their number" in persona.instructions


async def test_an_outbound_call_tells_the_agent_the_same(fake_db, can_send):
    persona = await live.persona_for(_tenant(), _outbound())
    assert "The caller is on a line ending 4604" in persona.instructions


async def test_a_companys_own_writing_block_cannot_lose_the_line(fake_db, can_send):
    """The fact lives outside the editable block."""
    text = prompt.build(persona="P", context="C", can_send_whatsapp=True,
                        written={"writing": "Send things the way we like."},
                        calling_number_ending="4604")
    assert "Send things the way we like." in text
    assert "The caller is on a line ending 4604" in text


def test_a_withheld_number_is_said_once_and_never_repeated_in_the_prompt():
    text = prompt.build(persona="P", context="C", can_send_whatsapp=True, calling_number_ending="")
    assert "Is call par aap ka number mujhe nahi mil raha, please number bata dein" in text
    assert "never repeat the same refusal" in text
    assert "The caller is on a line ending" not in text


def test_without_messaging_nothing_about_the_line_is_said():
    text = prompt.build(persona="P", context="C", can_send_whatsapp=False, calling_number_ending="4604")
    assert "line ending 4604" not in text


# ---------------------------------------------------------------------------
# The tool: the line is chosen in words or by its last four digits
# ---------------------------------------------------------------------------

async def test_jis_number_se_call_kar_raha_hun_chooses_the_line(fake_db, can_send):
    call = _inbound()
    live._sessions[call.id] = _watched(call, [L4, L5, L16])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert "the number ending 4604" in answer and "Read this back" in answer
    live._sessions[call.id]._note("agent", "4604 par khatam hone wale number par bhej doon?")
    live._sessions[call.id]._note("caller", "ji")
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "calling_number", "number_confirmed": True})
    assert answer == live.QUEUED
    assert live.queued_messages(live._pending[call.id])[0]["to"] == "+923011114604"


async def test_the_last_four_digits_choose_the_line_too(fake_db, can_send):
    call = _inbound()
    live._sessions[call.id] = _watched(call, [L4, L5, L18])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert answer != live.NOT_SAME_NUMBER
    assert "the number ending 4604" in answer
    assert live._said_line_ending(L18[1], call)
    assert not live._said_line_ending("Number ends with 9999, ispe bhej de.", call)
    assert not live._said_line_ending("mera number 0300 123 4604 hai", call), "a whole number is itself"
    assert live._caller_chose([L18], call, "92") == ("calling", "+923011114604")


# ---------------------------------------------------------------------------
# The watch: the agent is told, and corrected when it denies
# ---------------------------------------------------------------------------

async def test_choosing_the_line_gets_the_agent_a_note_with_the_digits(fake_db, caplog):
    call = _inbound()
    session = _watched(call, [L4, L5])
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note(*L16)
        await asyncio.sleep(0.05)
    assert session._agent.notes == [(live.CALLING_LINE_NOTE.format(ending="4604"), False)]
    assert "the number ending 4604" in session._agent.notes[0][0]
    assert "to set to calling_number" in session._agent.notes[0][0]
    assert session._agent.said == []
    session._note("agent", "Mujhe abhi bhi aap ka number nahi dikh raha.")
    session._note(*L18)
    await asyncio.sleep(0.05)
    assert len(session._agent.notes) == 3          # the denial, then the digits again


async def test_denying_a_known_number_is_logged_and_corrected(fake_db, caplog):
    call = _inbound()
    session = _watched(call, [L4, L5, L6])
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note(*L7)
        await asyncio.sleep(0.05)
    [warning] = [r.getMessage() for r in caplog.records if "has no number for the caller" in r.getMessage()]
    assert "4604" in warning and "+923011114604" not in warning
    assert session._agent.notes == [(live.CALLING_LINE_NOTE.format(ending="4604"), False)]
    for who, text in [("caller", "ji"), L15, L16, L19]:
        session._note(who, text)
    await asyncio.sleep(0.05)
    assert all(n == live.CALLING_LINE_NOTE.format(ending="4604") for n, _ in session._agent.notes)
    assert len(session._agent.notes) == 4


async def test_the_offer_with_the_digits_is_not_a_denial(fake_db):
    call = _inbound()
    session = _watched(call, [L4])
    session._note("agent", "Aap ka poora number mujhe nazar nahi aata, lekin main us number par "
                           "bhej sakti hoon jis se aap call kar rahe hain, jis ke aakhir mein "
                           "4604 hai.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


async def test_a_withheld_number_may_be_refused_once(fake_db):
    call = _inbound(counterparty="")
    session = _watched(call, [L4, L5, L6])
    session._note("agent", "Is call par aap ka number mujhe nahi mil raha, please number bata dein.")
    await asyncio.sleep(0.05)
    assert session._agent.notes == []
    session._note("caller", "number hai hi nahi aapke paas?")
    session._note(*L15)                                 # the same refusal again
    await asyncio.sleep(0.05)
    assert session._agent.notes == [(live.LINE_UNKNOWN_AGAIN_NOTE, False)]
    session._note(*L16)                                 # "the number I am calling from"
    await asyncio.sleep(0.05)
    assert session._agent.notes[-1] == (live.LINE_UNKNOWN_AGAIN_NOTE, False)


async def test_nothing_is_said_about_the_line_without_the_tool(fake_db):
    call = _inbound()
    session = _watched(call, [L4], tools=[{"name": "end_call"}])
    session._note(*L16)
    session._note(*L7)
    await asyncio.sleep(0.05)
    assert session._agent.notes == []


# ---------------------------------------------------------------------------
# 2. The budget
# ---------------------------------------------------------------------------

def test_the_budget_is_read_from_the_callers_words():
    assert amounts.stated_budget(L2[1]) == 15_000_000
    assert amounts.stated_budget("Mera budget 1 crore 50 lakh tak hai") == 15_000_000
    assert amounts.stated_budget("budget 80 lakh ke aas paas") == 8_000_000
    assert amounts.stated_budget("mujhe 1 crore 50 lakh ki gaari chahiye") is None
    assert amounts.stated_budget("mera budget 40 km hai") is None


def test_priced_items_are_read_from_any_shape_of_material():
    items = dict(amounts.priced_items(MATERIAL))
    assert items["BYD SEAL Dynamic"] == 14_790_000
    assert items["SEALION 7 Premium"] == 15_490_000
    assert items["ATTO 3 Advance"] == 8_990_000
    assert items["SHARK 6"] == 19_990_000
    assert items["Booking advance"] == 1_750_000
    families = amounts.family_prices(MATERIAL)
    assert families["sealion 7"] == [15_490_000]
    assert sorted(families["seal"]) == [14_790_000, 16_990_000]
    assert sorted(families["byd seal"]) == [14_790_000, 16_990_000]
    assert "booking" not in families and "byd" not in families
    assert amounts.families_named(L3[1], families) == ["seal", "sealion 7"]
    assert amounts.families_named("ATTO 3 ya SHARK 6?", families) == ["atto 3", "shark 6"]
    assert amounts.families_named("BYD Pakistan ko call karne ka shukriya", families) == []


async def test_a_stated_budget_gets_the_agent_a_note(fake_db, caplog):
    call = _inbound()
    session = _watched(call)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        session._note(*L2)
        await asyncio.sleep(0.05)
    [(note, urgent)] = session._agent.notes
    assert not urgent
    assert note == live.BUDGET_NOTE.format(budget="1 crore 50 lakh rupay (15,000,000)")
    assert "compare each model's price with it using calculate" in note
    assert any("stated a budget of 15000000" in r.getMessage() for r in caplog.records)


async def test_a_model_above_the_budget_named_without_saying_so_is_corrected(fake_db, caplog):
    call = _inbound()
    session = _watched(call)
    session._note(*L2)
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note(*L3)
        await asyncio.sleep(0.05)
    [warning] = [r.getMessage() for r in caplog.records if "above the caller's budget" in r.getMessage()]
    assert "sealion 7" in warning and "15490000" in warning and "15000000" in warning
    assert "seal (" not in warning                      # SEAL Dynamic is within budget
    assert session._agent.notes[-1] == (live.BUDGET_CORRECTION.format(
        model="SEALION 7", price="1 crore 54 lakh 90 hazar rupay", budget="1 crore 50 lakh rupay",
        over="4 lakh 90 hazar rupay"), False)
    assert session._agent.said == []


async def test_saying_a_model_is_over_budget_is_fine(fake_db, caplog):
    call = _inbound()
    session = _watched(call, [L2])
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        session._note("agent", "SEAL Dynamic aap ke budget mein hai; SEALION 7 aap ke budget se "
                               "4 lakh 90 hazar zyada hai.")
        await asyncio.sleep(0.05)
    assert not any("above the caller's budget" in r.getMessage() for r in caplog.records)
    assert len(session._agent.notes) == 1              # only the budget note itself


async def test_without_a_budget_nothing_is_checked(fake_db):
    call = _inbound()
    session = _watched(call, [("caller", "family ke liye gaari chahiye")])
    session._note(*L3)
    await asyncio.sleep(0.05)
    assert session._agent.notes == []
