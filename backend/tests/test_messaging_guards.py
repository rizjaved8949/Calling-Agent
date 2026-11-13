"""
A WhatsApp message goes out only when the caller asked for it, to a number
they said and confirmed, and "sent" is said only when it was.

From thirty evaluation calls: the agent said "I've sent it on WhatsApp" when
nobody had asked, to a number nobody had given, from a line whose token had
expired twelve days earlier. Every send bounced after the caller hung up.
The transcript now decides, not the model.
"""
from __future__ import annotations

import asyncio

import pytest

from app.errors import UpstreamError
from app.models.call import Call, Channel, Direction
from app.models.tenant import Tenant
from app.services.agent import live, prompt, tools, turns

TEXT = "SEAL Premium: PKR 16,990,000. Booking advance 3,350,000."


def _tenant(**extra) -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", **extra)


def _call(**extra) -> Call:
    fields = dict(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923191611020")
    return Call(**{**fields, **extra})


class _Line:
    """A session as the tool guards see it: a transcript, and whether the
    caller has just been heard."""

    def __init__(self, transcript):
        self.transcript = list(transcript)
        self.caller_spoke_at = 0.0

    def caller_spoke_within(self, _seconds):
        return False


def _on_the_line(call: Call, transcript) -> _Line:
    line = _Line(transcript)
    live._sessions[call.id] = line
    return line


@pytest.fixture(autouse=True)
def _no_sessions():
    live._sessions.clear()
    yield
    live._sessions.clear()


@pytest.fixture
def sending(monkeypatch):
    """A WhatsApp sender that records what it was asked to send: a plain text
    or the follow-up template. Each entry is (to, message, kwargs); kwargs
    carries "kind" and, for a template, its name, language and parameters."""
    from app.services import lines

    sent: list[tuple[str, str, dict]] = []

    class FakeWhatsApp:
        def __init__(self, tenant):
            self.tenant = tenant

        async def send_text(self, to, body, **kwargs):
            sent.append((to, body, {**kwargs, "kind": "text"}))

        async def send_template(self, to, template, *, language, parameters, **kwargs):
            sent.append((to, parameters[1], {**kwargs, "kind": "template", "template": template,
                                             "language": language, "parameters": parameters}))

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant

    monkeypatch.setattr(lines, "whatsapp_sender", sender)
    return sent


OFFER_AND_YES = [
    ("caller", "SEAL Premium ki price kya hai?"),
    ("agent", "SEAL Premium 1 crore 69 lakh 90 hazar hai. Kya main yeh details aapko "
              "WhatsApp par bhej doon?"),
    ("caller", "ji bhej dein"),
    ("agent", "Kis number par bhejun?"),
    ("caller", "0300 123 4567"),
]
READ_BACK_CONFIRMED = OFFER_AND_YES + [
    ("agent", "0300 123 4567, sahi hai?"),
    ("caller", "ji sahi hai"),
]


async def _send(tenant, call, **args):
    handle = live._tool_handler(tenant, call)
    return handle, await handle("send_whatsapp_message", {"text": TEXT, **args})


# ---------------------------------------------------------------------------
# The five checks
# ---------------------------------------------------------------------------

async def test_an_unrequested_send_is_refused(fake_db, sending):
    call = _call()
    _on_the_line(call, [("caller", "price kya hai?"), ("agent", "16,990,000 hai.")])
    _, answer = await _send(_tenant(), call, to="03001234567")
    assert answer == live.NOT_ASKED
    assert sending == []


async def test_a_no_after_the_offer_is_a_no(fake_db, sending):
    call = _call()
    _on_the_line(call, [("agent", "Kya main WhatsApp par bhej doon?"),
                        ("caller", "nahi, rehne dein")])
    _, answer = await _send(_tenant(), call, to="03001234567")
    assert answer == live.NOT_ASKED and sending == []


async def test_the_caller_asking_themselves_is_consent(fake_db, sending):
    call = _call()
    _on_the_line(call, [("caller", "mujhe yeh WhatsApp par likh kar bhej dein, "
                                   "number 0300 123 4567")])
    _, answer = await _send(_tenant(), call, to="0300 123 4567")
    assert "Read this back" in answer, answer


async def test_consent_with_an_unconfirmed_number_gives_a_read_back_not_a_send(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES)
    _, answer = await _send(_tenant(), call, to="0300 123 4567", number_confirmed=False)
    assert "Read this back" in answer and "0300 123 4567" in answer
    assert "number_confirmed true" in answer
    assert sending == []


async def test_a_confirmed_number_is_sent_to_and_the_summary_says_so(fake_db, sending):
    from app.repositories import calls as call_repo

    call = _call()
    await call_repo.save_call(call)
    _on_the_line(call, READ_BACK_CONFIRMED)
    # The agent read the number back and the caller agreed: queued at once,
    # and asked again for the same thing, still one message.
    handle, first = await _send(_tenant(), call, to="0300 123 4567", number_confirmed=False)
    assert first == live.QUEUED
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert answer == live.QUEUED
    assert len(live.queued_messages(live._pending[call.id])) == 1
    assert sending == [], "sent during the call"
    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4567: queued, to be sent after the call" in saved.summary

    # When the call ends, it goes: once.
    results = await live.flush_queued_messages(_tenant(), call, live._pending[call.id])
    assert results == ["sent (template)"]
    assert [(to, body) for to, body, _ in sending] == [("+923001234567", TEXT)]
    assert sending[0][2]["timeout"] == 5.0
    assert await live.flush_queued_messages(_tenant(), call, live._pending[call.id]) == []
    assert len(sending) == 1
    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4567: sent after the call (template)" in saved.summary


async def test_the_agents_own_read_back_and_a_yes_are_enough(fake_db, sending):
    """READ_BACK_CONFIRMED ends with the agent reading the number back and the
    caller agreeing: that is the confirmation, whatever the tool was told."""
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    _, answer = await _send(_tenant(), call, to="0300 123 4567", number_confirmed=True)
    assert answer == live.QUEUED and sending == []
    assert live.queued_messages(live._pending[call.id])[0]["to"] == "+923001234567"


async def test_without_the_agents_read_back_the_tool_still_asks_for_one(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES + [("agent", "Theek hai."), ("caller", "ji")])
    _, answer = await _send(_tenant(), call, to="0300 123 4567", number_confirmed=True)
    assert "Read this back" in answer and sending == []


async def test_confirmed_but_the_caller_has_not_said_yes_is_not_sent(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES + [("agent", "0300 123 4567, sahi hai?"),
                                        ("caller", "nahi, 0300 123 4568")])
    handle, _ = await _send(_tenant(), call, to="0300 123 4567", number_confirmed=False)
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert "Read this back" in answer and sending == []


async def test_a_ten_digit_number_is_refused(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES + [("caller", "0300123456")])
    _, answer = await _send(_tenant(), call, to="0300123456", number_confirmed=True)
    assert answer == "Not sent: that number looks incomplete. Ask for it again."
    assert sending == []


async def test_a_number_the_caller_never_said_is_refused(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES[:4])            # consent given, no number yet
    _, answer = await _send(_tenant(), call, to="0300 999 8877", number_confirmed=True)
    assert answer == live.NOT_SPOKEN and sending == []


async def test_a_number_other_than_the_one_the_caller_gave_is_redirected(fake_db, sending):
    """They said 0300 123 4567; the model passes another: confirm theirs."""
    call = _call()
    _on_the_line(call, OFFER_AND_YES)
    _, answer = await _send(_tenant(), call, to="0300 999 8877", number_confirmed=True)
    assert sending == []
    assert "the caller last gave 0300 123 4567, not 0300 999 8877" in answer


async def test_urdu_digits_count_as_said(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES[:4] + [("caller", "۰۳۰۰ ۱۲۳ ۴۵۶۷")])
    _, answer = await _send(_tenant(), call, to="0300 123 4567")
    assert "Read this back" in answer


async def test_calling_number_without_the_caller_saying_so_is_refused(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES[:4])
    _, answer = await _send(_tenant(), call, to="calling_number", number_confirmed=True)
    assert answer == live.NOT_SAME_NUMBER and sending == []


async def test_calling_number_the_caller_asked_for_is_read_back_then_sent(fake_db, sending):
    call = _call()
    _on_the_line(call, OFFER_AND_YES[:4] + [("caller", "isi number par bhej dein"),
                                            ("agent", "1020 par khatam hone wale number par?"),
                                            ("caller", "ji")])
    # The agent has named the line by its ending and the caller said ji:
    # queued on the first call, no second read-back.
    handle, first = await _send(_tenant(), call, to="calling_number")
    assert first == live.QUEUED and sending == []
    assert live.queued_messages(live._pending[call.id])[0]["to"] == "+923191611020"


# ---------------------------------------------------------------------------
# The number is the one the caller chose, not the one the model passed
# ---------------------------------------------------------------------------
#
# On a real call the caller gave a number for a test-drive request, then
# asked for the WhatsApp on "the number I'm calling from". The model passed
# the earlier number; it had been confirmed for the request, so the tool
# would have sent to it without a read-back.

GAVE_FOR_REQUEST = [
    ("caller", "Mera naam Alif hai aur mera number 0300 123 4567 hai, Lahore, ATTO 2"),
    ("agent", "...sahi hai?"), ("caller", "ji bilkul sahi hai"),
]
CHOSE_CALLING_NUMBER = GAVE_FOR_REQUEST + [
    ("agent", "Kya main yeh details aapko WhatsApp par bhej doon?"), ("caller", "ji bhej dein"),
    ("agent", "Kis number par bhejun?"), ("caller", "jis number se main call kar raha hoon, isi number par"),
]


async def test_the_calling_number_the_caller_chose_is_read_back_not_the_earlier_one(fake_db, sending):
    call = _call(counterparty="+923011114604")
    _on_the_line(call, CHOSE_CALLING_NUMBER)
    handle = live._tool_handler(_tenant(), call)
    # The earlier number is already confirmed on this call (the request).
    handle_pending = await handle("record_request", {
        "type": "test_drive", "name": "Alif", "mobile": "0300 123 4567", "city": "Lahore",
        "vehicle_model": "ATTO 2", "preferred_times": ["Sat 12"], "details": "test drive",
        "confirmed": True})
    assert "Read this back" in handle_pending
    live._sessions[call.id].transcript = list(CHOSE_CALLING_NUMBER)
    # (The record confirmation needs the last caller turn to be a yes.)
    live._sessions[call.id].transcript.append(("caller", "ji"))
    recorded = await handle("record_request", {
        "type": "test_drive", "name": "Alif", "mobile": "0300 123 4567", "city": "Lahore",
        "vehicle_model": "ATTO 2", "preferred_times": ["Sat 12"], "details": "test drive",
        "confirmed": True})
    assert recorded.startswith("Recorded.")
    live._sessions[call.id].transcript = list(CHOSE_CALLING_NUMBER)

    # The model substitutes the earlier, already-confirmed number.
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert sending == [], "sent to the wrong number"
    assert "the caller chose the number they are calling from, not 0300 123 4567" in answer
    assert "the number ending 4604" in answer
    assert "to calling_number and number_confirmed true" in answer

    # Read back as the tool said, the caller says yes, and it goes to the right one.
    live._sessions[call.id].transcript += [("agent", "4604 par khatam hone wale number par?"),
                                           ("caller", "ji")]
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "calling_number", "number_confirmed": True})
    assert answer == live.QUEUED
    assert sending == []
    await live.flush_queued_messages(_tenant(), call, live._pending[call.id])
    assert [to for to, _, _ in sending] == ["+923011114604"]


