"""
What the agent is told, and what it can do.

The prompt is tested like code because it is the only thing standing between a
caller and an agent that claims to be a university, asks for a number it
already has, or will not hang up.
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, tools


def _tenant(**extra) -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme University", **extra)


def _call(**extra) -> Call:
    return Call(
        tenantId="t-1", channel=Channel.WHATSAPP_CALL, direction=Direction.INBOUND,
        counterparty="+923191611020", **extra,
    )


@pytest.fixture
def no_whatsapp(monkeypatch):
    async def none(_tenant):
        return None
    monkeypatch.setattr("app.services.lines.whatsapp_sender", none)


@pytest.fixture
def with_whatsapp(monkeypatch):
    async def sender(tenant):
        return tenant
    monkeypatch.setattr("app.services.lines.whatsapp_sender", sender)


# ---------------------------------------------------------------------------
# Who she says she is
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_she_is_an_assistant_not_the_organisation(fake_db, no_whatsapp):
    persona = await live.persona_for(_tenant(), _call())
    text = persona.instructions

    assert "virtual assistant answering on this organisation’s behalf" in text         or "virtual assistant answering on this organisation's behalf" in text
    assert "you are not the organisation itself" in text
    assert "never claim to be human" in text.lower()


@pytest.mark.asyncio
async def test_the_account_name_is_never_put_in_her_mouth(fake_db, no_whatsapp):
    """An account called "Calling Agent" had its university assistant
    introduce itself as the assistant for Calling Agent, contradicting the
    persona directly above it."""
    tenant = _tenant(persona="You are the university's virtual assistant.")
    tenant.name = "Calling Agent"
    text = (await live.persona_for(tenant, _call())).instructions
    assert "Calling Agent" not in text


@pytest.mark.asyncio
async def test_tone_and_escalation_notes_reach_the_agent(fake_db, no_whatsapp, monkeypatch):
    """The settings screen collects both and nothing used to read them."""
    from app.services import routing

    def voice(_tenant, _resolved):
        return {
            "greeting": "Hello", "persona": "You are the assistant.",
            "language": "ur-PK", "ttsVoice": "",
            "tone": "Never promise a refund.",
            "escalation": "Offer a callback if unsure.",
        }

    monkeypatch.setattr(routing, "persona_for", voice)
    text = (await live.persona_for(_tenant(), _call())).instructions
    assert "Never promise a refund." in text
    assert "Offer a callback if unsure." in text


def test_a_language_tag_is_spoken_as_a_language():
    assert live._language_name("ur-PK") == "Urdu"
    assert live._language_name("en") == "English"
    # Anything unrecognised passes through rather than being dropped.
    assert live._language_name("xx-YY") == "xx-YY"


@pytest.mark.asyncio
async def test_she_is_told_to_answer_what_was_said(fake_db, no_whatsapp):
    text = (await live.persona_for(_tenant(), _call())).instructions
    assert "Acknowledge what they told you before you answer" in text
    assert "Never repeat your opening line" in text
    assert "warm" in text.lower()


@pytest.mark.asyncio
async def test_the_companys_own_persona_still_leads(fake_db, no_whatsapp):
    """The company wrote it; it must not be buried under our scaffolding."""
    persona = await live.persona_for(_tenant(persona="You are Ayesha. Be brisk."), _call())
    assert persona.instructions.startswith("You are Ayesha. Be brisk.")


# ---------------------------------------------------------------------------
# What she can do
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messaging_is_offered_only_when_it_could_work(fake_db, no_whatsapp):
    persona = await live.persona_for(_tenant(), _call())
    assert [t["name"] for t in persona.tools] == ["end_call"]
    assert "send_whatsapp_message" not in persona.instructions


@pytest.mark.asyncio
async def test_messaging_is_offered_when_a_whatsapp_number_exists(fake_db, with_whatsapp):
    persona = await live.persona_for(_tenant(), _call())
    names = [t["name"] for t in persona.tools]
    assert names == ["send_whatsapp_message", "end_call"]
    assert "never ask" in persona.instructions.lower()


def test_the_message_tool_forbids_asking_for_the_number():
    description = tools.SEND_WHATSAPP_MESSAGE["description"]
    assert "Do NOT ask them for their number" in description
    assert "already have it" in description


def test_the_end_call_tool_covers_a_time_waster_and_forbids_an_escape():
    description = tools.END_CALL["description"]
    assert "going nowhere" in description
    assert "abusive" in description
    assert "never as a way to avoid a question" in description


# ---------------------------------------------------------------------------
# Running the tools
# ---------------------------------------------------------------------------

def test_a_message_goes_to_the_caller_without_being_asked(monkeypatch):
    """The number on the call is the default, which is the whole point."""
    sent: list[tuple[str, str]] = []

    class FakeWhatsApp:
        def __init__(self, tenant):
            self.tenant = tenant

        async def send_text(self, to, body, call_id=""):
            sent.append((to, body))

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant
    monkeypatch.setattr("app.services.lines.whatsapp_sender", sender)

    handle = live._tool_handler(_tenant(), _call())
    answer = asyncio.run(handle("send_whatsapp_message", {"text": "Fees are 50,000."}))

    assert sent == [("+923191611020", "Fees are 50,000.")]
    assert "Sent" in answer


def test_a_message_to_a_different_number_is_allowed(monkeypatch):
    sent: list[tuple[str, str]] = []

    class FakeWhatsApp:
        def __init__(self, tenant): ...
        async def send_text(self, to, body, call_id=""):
            sent.append((to, body))

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant
    monkeypatch.setattr("app.services.lines.whatsapp_sender", sender)

    handle = live._tool_handler(_tenant(defaultCountryCode="92"), _call())
    asyncio.run(handle("send_whatsapp_message", {"text": "hi", "to": "03001112222"}))
    assert sent[0][0] == "+923001112222"


def test_a_failed_message_never_claims_it_was_sent(monkeypatch):
    class FakeWhatsApp:
        def __init__(self, tenant): ...
        async def send_text(self, *a, **kw):
            raise RuntimeError("Meta said no")

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant
    monkeypatch.setattr("app.services.lines.whatsapp_sender", sender)

    handle = live._tool_handler(_tenant(), _call())
    answer = asyncio.run(handle("send_whatsapp_message", {"text": "hi"}))
    assert "did not send" in answer
    assert "Sent" not in answer


def test_no_whatsapp_number_is_said_plainly(monkeypatch):
    async def none(_tenant):
        return None
    monkeypatch.setattr("app.services.lines.whatsapp_sender", none)

    handle = live._tool_handler(_tenant(), _call())
    answer = asyncio.run(handle("send_whatsapp_message", {"text": "hi"}))
    assert "no WhatsApp number" in answer


def test_an_unknown_tool_is_refused_not_crashed():
    handle = live._tool_handler(_tenant(), _call())
    assert "not something you can do" in asyncio.run(handle("launch_rocket", {}))


@pytest.mark.asyncio
async def test_ending_waits_for_the_goodbye_before_hanging_up(monkeypatch):
    """Cutting the line the instant end_call fires clips the farewell, which
    sounds exactly like a dropped call."""
    call = _call(id="c-9")
    hung_up: list[str] = []

    class Session:
        def __init__(self):
            self.speaking = True

    session = Session()
    live._sessions["c-9"] = session

    async def fake_hang_up(_tenant, _call, reason):
        hung_up.append(reason)

    monkeypatch.setattr(live, "hang_up", fake_hang_up)

    task = asyncio.create_task(live._hang_up_after_goodbye(_tenant(), call, "done"))
    await asyncio.sleep(0.5)
    assert not hung_up, "hung up while the agent was still speaking"
    session.speaking = False
    await asyncio.wait_for(task, timeout=5)
    assert hung_up == ["done"]
    live._sessions.pop("c-9", None)


# ---------------------------------------------------------------------------
# Where things sit in the prompt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_rules_come_after_the_material(fake_db, with_whatsapp, monkeypatch):
    """Measured, not guessed: with the rules before a 67k-character knowledge
    base the agent used send_whatsapp_message two times in five, telling the
    caller "I have sent it" while sending nothing. Moved after, five in five.
    Last word in the prompt wins, so ordering is load-bearing."""
    from app.services import routing

    async def big_context(_tenant, _resolved):
        return "FEE SCHEDULE. " + ("x" * 60_000)

    monkeypatch.setattr(routing, "context_for", big_context)

    text = (await live.persona_for(_tenant(), _call())).instructions
    material = text.index("## The material you answer from")
    conduct = text.index("## How to behave on this call")
    assert material < conduct, "the rules were buried before the knowledge base again"
    assert text.index("send_whatsapp_message") > material


@pytest.mark.asyncio
async def test_the_persona_is_restated_at_the_end(fake_db, no_whatsapp, monkeypatch):
    """A persona saying "call it the university, never by name" was ignored in
    favour of the name used throughout the documents, for the same reason."""
    from app.services import routing

    async def big_context(_tenant, _resolved):
        return "The University of Central Punjab. " + ("x" * 60_000)

    monkeypatch.setattr(routing, "context_for", big_context)

    persona = "You are the university's assistant. Say 'the university', never its name."
    text = (await live.persona_for(_tenant(persona=persona), _call())).instructions
    assert text.count(persona) == 2, "the persona is stated once and buried"
    assert text.rindex(persona) > text.index("## The material you answer from")


@pytest.mark.asyncio
async def test_she_is_forbidden_from_claiming_an_unsent_message(fake_db, with_whatsapp):
    """The failure mode was not silence. It was saying "I have sent it on
    WhatsApp" to somebody who would then go and look for it."""
    text = (await live.persona_for(_tenant(), _call())).instructions
    assert "Never tell a caller you have sent something unless you called" in text
    assert "is a lie to someone who will go and look for it" in text


@pytest.mark.asyncio
async def test_she_is_told_she_cannot_transfer_a_call(fake_db, no_whatsapp):
    """On a real call she said "please hold, I will connect you to an
    admissions officer". There is no switchboard behind her, so the caller
    would have held a line where nothing was ever going to happen."""
    text = (await live.persona_for(_tenant(), _call())).instructions
    assert "You cannot transfer a call" in text
    assert "There is no switchboard behind you" in text
    assert "cannot put them" in text


@pytest.mark.asyncio
async def test_she_is_told_not_to_promise_a_time(fake_db, no_whatsapp):
    text = (await live.persona_for(_tenant(), _call())).instructions
    assert "Promise nothing with a time on it" in text
