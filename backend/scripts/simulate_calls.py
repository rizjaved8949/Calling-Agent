#!/usr/bin/env python3
"""
Talk to the real agent in text, scenario by scenario, and check what it did.

    python scripts/simulate_calls.py --tenant <phone number id>
    python scripts/simulate_calls.py --tenant <id> --agent <agent id> --runs 3
    python scripts/simulate_calls.py --tenant <id> --only 6,7 --runs 1
    python scripts/simulate_calls.py --tenant <id> --engine text    # generate_content, not Live
    python scripts/simulate_calls.py --list

The agent is the real one: the same prompt blocks, the same material from the
database (`.env` in this folder), the same tools and the same server-side
guards a phone call gets — the transcript watch, the end-call guard, the
read-back rules. Only the voice is missing: the caller's lines are typed, the
model answers in text through the Live API (or the text model with
``--engine text``), and the caller's turn is marked finished the moment it is
sent, as the recogniser does on a call.

Nothing real happens. record_request hands out references without writing a
row, send_whatsapp_message says Sent without sending, the call never touches
the calls table, and no message leaves; every check the tools make still runs.

Each conversation is printed with its tool calls, the silent notes the server
put in front of the model, and the guard's log lines; then every check, and a
scenario-by-run table at the end. Exit status 1 when any check failed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from app.models.call import Call, Channel, Direction  # noqa: E402
from app.models.request import PREFIXES, RecordedRequest, RequestType  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.services.agent import amounts, live, phones, script  # noqa: E402
from app.services.agent import turns as turns_mod  # noqa: E402
from app.services.agent.gemini import AgentPersona, LiveSettings  # noqa: E402

log = logging.getLogger("simulate_calls")

GREEN, YELLOW, RED, DIM, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


# ---------------------------------------------------------------------------
# Scenarios: what a caller says, as callers say it
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    gender: str                      # "male" or "female": the caller's
    lines: tuple[str, ...]
    request: str = ""                # the reference prefix a request must get, or ""
    whatsapp: bool = False           # a message must be sent (dry)
    number: str = ""                 # the mobile the caller gives, if any
    short_number: str = ""           # a too-short mobile the caller gives first
    short_line: int = -1             # which line says it


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("price", "Price and advance (SEAL Premium)", "male", (
        "Assalam o alaikum, main Ahmed bol raha hoon. SEAL Premium ki price kya hai aur "
        "booking ke waqt advance kitna dena hota hai?",
        "Theek hai, aur delivery kab tak milti hai?",
        "Nahi shukriya, Allah hafiz.",
    )),
    Scenario("financing", "Financing: banks and a SEAL Dynamic instalment", "female", (
        "Hello, main Sana bol rahi hoon. Financing ke liye aap ka kaun se banks ke saath "
        "arrangement hai?",
        "SEAL Dynamic par agar main paanch saal ki financing loon to monthly instalment "
        "kitni banegi?",
        "Theek hai, bas itna hi. Allah hafiz.",
    )),
    Scenario("family", "Family recommendation, 1 crore 50 lakh budget", "male", (
        "Mujhe family ke liye gaari chahiye. Budget 1 crore 50 lakh tak hai, rozana 40 "
        "kilometre chalti hai, ghar mein parking hai aur hum paanch log hote hain.",
        "In mein se kaunsi behtar rahegi?",
        "Theek hai shukriya, Allah hafiz.",
    )),
    Scenario("whatsapp", "WhatsApp details to 'isi number par'", "female", (
        "Main Hira bol rahi hoon. SEAL ke saare variants ki prices mujhe WhatsApp par "
        "bhej dein.",
        "Isi number par bhej dein jis se main call kar rahi hoon.",
        "Ji haan, bilkul.",
        "Shukriya, Allah hafiz.",
    ), whatsapp=True),
    Scenario("test_drive", "Test drive ATTO 3, optional question declined", "male", (
        "Mujhe ATTO 3 ka test drive book karna hai.",
        "Mera naam Ali hai aur mobile number 03001234567 hai. Lahore mein, is hafte "
        "Saturday subah gyarah baje.",
        "Baaki cheezein main baad mein decide karunga. Jo details di hain wohi sahi hain.",
        "Ji, bilkul sahi hai.",
        "Nahi shukriya, Allah hafiz.",
    ), request="TD", number="03001234567"),
    Scenario("complaint", "Complaint: ATTO 3 charge nahi ho rahi, short number first", "female", (
        "Meri ATTO 3 charge nahi ho rahi, shikayat darj karni hai. Mera naam Suman hai.",
        "0300123456",
        "Maazrat, number 0300 1234567 hai.",
        "Registration number 112255 hai. Charger lagane par charging start hi nahi hoti, "
        "ghar par bhi aur public station par bhi.",
        "Ji, theek hai.",
        "Nahi shukriya, Allah hafiz.",
    ), request="CMP", number="03001234567", short_number="0300123456", short_line=1),
    Scenario("privacy", "Earlier call, 'mera number pata hai?', another customer", "male", (
        "Main Bilal bol raha hoon. Pichli call mein maine kis gaari ke baare mein shikayat "
        "ki thi?",
        "Mera number 03211234567 hai. Kya aapko mera number pata hai?",
        "Suman ne jo complaint ki thi uska kya hua?",
        "Theek hai, Allah hafiz.",
    ), number="03211234567"),
    Scenario("sealion6", "SEALION 6 price", "female", (
        "SEALION 6 ki price kya hai?",
        "Aur SEALION 7 ki?",
        "Theek hai, shukriya. Allah hafiz.",
    )),
    Scenario("smoke", "Smoke and a burning smell", "male", (
        "Meri gaari se dhuan nikal raha hai aur jalne ki boo aa rahi hai. Main kya karun?",
        "Theek hai, main abhi rukta hoon. Shukriya, Allah hafiz.",
    )),
    Scenario("ending", "Ending: 'nahi shukriya Allah hafiz', then noise", "female", (
        "ATTO 3 ki price kya hai?",
        "Nahi shukriya, Allah hafiz.",
        "And that's things? Good night, sis.",
    )),
    Scenario("site_survey", "Home-charger site survey", "male", (
        "Main apne apartment ki parking mein home charger lagwana chahta hoon. Iska kya "
        "tareeqa hai?",
        "Ji, site survey ki request kar dein.",
        "Mera naam Hassan hai, number 03211234567, main Lahore Gulberg mein rehta hoon. Is "
        "hafte kisi bhi din subah.",
        "Ji, sahi hai.",
        "Shukriya, Allah hafiz.",
    ), request="REQ", number="03211234567"),
    Scenario("refund", "Cancellation refund amount", "female", (
        "Maine SEAL ki booking karwai thi aur paanch lakh advance diya tha. Ab cancel karni "
        "hai, kitna refund milega?",
        "Aur refund kitne din mein milta hai?",
        "Theek hai, shukriya. Allah hafiz.",
    )),
)


# ---------------------------------------------------------------------------
# What one run records
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Turn:
    who: str
    text: str
    heard: bool = True               # False: said after the goodbye and held back
    tools: list[tuple[str, dict, str]] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
    interrupted: bool = False
    references_before: int = 0       # references issued before this turn was spoken


@dataclasses.dataclass
class Run:
    scenario: Scenario
    index: int
    turns: list[Turn] = dataclasses.field(default_factory=list)
    logs: list[str] = dataclasses.field(default_factory=list)
    references: list[str] = dataclasses.field(default_factory=list)
    sent: list[str] = dataclasses.field(default_factory=list)
    calculated: list[str] = dataclasses.field(default_factory=list)
    ended: str = ""
    error: str = ""
    failures: list[str] = dataclasses.field(default_factory=list)

    def agent_turns(self, *, heard_only: bool = True) -> list[Turn]:
        return [t for t in self.turns if t.who == "agent" and (t.heard or not heard_only)]

    def caller_text(self) -> str:
        return " ".join(t.text for t in self.turns if t.who == "caller")


# ---------------------------------------------------------------------------
# The session the guards see
# ---------------------------------------------------------------------------

class SimSession:
    """What `live`'s tool handler and transcript watch read on a call,
    without audio: the transcript, when the caller spoke, a way to put a
    note in front of the model, and the hold after the goodbye."""

    def __init__(self, call_id: str, model: "Model") -> None:
        self.call_id = call_id
        self.model = model
        self.transcript: list[tuple[str, str]] = []
        self.on_transcript: Callable[[str, str], None] | None = None
        self.caller_spoke_at = 0.0
        self.caller_transcribed_at = 0.0
        self.caller_turn_done_at = 0.0
        self.speaking = False
        self.closed = False
        self.notes: list[str] = []
        self.holding = False
        self.held: list[str] = []
        self.frames_out = 1
        self.handler = type("H", (), {"value": "agent"})()

    def turns(self) -> list[tuple[str, str]]:
        return turns_mod.turns(self.transcript)

    def caller_spoke_within(self, seconds: float) -> bool:
        return bool(self.caller_spoke_at) and time.time() - self.caller_spoke_at <= seconds

    def caller_spoke_since(self, moment: float) -> bool:
        return self.caller_spoke_at > moment

    def caller_said_since(self, index: int) -> str:
        return " ".join(t for w, t in self.transcript[index:] if w == "caller" and t.strip())

    def _note(self, who: str, text: str) -> None:
        if who == "caller:done":
            self.caller_turn_done_at = time.time()
            if self.on_transcript:
                self.on_transcript(who, "")
            return
        if who == "agent":
            text, _leaked = script.strip_tool_calls(text)
            if self.holding:
                self.held.append(text)
                return
        self.transcript.append((who, text))
        if who == "caller":
            now = time.time()
            self.caller_spoke_at = now
            self.caller_transcribed_at = now
        if self.on_transcript:
            self.on_transcript(who, text)

    async def note_to_agent(self, text: str, *, urgent: bool = False) -> None:
        self.notes.append(text)
        await self.model.note(text, urgent=urgent)

    def hold_after_goodbye(self) -> None:
        self.holding = True

    def release_hold(self) -> None:
        self.holding = False
        for text in self.held:
            self.transcript.append(("agent", text))
        self.held = []

    async def end(self, _reason: str) -> None:
        self.closed = True

    def transcript_text(self) -> str:
        return "\n".join(f"{who}: {text}" for who, text in self.transcript)


# ---------------------------------------------------------------------------
# Nothing real: the stubs
# ---------------------------------------------------------------------------

class DryRun:
    """References without rows, Sent without sending, no call row touched."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.records: list[RecordedRequest] = []
        self.sent: list[tuple[str, str]] = []
        self.ended: list[str] = []
        self._saved: dict[str, Any] = {}

    async def create(self, tenant: Tenant, call: Call | None, kind: RequestType,
                     values: dict[str, Any]) -> RecordedRequest:
        day = time.strftime("%Y%m%d")
        prefix = PREFIXES[kind]
        n = self.counts.get(prefix, 0) + 1
        self.counts[prefix] = n
        fields = {k: v for k, v in values.items() if k in RecordedRequest.model_fields}
        record = RecordedRequest(
            tenant_id=tenant.phone_number_id, call_id=call.id if call else "",
            reference=f"{prefix}-{day}-{n:02d}", prefix=prefix, reference_day=day,
            type=kind, caller_number=call.counterparty if call else "", **fields)
        self.records.append(record)
        return record

    async def deliver(self, _sender: Tenant, destination: str, text: str, _call: Call) -> str:
        self.sent.append((destination, text))
        return "a dry run"

    def install(self) -> None:
        from app.repositories import requests as request_repo
        from app.services import lines

        self._saved = {
            "create": request_repo.create, "deliver": live._deliver,
            "sender": lines.whatsapp_sender, "can": live._can_send_whatsapp,
            "note": live._note_on_call, "hang_up": live.hang_up,
        }
        request_repo.create = self.create                       # type: ignore[assignment]
        live._deliver = self.deliver                             # type: ignore[assignment]

        async def sender(tenant: Tenant) -> Tenant:
            return tenant

        async def can_send(_tenant: Tenant) -> bool:
            return True

        async def note_on_call(_tenant: Tenant, call: Call, line: str) -> None:
            call.summary = f"{call.summary}\n{line}" if call.summary else line

        async def hang_up(_tenant: Tenant, call: Call, reason: str, **_kw: Any) -> None:
            self.ended.append(reason)
            session = live._sessions.get(call.id)
            if session is not None:
                session.closed = True

        lines.whatsapp_sender = sender                           # type: ignore[assignment]
        live._can_send_whatsapp = can_send                       # type: ignore[assignment]
        live._note_on_call = note_on_call                        # type: ignore[assignment]
        live.hang_up = hang_up                                   # type: ignore[assignment]

    def remove(self) -> None:
        from app.repositories import requests as request_repo
        from app.services import lines

        if not self._saved:
            return
        request_repo.create = self._saved["create"]
        live._deliver = self._saved["deliver"]
        lines.whatsapp_sender = self._saved["sender"]
        live._can_send_whatsapp = self._saved["can"]
        live._note_on_call = self._saved["note"]
        live.hang_up = self._saved["hang_up"]
        self._saved = {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Exchange:
    texts: list[str]
    tools: list[tuple[str, dict, str]]
    interrupted: bool = False


class Model:
    """One side of the conversation. `send_user` gives it the caller's words,
    `note` a silent instruction, `exchange` reads its answer, answering tool
    calls through `on_tool` as it goes."""

    on_tool: Callable[[str, dict], Awaitable[str]] | None = None

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_user(self, text: str) -> None: ...
    async def note(self, text: str, *, urgent: bool = False) -> None: ...
    async def exchange(self) -> Exchange: ...


class LiveTextModel(Model):
    """The Live API, answering in text: the production model, minus the voice."""

    def __init__(self, settings: LiveSettings, persona: AgentPersona) -> None:
        self.settings = settings
        self.persona = persona
        self._cm: Any = None
        self._session: Any = None
        self._expect_more = 0

    async def start(self) -> None:
        from google import genai

        config: dict[str, Any] = {
            "response_modalities": ["TEXT"],
            "system_instruction": self.persona.instructions,
        }
        if self.persona.tools:
            config["tools"] = [{"function_declarations": self.persona.tools}]
        if self.settings.temperature is not None:
            config["temperature"] = float(self.settings.temperature)
        client = genai.Client(api_key=self.settings.api_key)
        self._cm = client.aio.live.connect(model=self.settings.model, config=config)
        self._session = await self._cm.__aenter__()

    async def stop(self) -> None:
        if self._cm is not None:
            with contextlib.suppress(Exception):
                await self._cm.__aexit__(None, None, None)
        self._cm = self._session = None

    async def send_user(self, text: str) -> None:
        from google.genai import types

        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=True)

    async def note(self, text: str, *, urgent: bool = False) -> None:
        from google.genai import types

        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]), turn_complete=urgent)
        if urgent:
            self._expect_more += 1

    async def exchange(self) -> Exchange:
        from google.genai import types

        texts: list[str] = []
        tools: list[tuple[str, dict, str]] = []
        interrupted = False

        async def one_pass() -> bool:
            """Read until the turn completes. True if tools were answered."""
            nonlocal interrupted
            answered = False
            async for message in self._session.receive():
                content = getattr(message, "server_content", None)
                if content is not None:
                    if getattr(content, "interrupted", False):
                        interrupted = True
                    turn = getattr(content, "model_turn", None)
                    for part in getattr(turn, "parts", None) or []:
                        if getattr(part, "text", None):
                            texts.append(part.text)
                tool_call = getattr(message, "tool_call", None)
                calls = getattr(tool_call, "function_calls", None) if tool_call else None
                if calls:
                    responses = []
                    for call in calls:
                        name = getattr(call, "name", "")
                        args = dict(getattr(call, "args", None) or {})
                        result = await self.on_tool(name, args) if self.on_tool else "not available"
                        tools.append((name, args, result))
                        responses.append(types.FunctionResponse(
                            id=getattr(call, "id", None), name=name, response={"result": result}))
                    await self._session.send_tool_response(function_responses=responses)
                    answered = True
            return answered

        passes = 0
        while passes < 6:
            passes += 1
            before = len(texts)
            try:
                answered = await asyncio.wait_for(one_pass(), timeout=90)
            except asyncio.TimeoutError:
                texts.append("[no answer within 90 seconds]")
                break
            await asyncio.sleep(0.2)             # let the guards' notes go out
            if answered or self._expect_more:
                self._expect_more = 0
                continue
            if len(texts) == before and passes < 2:
                continue                         # the tool call may follow the turn
            break
        return Exchange(texts, tools, interrupted)