async def test_a_number_dictated_after_choosing_the_calling_number_wins(fake_db, sending):
    call = _call(counterparty="+923011114604")
    _on_the_line(call, OFFER_AND_YES[:4] + [
        ("caller", "isi number par"), ("agent", "4604 wale par?"),
        ("caller", "nahi, 0300 999 8877 par bhej dein")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert sending == []
    assert "the caller last gave 0300 999 8877, not the number they are calling from" in answer
    assert "confirm: 0300 999 8877" in answer


async def test_a_number_other_than_the_one_just_dictated_is_not_sent_to(fake_db, sending):
    call = _call()
    _on_the_line(call, GAVE_FOR_REQUEST + [
        ("agent", "Kya main WhatsApp par bhej doon?"), ("caller", "ji"),
        ("agent", "Kis number par?"), ("caller", "0300 999 8877 par"),
        ("agent", "0300 999 8877, sahi hai?"), ("caller", "ji")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert sending == []
    assert "the caller last gave 0300 999 8877, not 0300 123 4567" in answer


async def test_that_number_means_the_one_given_before(fake_db, sending):
    call = _call(counterparty="+923011114604")
    _on_the_line(call, GAVE_FOR_REQUEST + [
        ("agent", "Kya main WhatsApp par bhej doon?"), ("caller", "ji"),
        ("agent", "Isi number par ya kisi aur par?"), ("caller", "wahi number jo maine bataya tha")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("send_whatsapp_message", {"text": TEXT, "to": "calling_number"})
    assert sending == []
    assert "the caller last gave 0300 123 4567" in answer


def test_a_dictated_number_is_found_in_a_sentence():
    from app.services.agent import phones, turns

    assert phones.find_number("mera number 0300 123 4567 hai", "92") == ("+923001234567", "03001234567")
    assert phones.find_number("call me on +92 300-1234567", "") == ("+923001234567", "03001234567")
    assert phones.find_number("۰۳۰۰ ۱۲۳ ۴۵۶۷ par", "92") == ("+923001234567", "03001234567")
    assert phones.find_number("SEAL Premium PKR 16,990,000 aur advance 3,350,000", "92") is None
    assert phones.find_number("0300", "92") is None
    assert turns.mentions_calling_number("jis number se main call kar raha hoon")
    assert turns.mentions_calling_number("इसी नंबर पर भेज दें")
    assert turns.mentions_calling_number("اسی نمبر پر")
    assert not turns.mentions_calling_number("wahi number jo maine bataya")
    assert turns.mentions_earlier_number("wahi number jo maine bataya")


# ---------------------------------------------------------------------------
# When it does not send
# ---------------------------------------------------------------------------

async def test_a_failed_send_never_promises_it_later(fake_db, monkeypatch):
    from app.repositories import calls as call_repo
    from app.services import lines

    class FakeWhatsApp:
        def __init__(self, tenant): ...

        async def send_text(self, *a, **kw):
            raise RuntimeError("Meta said no")

        send_template = send_text

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant

    monkeypatch.setattr(lines, "whatsapp_sender", sender)
    call = _call()
    await call_repo.save_call(call)
    _on_the_line(call, READ_BACK_CONFIRMED)
    handle, _ = await _send(_tenant(), call, to="0300 123 4567")
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert answer == live.QUEUED
    results = await live.flush_queued_messages(_tenant(), call, live._pending[call.id])
    assert results == ["not sent (could not be sent: Meta said no)"]
    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4567: not sent (could not be sent: Meta said no)" in saved.summary
    assert "Sent" not in saved.summary


async def test_no_whatsapp_number_is_said_plainly(fake_db, monkeypatch):
    from app.services import lines

    async def none(_tenant):
        return None

    monkeypatch.setattr(lines, "whatsapp_sender", none)
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    handle, _ = await _send(_tenant(), call, to="0300 123 4567")
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert answer == live.NO_SENDER and "no WhatsApp number" in answer


# ---------------------------------------------------------------------------
# The health gate: a dead token takes the tool away
# ---------------------------------------------------------------------------

def _whatsapp_number(**extra):
    from app.models.number import NumberKind, NumberStatus, PhoneNumber

    return PhoneNumber(id="wa1", tenantId="t-1", kind=NumberKind.WHATSAPP,
                       phoneNumber="+923000000001", status=NumberStatus.VERIFIED,
                       metaPhoneNumberId="meta-1", wabaId="waba-1",
                       accessToken="tok", appSecret="sec", **extra)


async def test_a_401_marks_the_line_failed_and_the_tool_disappears(fake_db, monkeypatch):
    from app.models.number import NumberStatus
    from app.repositories import calls as call_repo
    from app.repositories import numbers as number_repo
    from app.services import lines

    await number_repo.save(_whatsapp_number())
    tenant = _tenant()
    call = _call()
    await call_repo.save_call(call)

    # Before: the number sends, so the tool is offered.
    before = await live.persona_for(tenant, call)
    assert "send_whatsapp_message" in [t["name"] for t in before.tools]

    class FakeWhatsApp:
        def __init__(self, tenant): ...

        async def send_text(self, *a, **kw):
            raise UpstreamError("WhatsApp refused the request: Error validating access token",
                                http_status=401, provider_code=190)

        send_template = send_text

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)
    _on_the_line(call, READ_BACK_CONFIRMED)
    handle, _ = await _send(tenant, call, to="0300 123 4567")
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert answer == live.QUEUED
    assert await live.flush_queued_messages(tenant, call, live._pending[call.id]) == [
        "not sent (token rejected)"]

    number = await number_repo.get("t-1", "wa1")
    assert number.status is NumberStatus.FAILED
    assert number.status_detail == lines.META_TOKEN_REJECTED
    assert number.send_auth_failed_at
    saved = await call_repo.get_call("t-1", call.id)
    assert "not sent (token rejected)" in saved.summary

    # After: the next call is not offered the tool at all.
    after = await live.persona_for(tenant, _call())
    assert "send_whatsapp_message" not in [t["name"] for t in after.tools]
    assert "send_whatsapp_message" not in after.instructions

    # Until a new token verifies.
    async def meta_says_yes(_t):
        return "Meta confirmed +923000000001."

    monkeypatch.setattr(lines, "_verify_meta", meta_says_yes)
    verified = await lines.verify(tenant, number)
    assert verified.verified and verified.send_auth_failed_at is None
    again = await live.persona_for(tenant, _call())
    assert "send_whatsapp_message" in [t["name"] for t in again.tools]


async def test_a_recent_auth_failure_hides_the_tool_even_before_the_row_is_read(fake_db):
    import time

    from app.repositories import numbers as number_repo
    from app.services import lines

    await number_repo.save(_whatsapp_number(sendAuthFailedAt=time.time() - 60))
    assert await lines.whatsapp_sender(_tenant()) is None
    await number_repo.save(_whatsapp_number(sendAuthFailedAt=time.time() - 3600))
    assert await lines.whatsapp_sender(_tenant()) is not None


def test_auth_rejected_reads_a_401_or_graph_error_190():
    from app.services import whatsapp

    assert whatsapp.auth_rejected(UpstreamError("x", http_status=401))
    assert whatsapp.auth_rejected(UpstreamError("x", http_status=400, provider_code=190))
    assert not whatsapp.auth_rejected(UpstreamError("x", http_status=400, provider_code=131026))
    assert not whatsapp.auth_rejected(RuntimeError("x"))


# ---------------------------------------------------------------------------
# How it reaches a phone caller: the approved template
# ---------------------------------------------------------------------------
#
# Meta delivers a plain text only inside the 24-hour window after the person
# messaged the business. A phone caller usually has not, so the send was
# accepted and never delivered: "sent", and nothing arrived.


async def _confirmed_send(tenant, call, sending_list, **args):
    """Queue through the tool as a call does, then send as the call's end
    does, so `sending_list` holds what reached the provider."""
    handle = live._tool_handler(tenant, call)
    await handle("send_whatsapp_message", {"text": args.get("text", TEXT), "to": "0300 123 4567"})
    answer = await handle("send_whatsapp_message",
                          {"text": args.get("text", TEXT), "to": "0300 123 4567",
                           "number_confirmed": True})
    if answer == live.QUEUED:
        await live.flush_queued_messages(tenant, call, live._pending[call.id])
    return answer


async def test_a_caller_who_never_messaged_gets_the_approved_template(fake_db, sending):
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    company = Tenant(phoneNumberId="t-1", name="Acme Motors", defaultCountryCode="92")
    assert await _confirmed_send(company, call, sending) == live.QUEUED
    [(to, body, how)] = sending
    assert how["kind"] == "template"
    assert how["template"] == "call_followup" and how["language"] == "en"
    assert how["parameters"] == ["Acme Motors", TEXT]
    assert how["body"] == TEXT and how["timeout"] == 5.0
    assert to == "+923001234567"


async def test_a_caller_inside_the_24_hour_window_gets_a_plain_text(fake_db, sending):
    import time

    from app.models.call import Message, MessageDirection
    from app.repositories import calls as call_repo

    await call_repo.save_message(Message(
        tenantId="t-1", direction=MessageDirection.INBOUND, counterparty="+923001234567",
        body="salam", createdAt=time.time() - 3600))
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    assert await _confirmed_send(_tenant(), call, sending) == live.QUEUED
    assert sending[0][2]["kind"] == "text"


async def test_a_message_older_than_24_hours_does_not_open_the_window(fake_db, sending):
    import time

    from app.models.call import Message, MessageDirection
    from app.repositories import calls as call_repo

    await call_repo.save_message(Message(
        tenantId="t-1", direction=MessageDirection.INBOUND, counterparty="+923001234567",
        body="salam", createdAt=time.time() - 25 * 3600))
    # And our own message to them does not count either.
    await call_repo.save_message(Message(
        tenantId="t-1", direction=MessageDirection.OUTBOUND, counterparty="+923001234567",
        body="hello", createdAt=time.time() - 60))
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    await _confirmed_send(_tenant(), call, sending)
    assert sending[0][2]["kind"] == "template"


async def test_the_template_is_signed_with_the_agents_name(fake_db, sending):
    """A real follow-up arrived "during your call with Calling Agent", the
    account's name. {{1}} is the agent the caller spoke to."""
    from app.models.agent import Agent
    from app.repositories import agents as agent_repo

    agent = await agent_repo.save_agent(Agent(tenantId="t-1", name="BYD Pakistan"))
    call = _call(agentId=agent.id)
    _on_the_line(call, READ_BACK_CONFIRMED)
    company = Tenant(phoneNumberId="t-1", name="Calling Agent", defaultCountryCode="92")
    assert await _confirmed_send(company, call, sending) == live.QUEUED
    assert sending[0][2]["parameters"][0] == "BYD Pakistan"


async def test_the_company_name_is_used_only_when_the_agent_has_no_name(fake_db, sending):
    from app.models.agent import Agent
    from app.repositories import agents as agent_repo

    unnamed = await agent_repo.save_agent(Agent(tenantId="t-1", name="   "))
    company = Tenant(phoneNumberId="t-1", name="Calling Agent", defaultCountryCode="92")
    call = _call(agentId=unnamed.id)
    _on_the_line(call, READ_BACK_CONFIRMED)
    await _confirmed_send(company, call, sending)
    assert sending[0][2]["parameters"][0] == "Calling Agent"

    no_agent = _call()
    _on_the_line(no_agent, READ_BACK_CONFIRMED)
    await _confirmed_send(company, no_agent, sending)
    assert sending[1][2]["parameters"][0] == "Calling Agent"


REAL_FOLLOW_UP = (
    "Assalam-o-Alaikum, Alif! BYD Pakistan Virtual Assistant here.\n"
    "Here are some details on our models:\n"
    "ATTO 3: PKR 8,990,000\n"
    "SEAL Dynamic: PKR 14,790,000\n"
    "SEAL Premium: PKR 16,990,000\n"
    "Drive safe!"
)
DETAILS_ONLY = (
    "Here are some details on our models:\n"
    "ATTO 3: PKR 8,990,000\n"
    "SEAL Dynamic: PKR 14,790,000\n"
    "SEAL Premium: PKR 16,990,000"
)


def test_a_greeting_an_introduction_and_a_sign_off_are_stripped_and_every_price_stays():
    from app.services.whatsapp import strip_pleasantries

    assert strip_pleasantries(REAL_FOLLOW_UP) == DETAILS_ONLY
    assert strip_pleasantries("Hello! Salam Alif.\nATTO 2: PKR 7,290,000\nThank you. Allah hafiz!") == (
        "ATTO 2: PKR 7,290,000")


@pytest.mark.parametrize("text", [
    "Thank you for choosing the ATTO 3 at PKR 8,990,000.",
    "Hello, our helpline is 0300 123 4567.\nATTO 3: PKR 8,990,000\nCall 0300 123 4567 anytime, thanks.",
    "ATTO 2: PKR 7,290,000\nDrive safe in your 2026 ATTO 3!",
    "Advance: 20%\nDelivery: tentative",
    "SEAL Premium: PKR 16,990,000. Booking advance 3,350,000.",
])
def test_a_sentence_with_a_number_or_a_detail_is_never_stripped(text):
    from app.services.whatsapp import strip_pleasantries

    assert strip_pleasantries(text) == text


def test_a_text_that_is_only_pleasantries_is_left_alone():
    from app.services.whatsapp import strip_pleasantries

    assert strip_pleasantries("Hello! Thank you!") == "Hello! Thank you!"
    assert strip_pleasantries("Drive safe!") == "Drive safe!"
    assert strip_pleasantries("") == ""


async def test_the_sent_parameter_is_the_details_only(fake_db, sending):
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    assert await _confirmed_send(_tenant(), call, sending, text=REAL_FOLLOW_UP) == live.QUEUED
    assert sending[0][2]["parameters"][1] == (
        "Here are some details on our models: · ATTO 3: PKR 8,990,000 · "
        "SEAL Dynamic: PKR 14,790,000 · SEAL Premium: PKR 16,990,000")


def test_the_tool_and_the_writing_rules_say_details_only_and_every_item_quoted():
    description = tools.SEND_WHATSAPP_MESSAGE["description"]
    assert "no greeting, no self-introduction, no caller's name, no sign-off" in description
    assert "the message template already greets and closes" in description
    assert "include every item you told them on the call for that topic" in description
    assert "stay within 900 characters" in description
    assert "the rest is available on request" in description
    text = " ".join(prompt.build(persona="P", context="C", can_send_whatsapp=True).split())
    assert "The text is only the details themselves" in text
    assert "no greeting, no self-introduction, no caller's name, no sign-off" in text
    assert "Include every item you quoted" in text
    assert "every model and price you quoted" in text
    assert "ATTO 2: PKR 7,290,000" in text


def test_the_template_parameter_is_cleaned_for_meta():
    from app.services.whatsapp import MAX_PARAMETER_CHARS, clean_parameter

    assert clean_parameter("Line one\nLine two\t\tthree      spaced\r\n") == (
        "Line one · Line two · three    spaced")
    assert clean_parameter("a" * 2000).endswith("…")
    assert len(clean_parameter("a" * 2000)) == MAX_PARAMETER_CHARS
    assert clean_parameter("   \n\t ") == "Details from your call."
    assert clean_parameter("fine as it is") == "fine as it is"


async def test_a_message_with_newlines_is_cleaned_before_sending(fake_db, sending):
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
    text = "SEAL Premium\nPKR 16,990,000\nAdvance 3,350,000"
    assert await _confirmed_send(_tenant(), call, sending, text=text) == live.QUEUED
    assert sending[0][2]["parameters"][1] == "SEAL Premium · PKR 16,990,000 · Advance 3,350,000"


async def test_metas_refusal_is_reported_with_its_reason(fake_db, monkeypatch, caplog):
    import logging

    from app.repositories import calls as call_repo
    from app.services import lines

    class FakeWhatsApp:
        def __init__(self, tenant): ...

        async def send_template(self, *a, **kw):
            raise UpstreamError("WhatsApp refused the request: Template name does not exist in the translation",
                                details="Template name does not exist in the translation",
                                http_status=400, provider_code=132001)

        async def send_text(self, *a, **kw):
            raise AssertionError("a phone caller must get the template")

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)

    async def sender(tenant):
        return tenant

    monkeypatch.setattr(lines, "whatsapp_sender", sender)
    call = _call()
    await call_repo.save_call(call)
    _on_the_line(call, READ_BACK_CONFIRMED)
    with caplog.at_level(logging.WARNING, logger="app.services.agent.live"):
        answer = await _confirmed_send(_tenant(), call, [])
    assert answer == live.QUEUED
    saved = await call_repo.get_call("t-1", call.id)
    assert ("WhatsApp to …4567: not sent (could not be sent: Template name does not exist "
            "in the translation)") in saved.summary
    [line] = [r.getMessage() for r in caplog.records if "did not send" in r.getMessage()]
    assert "Template name does not exist" in line and "…4567" in line
    assert "+923001234567" not in line and "03001234567" not in line


def test_the_follow_up_settings_have_defaults():
    from app.config import Settings

    assert Settings.model_fields["whatsapp_followup_template"].default == "call_followup"
    assert Settings.model_fields["whatsapp_followup_language"].default == "en"


async def test_verify_reports_a_missing_or_unapproved_follow_up_template(fake_db, monkeypatch):
    from app.services import lines, whatsapp

    async def number_ok(_t):
        return "Meta confirmed +923000000001 (Acme)."

    monkeypatch.setattr(lines, "_meta_number_detail", number_ok)
    tenant = _tenant()
    advice = "Create an approved Utility template named call_followup with {{1}} and {{2}}"

    async def none(self, *, timeout=None):
        return []

    monkeypatch.setattr(whatsapp.WhatsApp, "templates", none)
    number = await lines.verify(tenant, _whatsapp_number())
    assert number.verified, "a missing template must not fail the verify"
    assert number.status_detail.startswith("Meta confirmed +923000000001 (Acme).")
    assert advice in number.status_detail

    async def pending(self, *, timeout=None):
        return [{"name": "call_followup", "status": "PENDING", "language": "en"}]

    monkeypatch.setattr(whatsapp.WhatsApp, "templates", pending)
    number = await lines.verify(tenant, _whatsapp_number())
    assert number.verified and "not approved (PENDING)" in number.status_detail

    async def other_language(self, *, timeout=None):
        return [{"name": "call_followup", "status": "APPROVED", "language": "ur"}]

    monkeypatch.setattr(whatsapp.WhatsApp, "templates", other_language)
    number = await lines.verify(tenant, _whatsapp_number())
    assert number.verified and "approved in ur" in number.status_detail
    assert "WHATSAPP_FOLLOWUP_LANGUAGE" in number.status_detail

    async def approved(self, *, timeout=None):
        return [{"name": "call_followup", "status": "APPROVED", "language": "en"}]

    monkeypatch.setattr(whatsapp.WhatsApp, "templates", approved)
    number = await lines.verify(tenant, _whatsapp_number())
    assert number.verified and number.status_detail == "Meta confirmed +923000000001 (Acme)."

    async def unreachable(self, *, timeout=None):
        raise UpstreamError("WhatsApp did not respond")

    monkeypatch.setattr(whatsapp.WhatsApp, "templates", unreachable)
    number = await lines.verify(tenant, _whatsapp_number())
    assert number.verified and "could not be checked" in number.status_detail


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------

def test_the_tool_no_longer_says_it_has_the_number():
    description = tools.SEND_WHATSAPP_MESSAGE["description"]
    assert "already have" not in description
    assert "Do NOT ask them for their number" not in description
    assert "number_confirmed" in description
    assert "Saying yes is not queueing" in description
    assert set(tools.SEND_WHATSAPP_MESSAGE["parameters"]["required"]) == {"text", "to"}
    assert "number_confirmed" in tools.SEND_WHATSAPP_MESSAGE["parameters"]["properties"]


def test_the_writing_rules_follow_the_five_step_flow():
    text = " ".join(prompt.build(persona="You answer calls.", context="Fees: 50,000.",
                                 can_send_whatsapp=True).split())
    assert "Answer on the call first" in text
    assert "Kya main yeh details aapko WhatsApp par bhej doon?" in text
    assert "ask which number to send it to" in text
    assert "Read the number back in groups" in text
    assert "never queue after a no" in text.lower()
    assert "You already have the number" not in text
    assert "never ask which number to use" not in text
    # The "cannot answer: send it on WhatsApp" paragraph is gone; an
    # unanswered question is a recorded request now.
    assert "call send_whatsapp_message with their question written out" not in text


def test_consent_is_read_from_the_last_three_exchanges():
    conversation = [
        ("agent", "Kya main WhatsApp par bhej doon?"), ("caller", "ji"),
        ("agent", "a"), ("caller", "b"), ("agent", "c"), ("caller", "d"),
        ("agent", "e"), ("caller", "f"),
    ]
    assert not turns.consented_to_message(conversation), "a yes from long ago still counted"
    assert turns.consented_to_message(conversation[:2])
