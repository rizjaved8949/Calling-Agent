"""
Transcripts in one readable style: Urdu script respelled as Roman Urdu.

A transcript that mixes Urdu script, Roman Urdu and English line by line is
three transcripts interleaved. Urdu-script lines now go to the same text
model as Devanagari ones, with the same checks; a line the model cannot be
trusted with is kept exactly as heard.
"""
from __future__ import annotations

import pytest

from app.services.agent import script

URDU = "میرا نام علی ہے اور میرا نمبر 0300 1234567 ہے"
ROMAN = "Mera naam Ali hai aur mera number 0300 1234567 hai"


def test_urdu_script_is_recognised_and_needs_respelling():
    assert script.has_arabic_script(URDU)
    assert not script.has_arabic_script(ROMAN)
    assert script.needs_respelling(URDU)
    assert script.needs_respelling("बस मुझे बता दें")
    assert not script.needs_respelling("Assalam o alaikum, 0300 1234567")


def test_readable_does_not_invent_a_letter_by_letter_form_for_urdu_script():
    assert script.readable(URDU) == URDU


async def test_a_mixed_script_transcript_comes_out_roman(monkeypatch):
    sent: list[list[str]] = []

    async def model(lines, **_k):
        sent.append(lines)
        return ["Assalam o alaikum", ROMAN]

    monkeypatch.setattr(script, "_ask_model", model)
    out = await script.to_roman_urdu(
        ["السلام علیکم", "Walaikum assalam, main kaise madad karun?", URDU],
        api_key="k", model="m")
    assert sent == [["السلام علیکم", URDU]], "only the Urdu-script lines go to the model"
    assert out == ["Assalam o alaikum", "Walaikum assalam, main kaise madad karun?", ROMAN]


@pytest.mark.parametrize("bad", [
    "Mera naam Ali hai aur mera number 0300 1234568 hai",   # a digit changed
    "Mera naam Ali hai",                                    # half of it dropped
    "My name is Ali and my number is 0300 1234567",         # translated
    "میرا نام علی ہے اور میرا نمبر 0300 1234567 ہے",        # still Urdu script
    "",
])
async def test_a_version_that_changes_what_was_said_keeps_the_original(monkeypatch, bad):
    async def model(_lines, **_k):
        return [bad]

    monkeypatch.setattr(script, "_ask_model", model)
    assert await script.to_roman_urdu([URDU], api_key="k", model="m") == [URDU]


async def test_a_model_failure_keeps_the_line(monkeypatch):
    async def broken(_lines, **_k):
        raise RuntimeError("OUT OF QUOTA")

    monkeypatch.setattr(script, "_ask_model", broken)
    assert await script.to_roman_urdu([URDU], api_key="k", model="m") == [URDU]
    assert await script.to_roman_urdu([URDU], api_key="", model="m") == [URDU]


def test_numbers_in_urdu_digits_are_preserved():
    assert script.faithful("میرے پاس ۵۰۰ روپے ہیں", "Mere paas 500 rupay hain")
    assert not script.faithful("میرے پاس ۵۰۰ روپے ہیں", "Mere paas 600 rupay hain")


async def test_a_finished_call_in_urdu_script_is_respelled_and_the_original_kept(
        fake_db, monkeypatch):
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.models.tenant import Tenant
    from app.repositories import calls as call_repo
    from app.services.agent import live
    from app.services.agent.gemini import LiveSettings

    async def settings_now(_tenant=None):
        return LiveSettings(model="live", api_key="k")

    async def model(_lines, **_k):
        return [ROMAN]

    monkeypatch.setattr(live, "live_settings_now", settings_now)
    monkeypatch.setattr(script, "_ask_model", model)
    tenant = Tenant(phoneNumberId="900", name="Acme")
    transcript = f"caller: {URDU}\nagent: Shukriya Ali."
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.COMPLETED, counterparty="+923001112222",
                transcript=transcript)
    await call_repo.save_call(call)
    await live._respell_in_roman_urdu(tenant, call.id, transcript)
    saved = await call_repo.get_call("900", call.id)
    assert saved.transcript == f"caller: {ROMAN}\nagent: Shukriya Ali."
    assert saved.transcript_original == transcript


async def test_the_settled_call_schedules_the_respelling_for_urdu_script(fake_db, monkeypatch):
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.models.tenant import Tenant
    from app.repositories import calls as call_repo
    from app.services.agent import live

    scheduled: list[str] = []

    async def respell(_tenant, call_id, _transcript):
        scheduled.append(call_id)

    monkeypatch.setattr(live, "_respell_in_roman_urdu", respell)

    class Session:
        handler = type("H", (), {"value": "agent"})()
        frames_out = 10

        async def end(self, _reason):
            return None

        def transcript_text(self):
            return f"caller: {URDU}"

    tenant = Tenant(phoneNumberId="900", name="Acme", recordCalls=False)
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.IN_PROGRESS, counterparty="+923001112222", answeredAt=1.0)
    await call_repo.save_call(call)
    live._sessions[call.id] = Session()
    await live.finish(tenant, call.id, "done")
    import asyncio

    await asyncio.sleep(0)
    assert scheduled == [call.id]