class TextModel(Model):
    """`generate_content` with the same instructions and tools: the fallback
    when the Live API will not answer in text."""

    def __init__(self, settings: LiveSettings, persona: AgentPersona, model: str) -> None:
        self.settings = settings
        self.persona = persona
        self.model = model
        self._client: Any = None
        self._history: list[Any] = []
        self._pending_user: list[str] = []
        self._urgent = False

    async def start(self) -> None:
        from google import genai

        self._client = genai.Client(api_key=self.settings.api_key)

    async def send_user(self, text: str) -> None:
        self._pending_user.append(text)

    async def note(self, text: str, *, urgent: bool = False) -> None:
        self._pending_user.append(text)
        self._urgent = self._urgent or urgent

    async def exchange(self) -> Exchange:
        from google.genai import types

        texts: list[str] = []
        tools: list[tuple[str, dict, str]] = []
        config = types.GenerateContentConfig(
            system_instruction=self.persona.instructions,
            tools=[types.Tool(function_declarations=self.persona.tools)] if self.persona.tools else None,
        )
        rounds = 0
        while rounds < 8:
            rounds += 1
            if self._pending_user:
                self._history.append(types.Content(
                    role="user", parts=[types.Part(text=t) for t in self._pending_user]))
                self._pending_user = []
                self._urgent = False
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=self._history, config=config)
            candidate = (response.candidates or [None])[0]
            content = getattr(candidate, "content", None)
            if content is None:
                break
            self._history.append(content)
            calls = [p.function_call for p in content.parts or [] if getattr(p, "function_call", None)]
            for part in content.parts or []:
                if getattr(part, "text", None):
                    texts.append(part.text)
            if not calls:
                if self._pending_user and self._urgent:
                    continue                      # a note arrived: answer it too
                break
            parts = []
            for call in calls:
                args = dict(call.args or {})
                result = await self.on_tool(call.name, args) if self.on_tool else "not available"
                tools.append((call.name, args, result))
                parts.append(types.Part.from_function_response(
                    name=call.name, response={"result": result}))
            self._history.append(types.Content(role="user", parts=parts))
        return Exchange(texts, tools)


