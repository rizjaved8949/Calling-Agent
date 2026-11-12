"""
The conversation simulator, run against a scripted model.

Proves the plumbing without a key: the real tool handler and transcript
watch run, the dry-run stubs hand out references without writing a row and
say Sent without sending, and the checks catch what they are there for.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from app.models.tenant import Tenant
from app.repositories import requests as request_repo
from app.services.agent import live
from app.services.agent.gemini import AgentPersona

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "simulate_calls.py"
spec = importlib.util.spec_from_file_location("simulate_calls", SCRIPT)
sim = importlib.util.module_from_spec(spec)
sys.modules["simulate_calls"] = sim                 # dataclasses resolve annotations by module
spec.loader.exec_module(sim)

MATERIAL = ("SEAL Premium: PKR 16,990,000 (1 crore 69 lakh 90 hazar). ATTO 3 Advance: PKR "
            "8,990,000 (89 lakh 90 hazar). Advance at booking: PKR 1,750,000 (17 lakh 50 hazar).")


def _tenant() -> Tenant:
    return Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92", recordCalls=False)


class ScriptedModel(sim.Model):
    """Answers from a script: each caller line gets the next reply, which may
    first call tools (name, args) and then say something."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.user: list[str] = []
        self.notes: list[tuple[str, bool]] = []

    async def send_user(self, text):
        self.user.append(text)

    async def note(self, text, *, urgent=False):
        self.notes.append((text, urgent))

    async def exchange(self):
        if not self.replies:
            return sim.Exchange([], [])
        step = self.replies.pop(0)
        tools = []
        texts = []
        for item in step:
            if isinstance(item, tuple):
                name, args = item
                result = await self.on_tool(name, args)
                tools.append((name, args, result))
            else:
                texts.append(item)
        return sim.Exchange(texts, tools)


@pytest.fixture
def dry(fake_db):
    runner = sim.DryRun()
    runner.install()
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()
    yield runner
    runner.remove()
    live._sessions.clear()
    live._recorded.clear()
    live._pending.clear()


def test_twelve_scenarios_with_both_genders():
    assert len(sim.SCENARIOS) == 12
    assert {s.gender for s in sim.SCENARIOS} == {"male", "female"}
    assert len({s.key for s in sim.SCENARIOS}) == 12
    assert all(any("hafiz" in line.lower() for line in s.lines) for s in sim.SCENARIOS)


