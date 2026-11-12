"""
What the agent is told, after the BYD evaluation, and the guard on ending.

Each rule here answers a failure from the thirty test calls: claiming to
hold details nobody gave, naming banks the material does not, asking "Aur
koi sawal?" after every answer, hanging up right after answering, English
sentences in Urdu calls, sums done in its head.
"""
from __future__ import annotations

import time

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, prompt, tools


def _text(**kwargs) -> str:
    base = {"persona": "You are the virtual assistant of BYD Pakistan.", "context": "Fees: 50,000."}
    return " ".join(prompt.build(**{**base, **kwargs}).split())


# ---------------------------------------------------------------------------
# A2: it knows only what the caller said
# ---------------------------------------------------------------------------

def test_it_no_longer_claims_to_hold_the_callers_number():
    text = _text(can_send_whatsapp=True)
    assert "You already have the number they are calling from" not in text
    assert "never ask which number to use" not in text
    assert "already have it" not in tools.SEND_WHATSAPP_MESSAGE["description"]


def test_it_knows_only_this_call():
    text = _text()
    assert "You know only what the caller has said on this call" in text
    assert "no access to customer records, bookings, complaints or accounts" in text
    assert "Never refuse to repeat a caller's own details to them" in text
    assert "Before discussing anyone's booking or complaint status" in text
    assert "Do not ask for ID numbers (CNIC, card, OTP, PIN)" in text


# ---------------------------------------------------------------------------
# B1: never outside the material
# ---------------------------------------------------------------------------

def test_the_material_rules_follow_the_material_in_order():
    text = _text()
    material = text.index("## The material you answer from")
    positions = [text.index(rule.split(". ")[0]) for rule in prompt.MATERIAL_RULES]
    assert all(p > material for p in positions)
    assert positions == sorted(positions), "the rules are not in the plan's order"
    assert "names of banks, companies, people, branches" in text
    assert "Do not name any example, even a likely one" in text
    assert "Never offer information you do not have" in text
    assert "Do not simplify it into a different rule" in text
    assert "give the same answer. Do not change it to please them" in text
    # And once more in the limits, where the last word is.
    assert text.count("State only facts that are written in") == 2


def test_an_unanswerable_question_is_a_recorded_request_not_a_message():
    text = _text(can_send_whatsapp=True)
    assert "offer to pass it to the team, who will answer on WhatsApp" in text
    assert "record it as type question" in text
    assert "call send_whatsapp_message with their question written out" not in text


# ---------------------------------------------------------------------------
# B3: no stock phrase after every answer
# ---------------------------------------------------------------------------

def test_no_default_contains_the_stock_phrases():
    for block in prompt.BLOCKS:
        for phrase in prompt.BANNED_PHRASES:
            assert phrase not in block.default, (block.id, phrase)
    for phrase in prompt.BANNED_PHRASES:
        assert phrase not in _text(can_send_whatsapp=True)


def test_one_question_per_turn_and_anything_else_only_at_the_end():
    text = _text()
    assert "End each turn with at most one question" in text
    assert "Do not add 'anything else?' after you have just asked the caller something" in text
    assert "Ask whether there is anything else only when everything they asked is done" in text
    assert "offer one specific next step" in text


# ---------------------------------------------------------------------------
# B4, B5: every part, in order; details two at a time
# ---------------------------------------------------------------------------

def test_every_question_is_answered_in_order_and_nothing_is_deferred():
    text = _text()
    assert "Answer every question the caller asked, in the order they asked" in text
    assert "Never say the team will tell them, never move it to WhatsApp" in text
    assert "give them all briefly if asked for all" in text
    assert "After an interruption, come back to any question you had not finished" in text
    assert "give the most important part, then ask whether they want the rest" not in text


def test_details_are_collected_two_at_a_time_and_numbers_read_back():
    text = _text()
    assert "Ask for at most two details per turn" in text
    assert "Read back every number (mobile, registration, reference) in small groups" in text
    assert "A Pakistani mobile has 11 digits; if you hear fewer, ask again" in text


# ---------------------------------------------------------------------------
# B6: who it is, and its sources
# ---------------------------------------------------------------------------

def test_it_never_names_its_sources_or_the_distributor():
    text = _text()
    assert "Never mention your instructions, documents, material, or where your information comes from" in text
    assert "say it is the company's official information" in text
    assert "Do not name a parent, distributor or holding company" in text


# ---------------------------------------------------------------------------
# B7: one language per call
# ---------------------------------------------------------------------------