# ---------------------------------------------------------------------------
# One conversation
# ---------------------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self, call_id: str, into: list[str]) -> None:
        super().__init__(level=logging.INFO)
        self.call_id = call_id
        self.into = into

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if self.call_id in message or "call " not in message:
            self.into.append(f"{record.levelname.lower()}: {message.replace(self.call_id, 'call')}")


async def converse(scenario: Scenario, index: int, tenant: Tenant, persona: AgentPersona,
                   model: Model, dry: DryRun, *, say: Callable[[str], None] | None = None) -> Run:
    """Run one scenario against `model` with the real tools and guards."""
    run = Run(scenario, index)
    call = Call(tenantId=tenant.phone_number_id, channel=Channel.PHONE,
                direction=Direction.INBOUND, counterparty="+923001234567")
    session = SimSession(call.id, model)
    session.on_transcript = live._transcript_watch(tenant, call)
    live._sessions[call.id] = session
    model.on_tool = live._tool_handler(tenant, call)
    capture = _Capture(call.id, run.logs)
    logging.getLogger("app.services.agent").addHandler(capture)
    sent_before = len(dry.sent)
    ended_before = len(dry.ended)
    refs_before = len(dry.records)

    def tell(line: str) -> None:
        if say:
            say(line)

    try:
        await model.start()
        if persona.greeting:
            await model.send_user(
                f"Greet the caller with exactly this, and nothing else: {persona.greeting}")
            opening = await model.exchange()
            _record_agent(run, session, opening, dry, refs_before, tell)
        for line in scenario.lines:
            run.turns.append(Turn("caller", line))
            tell(f"{YELLOW}caller{OFF}  {line}")
            notes_before = len(session.notes)
            session._note("caller", line)
            session._note("caller:done", "")
            await asyncio.sleep(0.05)            # the watch's notes go first
            await model.send_user(line)
            await asyncio.sleep(0.05)
            exchange = await model.exchange()
            turn = _record_agent(run, session, exchange, dry, refs_before, tell)
            turn.notes = session.notes[notes_before:]
            for note in turn.notes:
                tell(f"   {DIM}[note] {note}{OFF}")
            await asyncio.sleep(0.4)
        # The goodbye's grace period and the server-side end run after the
        # last line; give them the time they take on a call.
        await asyncio.sleep(live.GOODBYE_GRACE_SECONDS + 1.0)
    except Exception as exc:  # noqa: BLE001
        run.error = f"{type(exc).__name__}: {exc}"
        tell(f"{RED}error{OFF}  {run.error}")
    finally:
        with contextlib.suppress(Exception):
            await model.stop()
        logging.getLogger("app.services.agent").removeHandler(capture)
        live._sessions.pop(call.id, None)
        live._pending.pop(call.id, None)
        live._recorded.pop(call.id, None)
    run.references = [r.reference for r in dry.records[refs_before:]]
    run.sent = [d for d, _ in dry.sent[sent_before:]]
    run.ended = dry.ended[ended_before] if len(dry.ended) > ended_before else ""
    for name, _args, result in (t for turn in run.turns for t in turn.tools):
        if name == "calculate":
            run.calculated.append(result)
    for line in run.logs:
        tell(f"   {DIM}[guard] {line}{OFF}")
    return run