async def test_a_complaint_run_with_the_real_tools_and_a_dry_recorder(dry, fake_db, monkeypatch):
    monkeypatch.setattr(live, "GOODBYE_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(live, "TRANSCRIPT_WAIT_SECONDS", 0.05)
    scenario = next(s for s in sim.SCENARIOS if s.key == "complaint")
    persona = AgentPersona(instructions=MATERIAL, greeting="Acme, how can I help?")
    details = {"type": "complaint", "name": "Suman", "mobile": "0300 1234567",
               "vehicle_model": "ATTO 3", "registration": "112255",
               "details": "charging start nahi hoti"}
    model = ScriptedModel([
        ["Acme, how can I help?"],
        ["Suman, maazrat is pareshani ke liye. Kya aap apna mobile number batayengi?"],
        ["Maazrat, is number mein das digits hain; gyarah hote hain. Dobara batayengi?"],
        ["Shukriya. Registration number kya hai?"],
        [("record_request", {k: v for k, v in details.items()}),
         "Suman, aap ka number zero three zero zero, one two three four five six seven, ATTO 3, "
         "registration 112255, charging start nahi hoti. Kya yeh sahi hai?"],
        [("record_request", {**details, "confirmed": True}),
         "Aap ki shikayat darj ho gayi hai, reference CMP-{ref}. Team aap se ek working day mein "
         "rabta karegi. Aur koi sawal?"],
        [("end_call", {"reason": "the caller said goodbye"}),
         "Acme ko call karne ka shukriya. Allah hafiz."],
    ])

    # The scripted reply quotes the reference the dry recorder hands out.
    original_exchange = model.exchange

    async def exchange():
        result = await original_exchange()
        result.texts = [t.replace("CMP-{ref}", dry.records[-1].reference if dry.records else "none")
                        for t in result.texts]
        return result

    model.exchange = exchange
    run = await sim.converse(scenario, 0, _tenant(), persona, model, dry)
    assert run.error == ""
    assert run.references and run.references[0].startswith("CMP-")
    assert fake_db.tables.get(request_repo.TABLE, {}) == {}, "a dry run wrote a row"
    # The ten-digit note reached the model, urgently, before its answer.
    assert any("has 10 digits" in n and urgent for n, urgent in model.notes)
    assert run.ended == "the caller said goodbye"
    assert sim.check(run, MATERIAL) == []


def test_the_checks_catch_each_failure():
    scenario = next(s for s in sim.SCENARIOS if s.key == "complaint")
    run = sim.Run(scenario, 0)
    run.turns = [
        sim.Turn("agent", "Acme, how can I help?"),
        sim.Turn("caller", scenario.lines[0]),
        sim.Turn("agent", "Kya aap apna number batayenge? Aur model kya hai?"),      # two questions, male form
        sim.Turn("caller", scenario.lines[1]),
        sim.Turn("agent", "Aap ka number zero three zero zero one two three four five six? Theek hai?"),
        sim.Turn("caller", scenario.lines[2]),
        sim.Turn("agent", "calculate{expression:1 Shukriya. ATTO 3 Advance ki price 8,990,000 hai, "
                          "ya 2 crore 10 lakh. Aap ki shikayat team ko bhej di hai."),
        sim.Turn("caller", scenario.lines[-1]),
        sim.Turn("agent", "Acme ko call karne ka shukriya. Allah hafiz. Acme ko call karne ka "
                          "shukriya. Allah hafiz."),
    ]
    failures = sim.check(run, MATERIAL)
    joined = "\n".join(failures)
    assert "two questions in one turn" in joined
    assert "wrong gender form for a female caller" in joined
    assert "the short number was not challenged" in joined
    assert "a tool name or bracket in speech" in joined
    assert "an amount not in the material: '2 crore 10 lakh'" in joined
    assert "an amount said in digits, not words: '8,990,000'" in joined
    assert "claimed recorded or sent with no reference" in joined
    assert "no request was recorded" in joined
    assert "the closing line was said 2 times" in joined
    assert "the call did not end" in joined


def test_spoken_amounts_are_read_as_phrases():
    assert sim.spoken_amounts("SEAL ki price 1 crore 47 lakh 90 hazar rupay hai aur advance 17 lakh 50 hazar.") \
        == ["1 crore 47 lakh 90 hazar", "17 lakh 50 hazar"]
    assert sim.spoken_amounts("koi raqam nahi") == []


async def test_a_dry_send_says_sent_without_sending(dry, fake_db):
    from app.models.call import Call, Channel, Direction

    tenant = _tenant()
    call = Call(tenantId="t-1", channel=Channel.PHONE, direction=Direction.INBOUND,
                counterparty="+923001234567")
    session = sim.SimSession(call.id, ScriptedModel([]))
    live._sessions[call.id] = session
    for who, text in [("agent", "Details WhatsApp par bhej doon?"), ("caller", "ji, isi number par"),
                      ("agent", "Number ending 4567 par bhej doon?"), ("caller", "ji haan")]:
        session._note(who, text)
    handle = live._tool_handler(tenant, call)
    first = await handle("send_whatsapp_message", {"text": "SEAL: PKR 16,990,000", "to": "calling_number"})
    assert "Read this back" in first
    answer = await handle("send_whatsapp_message",
                          {"text": "SEAL: PKR 16,990,000", "to": "calling_number", "number_confirmed": True})
    assert answer == "Sent."
    assert dry.sent == [("+923001234567", "SEAL: PKR 16,990,000")]