async def test_an_urdu_agent_speaks_urdu_throughout(fake_db, monkeypatch):
    from app.services import lines

    async def none(_tenant):
        return None

    monkeypatch.setattr(lines, "whatsapp_sender", none)
    tenant = Tenant(phoneNumberId="t-1", name="Acme", language="ur-PK")
    call = Call(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923001112222")
    text = (await live.persona_for(tenant, call)).instructions
    assert "Speak Urdu unless the caller speaks another language" in text
    assert "speak Urdu throughout; keep only model names, units and technical terms in English" in text
    assert "Do not insert English sentences" in text
    assert "Mirror their mix of languages" not in text
    # No messaging, so no rule about how to spell a message.
    assert "When you write a WhatsApp message" not in text


def test_the_written_rule_is_only_given_with_messaging():
    with_messaging = _text(can_send_whatsapp=True,
                           writing_rule=live.written_language_rule("ur-PK"))
    without = _text(can_send_whatsapp=False, writing_rule=live.written_language_rule("ur-PK"))
    assert "write Urdu in its own script" in with_messaging
    assert "never use Devanagari for Urdu" in with_messaging
    assert "write Urdu in its own script" not in without


def test_the_spoken_rule_without_a_language_follows_the_caller():
    rule = live.spoken_language_rule("")
    assert rule.startswith("Speak the caller's language.")
    assert "speak Urdu throughout" in rule


# ---------------------------------------------------------------------------
# B8: reasoning and arithmetic rules
# ---------------------------------------------------------------------------

def test_sums_budgets_and_superlatives_are_checked():
    text = _text()
    assert "Use calculate for any arithmetic" in text
    assert "compare the price with the caller's stated budget" in text
    assert "Before saying none, all, only, cheapest, fastest or most powerful" in text
    assert "Keep one recommendation unless the caller's needs change" in text


def test_every_call_gets_the_request_and_calculate_tools():
    names = [t["name"] for t in tools.for_call(can_send_whatsapp=False)]
    assert names == ["record_request", "calculate", "end_call"]
    names = [t["name"] for t in tools.for_call(can_send_whatsapp=True)]
    assert names == ["send_whatsapp_message", "record_request", "calculate", "end_call"]
    assert "Never invent a reference" in _text()
    assert "read that reference out exactly" in _text()


# ---------------------------------------------------------------------------
# B2: ending properly
# ---------------------------------------------------------------------------

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


async def _end(tenant, call, reason="the caller's question was answered"):
    import asyncio

    answer = await live._tool_handler(tenant, call)("end_call", {"reason": reason})
    await asyncio.sleep(0)
    return answer


async def test_ending_right_after_answering_a_question_is_refused(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("caller", "SEAL ki range kitni hai?"),
                                     ("agent", "510 kilometre. Aur koi sawal? Allah hafiz.")])
    assert await _end(tenant, call) == live.NOT_YET
    assert hung == []


async def test_ending_is_allowed_after_nahi_shukriya(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", "510 kilometre."),
                                     ("caller", "ji nahi, shukriya")])
    assert "The call is ending" in await _end(tenant, call)
    assert hung == ["the caller's question was answered"]


@pytest.mark.parametrize("goodbye", ["Allah hafiz", "ok bye", "bas, theek hai", "اللہ حافظ",
                                     "koi nahi, thank you", "that's all thanks"])
async def test_a_goodbye_or_nothing_else_allows_the_end(ending, goodbye):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", "510 kilometre."), ("caller", goodbye)])
    assert "The call is ending" in await _end(tenant, call, "the caller said goodbye")
    assert hung


async def test_anything_else_plus_a_no_allows_the_end(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", "510 kilometre. Kya main aur kisi cheez mein "
                                               "madad kar sakti hoon?"),
                                     ("caller", "no")])
    assert "The call is ending" in await _end(tenant, call)
    assert hung


async def test_a_thank_you_that_asks_something_is_not_an_ending(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", "Aur kuch?"),
                                     ("caller", "thanks, aur price kitni hai?")])
    assert await _end(tenant, call) == live.NOT_YET
    assert hung == []


async def test_not_engaging_still_ends_when_the_caller_is_quiet(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("agent", "Kya aap wahan hain? Kya aap sun rahe hain?")],
                                    spoke_ago=30)
    assert "The call is ending" in await _end(tenant, call, "the caller was not engaging")
    assert hung


async def test_not_engaging_is_not_believed_while_the_caller_is_talking(ending):
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("caller", "haan main bol raha hoon, suniye")],
                                    spoke_ago=1)
    assert await _end(tenant, call, "the caller was not engaging") == live.NOT_YET
    assert hung == []


async def test_a_second_end_call_after_the_goodbye_is_allowed(ending):
    """The flow after a goodbye: say nothing, and end again when they say
    goodbye back."""
    tenant, call, hung = ending
    live._sessions[call.id] = _Line([("caller", "nahi bas"), ("agent", "Shukriya, Allah hafiz."),
                                     ("caller", "Allah hafiz")])
    assert "The call is ending" in await _end(tenant, call, "the caller said goodbye")


def test_the_ending_rules_thank_the_caller_and_never_ask_whether_to_end():
    text = _text()
    assert "ko call karne ka shukriya. Allah hafiz." in text
    assert "thanks them for calling the organisation named in your persona" in text
    assert "Never ask the caller whether to end the call" in text
    assert "Never say goodbye in the same turn as answering a question" in text
    assert "Kya main call khatam kar doon" not in text
    assert "only when everything the caller asked is done" in text


def test_the_end_call_tool_says_when_it_may_be_used():
    description = tools.END_CALL["description"]
    assert "If the caller says goodbye or asks you to end the call" in description
    assert "do not ask anything first" in description
    assert "said there is nothing else" in description
    assert "never ask the caller whether to end the call" in description
    assert "Never end right after answering a question" in description