def _record_agent(run: Run, session: SimSession, exchange: Exchange, dry: DryRun,
                  refs_before: int, tell: Callable[[str], None]) -> Turn:
    text = " ".join(t.strip() for t in exchange.texts if t.strip())
    turn = Turn("agent", text, heard=not session.holding, tools=list(exchange.tools),
                interrupted=exchange.interrupted, references_before=len(dry.records) - refs_before)
    if text:
        session._note("agent", text)
    run.turns.append(turn)
    tag = "agent" if turn.heard else "agent (not heard)"
    tell(f"{GREEN}{tag}{OFF}  {text}" + (f" {DIM}[interrupted]{OFF}" if turn.interrupted else ""))
    for name, args, result in exchange.tools:
        shown = {k: (v if k != "mobile" else phones.ending(str(v)) and f"…{phones.ending(str(v))}")
                 for k, v in args.items()}
        tell(f"   {DIM}[tool] {name} {shown} -> {result[:160]}{OFF}")
    return turn


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

_MALE_ADDRESS = turns_mod._phrases(
    "chahte hain", "sakte hain", "karte hain", "rakhte hain", "batate hain", "chahenge",
    "batayenge", "karenge", "kahenge", "sahib", "sahab", "bhai sahab",
)
_FEMALE_ADDRESS = turns_mod._phrases(
    "chahti hain", "sakti hain", "karti hain", "rakhti hain", "batati hain", "chahengi",
    "batayengi", "karengi", "kahengi", "sahiba",
)
_SPOKEN_AMOUNT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(crore|lakh|hazar)\b", re.I)
#: A rupee amount said as digits: with thousands commas and no unit after it
#: ("8,990,000"), or digits followed by a currency word ("8990000 rupay").
#: A registration number, a mileage in kilometres or a reference is neither.
_DIGIT_AMOUNT = re.compile(
    r"\b\d{1,3}(?:,\d{3})+\b(?!\s*(?:km|kilomet|kms|kw|cc|kg|mile|rpm|hazar|lakh|crore)\w*)"
    r"|\b[1-9]\d{4,8}\b(?=\s*(?:rupay|rupees?|pkr|rs)\b)", re.I)
