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
    return Call(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923191611020", **extra)


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
    """A WhatsApp sender that records what it was asked to send."""
    from app.services import lines

    sent: list[tuple[str, str, dict]] = []

    class FakeWhatsApp:
        def __init__(self, tenant):
            self.tenant = tenant

        async def send_text(self, to, body, **kwargs):
            sent.append((to, body, kwargs))

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
    handle, first = await _send(_tenant(), call, to="0300 123 4567", number_confirmed=False)
    assert "Read this back" in first
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert answer == "Sent."
    assert [(to, body) for to, body, _ in sending] == [("+923001234567", TEXT)]
    assert sending[0][2]["timeout"] == 5.0, "a send inside a call must not wait longer"

    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4567: sent" in saved.summary


async def test_number_confirmed_true_without_a_read_back_is_still_a_read_back(fake_db, sending):
    call = _call()
    _on_the_line(call, READ_BACK_CONFIRMED)
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
    _on_the_line(call, OFFER_AND_YES)
    _, answer = await _send(_tenant(), call, to="0300 999 8877", number_confirmed=True)
    assert answer == live.NOT_SPOKEN and sending == []


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
    handle, first = await _send(_tenant(), call, to="calling_number")
    assert "the number ending 1020" in first and sending == []
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "calling_number", "number_confirmed": True})
    assert answer == "Sent."
    assert sending[0][0] == "+923191611020"


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
    assert answer == live.NOT_SENT
    assert "follow shortly" not in answer and "Sent" not in answer
    saved = await call_repo.get_call("t-1", call.id)
    assert "WhatsApp to …4567: not sent (could not be sent)" in saved.summary


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

    monkeypatch.setattr("app.services.whatsapp.WhatsApp", FakeWhatsApp)
    _on_the_line(call, READ_BACK_CONFIRMED)
    handle, _ = await _send(tenant, call, to="0300 123 4567")
    answer = await handle("send_whatsapp_message",
                          {"text": TEXT, "to": "0300 123 4567", "number_confirmed": True})
    assert answer == live.NOT_SENT

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
# What the model is told
# ---------------------------------------------------------------------------

def test_the_tool_no_longer_says_it_has_the_number():
    description = tools.SEND_WHATSAPP_MESSAGE["description"]
    assert "already have" not in description
    assert "Do NOT ask them for their number" not in description
    assert "number_confirmed" in description
    assert "Saying yes is not sending" in description
    assert set(tools.SEND_WHATSAPP_MESSAGE["parameters"]["required"]) == {"text", "to"}
    assert "number_confirmed" in tools.SEND_WHATSAPP_MESSAGE["parameters"]["properties"]


def test_the_writing_rules_follow_the_five_step_flow():
    text = " ".join(prompt.build(persona="You answer calls.", context="Fees: 50,000.",
                                 can_send_whatsapp=True).split())
    assert "Answer on the call first" in text
    assert "Kya main yeh details aapko WhatsApp par bhej doon?" in text
    assert "ask which number to send it to" in text
    assert "Read the number back in groups" in text
    assert "never send after a no" in text.lower()
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