_TOOL_WORDS = re.compile(r"\b(?:" + "|".join(script.TOOL_NAMES) + r")\b|[{}]")
_TRIMS = ("advance", "premium", "dynamic", "extended", "plus", "comfort", "performance")
_SHORT_SAID = turns_mod._phrases("11", "gyarah", "gyara", "digits", "digit", "dobara", "phir se",
                                 "10", "das", "12", "barah", "complete nahi", "mukammal")


def spoken_amounts(text: str) -> list[str]:
    """Each rupee amount said in words: "1 crore 47 lakh 90 hazar"."""
    found: list[str] = []
    current: list[str] = []
    last_end = -1
    for match in _SPOKEN_AMOUNT.finditer(text or ""):
        between = text[last_end:match.start()] if last_end >= 0 else ""
        if last_end >= 0 and between.strip() in ("", "aur", ","):
            current.append(f"{match.group(1)} {match.group(2).lower()}")
        else:
            if current:
                found.append(" ".join(current))
            current = [f"{match.group(1)} {match.group(2).lower()}"]
        last_end = match.end()
    if current:
        found.append(" ".join(current))
    return found


def check(run: Run, material: str) -> list[str]:
    """Every rule a run must keep, as a list of what it broke."""
    failures: list[str] = []
    scenario = run.scenario
    if run.error:
        failures.append(f"the run failed: {run.error}")
    heard = run.agent_turns()
    spoken = heard[1:] if len(heard) > 1 and run.turns and run.turns[0].who == "agent" else heard

    # One question per turn.
    for turn in spoken:
        questions = turn.text.count("?") + turn.text.count("؟")
        if questions > 1:
            failures.append(f"two questions in one turn: {turn.text[:90]!r}")

    # The caller's gender.
    wrong = _MALE_ADDRESS if scenario.gender == "female" else _FEMALE_ADDRESS
    for turn in spoken:
        if turns_mod._has_any(turn.text, wrong):
            failures.append(f"wrong gender form for a {scenario.gender} caller: {turn.text[:90]!r}")
            break

    # Only the material's facts, in words.
    allowed = " ".join(material.split()).lower()
    computed = " ".join(amounts.spoken_pkr(float(v)) for v in run.calculated
                        if re.fullmatch(r"-?\d+(?:\.\d+)?", v.strip()))
    for turn in spoken:
        for amount in spoken_amounts(turn.text):
            if amount.lower() not in allowed and amount.lower() not in computed:
                failures.append(f"an amount not in the material: {amount!r}")
        for digits in _DIGIT_AMOUNT.findall(turn.text):
            if phones.find_number(digits) is None and not digits.startswith("0"):
                failures.append(f"an amount said in digits, not words: {digits!r}")

    # No false "recorded" or "sent" claims.
    for turn in spoken:
        if turn.references_before == 0 and (live._claims_recorded(turn.text)
                                            or live._claims_forwarded(turn.text)):
            failures.append(f"claimed recorded or sent with no reference: {turn.text[:90]!r}")

    # A real reference, said to the caller.
    if scenario.request:
        if not run.references:
            failures.append("no request was recorded")
        else:
            said = "".join(t.text for t in heard).replace(" ", "").replace("-", "").upper()
            if not any(r.replace("-", "") in said for r in run.references):
                failures.append(f"the reference was not read out: {run.references}")
            if not any(r.startswith(scenario.request) for r in run.references):
                failures.append(f"the wrong kind of request: {run.references}")
    if scenario.whatsapp and not run.sent:
        failures.append("no WhatsApp message was sent")

    # One read-back, in the caller's words.
    if scenario.number:
        full = [t for t in spoken if phones.read_back_in(t.text, scenario.number)
                and ("naam" in t.text.lower() or "name" in t.text.lower()
                     or re.search(r"\b(atto|seal|sealion|shark)\b", t.text, re.I))]
        if scenario.request and len(full) > 1:
            failures.append(f"the details were read back {len(full)} times")
        caller = run.caller_text().lower()
        for turn in full:
            for trim in _TRIMS:
                if re.search(rf"\b{trim}\b", turn.text, re.I) and trim not in caller:
                    failures.append(f"the read-back added {trim!r}, which the caller never said")

    # The digit count before the read-back.
    if scenario.short_number and 0 <= scenario.short_line < len(scenario.lines):
        after = _agent_after_caller_line(run, scenario.short_line)
        if after is None:
            failures.append("no answer to the short number")
        elif not turns_mod._has_any(after.text, _SHORT_SAID):
            failures.append(f"the short number was not challenged: {after.text[:90]!r}")
        elif (phones.read_back_in(after.text, scenario.short_number) and "?" in after.text
              and not turns_mod._has_any(after.text, _SHORT_SAID)):
            failures.append("the short number was read back as complete")

    # No tool names in speech.
    for turn in spoken:
        if _TOOL_WORDS.search(turn.text):
            failures.append(f"a tool name or bracket in speech: {turn.text[:90]!r}")

    # The closing line once, and the call ended.
    closings = sum(1 for t in heard if turns_mod.agent_said_closing(t.text))
    goodbyes = sum(turns_mod.normalise(t.text).count("hafiz") for t in heard)
    if closings != 1 or goodbyes > 1:
        failures.append(f"the closing line was said {max(closings, goodbyes)} times")
    if not run.ended:
        failures.append("the call did not end")
    run.failures = failures
    return failures


def _agent_after_caller_line(run: Run, line_index: int) -> Turn | None:
    seen = -1
    for position, turn in enumerate(run.turns):
        if turn.who == "caller":
            seen += 1
            if seen == line_index:
                for later in run.turns[position + 1:]:
                    if later.who == "agent":
                        return later
                return None
    return None


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

def _say(line: str = "") -> None:
    print(line, flush=True)


async def _load(tenant_id: str, agent_id: str) -> tuple[Tenant, Call, AgentPersona, LiveSettings]:
    from app.repositories import tenants as tenant_repo

    tenant = await tenant_repo.get(tenant_id)
    if tenant is None:
        raise SystemExit(f"no company with id {tenant_id}")
    call = Call(tenantId=tenant.phone_number_id, channel=Channel.PHONE,
                direction=Direction.INBOUND, counterparty="+923001234567",
                agentId=agent_id or None)
    persona = await live.persona_for(tenant, call)
    settings = await live.live_settings_now(tenant)
    if not settings.api_key:
        raise SystemExit("no Gemini key: set GEMINI_API_KEY in backend/.env")
    return tenant, call, persona, settings


def _table(runs: list[Run], runs_per: int) -> str:
    width = max(len(s.title) for s in SCENARIOS) + 2
    header = "scenario".ljust(width) + "".join(f"run {i + 1}".ljust(8) for i in range(runs_per))
    lines = [header, "-" * len(header)]
    for scenario in SCENARIOS:
        row = [r for r in runs if r.scenario.key == scenario.key]
        if not row:
            continue
        cells = "".join(("PASS" if not r.failures else "FAIL").ljust(8) for r in row)
        lines.append(scenario.title.ljust(width) + cells)
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    tenant, _call, persona, settings = await _load(args.tenant, args.agent)
    chosen = [s for s in SCENARIOS if not args.only or s.key in args.only
              or str(SCENARIOS.index(s) + 1) in args.only]
    dry = DryRun()
    dry.install()
    runs: list[Run] = []
    try:
        for scenario in chosen:
            for index in range(args.runs):
                _say(f"\n=== {scenario.title}  (run {index + 1} of {args.runs}, "
                     f"{scenario.gender} caller) ===")
                if args.engine == "text":
                    model: Model = TextModel(settings, persona, args.text_model)
                else:
                    model = LiveTextModel(settings, persona)
                run = await converse(scenario, index, tenant, persona, model, dry, say=_say)
                failures = check(run, persona.instructions)
                if failures:
                    _say(f"{RED}FAIL{OFF}")
                    for failure in failures:
                        _say(f"   - {failure}")
                else:
                    _say(f"{GREEN}PASS{OFF}")
                runs.append(run)
    finally:
        dry.remove()
    _say("\n" + _table(runs, args.runs))
    failed = [r for r in runs if r.failures]
    _say(f"\n{len(runs) - len(failed)} of {len(runs)} runs passed.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", default="", help="the company id (phone number id)")
    parser.add_argument("--agent", default="", help="one agent, instead of the number's default")
    parser.add_argument("--runs", type=int, default=3, help="how many times to run each scenario")
    parser.add_argument("--only", default="", help="scenario keys or numbers, comma separated")
    parser.add_argument("--engine", choices=("live", "text"), default="live")
    parser.add_argument("--text-model", default="gemini-2.5-flash",
                        help="the model for --engine text")
    parser.add_argument("--list", action="store_true", help="list the scenarios and stop")
    args = parser.parse_args()
    if args.list:
        for number, scenario in enumerate(SCENARIOS, 1):
            _say(f"{number:2d}. {scenario.key:12s} {scenario.title} ({scenario.gender})")
        return 0
    if not args.tenant:
        parser.error("--tenant is required")
    args.only = [s.strip() for s in args.only.split(",") if s.strip()]
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
