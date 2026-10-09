"""
The silence a caller sits through before the agent speaks.

Measured against the live model: reading the company's material took 1.7s,
opening the model session 3.8s, and the first word 0.7s — six seconds, all of
it after the carrier had already connected the caller. Long enough that people
say "hello?" first, which starts the call on the wrong foot.

None of that work needs the caller. Started when the leg is dialled instead, it
runs alongside the carrier's own setup: measured again, 5.7s became 0.0s.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.agent.pacer import FramePacer
from app.services.agent.audio_rate import FRAME_BYTES


def test_an_unticked_pacer_holds_its_audio():
    """This is what makes warming up safe: a greeting generated before the
    line opens waits in the queue rather than playing to nobody."""
    sent: list[bytes] = []
    pacer = FramePacer(sent.append, keepalive=False)
    pacer.offer(b"\x01\x02" * (FRAME_BYTES // 2) * 3)

    assert pacer.queued == 3
    assert sent == [], "audio went out before the line was open"


def test_attaching_a_transport_releases_what_was_queued():
    clock = {"now": 0.0}
    sent: list[bytes] = []
    pacer = FramePacer(lambda f: None, clock=lambda: clock["now"], keepalive=False)
    pacer.offer(b"\x01\x02" * (FRAME_BYTES // 2) * 3)

    # The line opens: the sink is swapped and the clock starts.
    pacer.send = sent.append
    for _ in range(40):
        clock["now"] += 0.02
        pacer.tick()

    assert sent, "the queued greeting never played"
    assert sum(len(f) for f in sent) >= FRAME_BYTES * 3


@pytest.mark.asyncio
async def test_start_hands_the_line_to_a_session_already_warmed(fake_db, monkeypatch):
    """`start` must not build a second agent over the top of the warm one."""
    from app.models.call import Call, CallStatus, Channel
    from app.models.tenant import Tenant
    from app.repositories import calls as call_repo
    from app.services.agent import live

    tenant = Tenant(phoneNumberId="t-1", name="Acme")
    call = Call(id="warm-1", tenantId="t-1", channel=Channel.PHONE, counterparty="+92300")
    await call_repo.save_call(call)

    attached: list[object] = []

    class Warmed:
        handler = type("H", (), {"value": "agent"})()
        def attach_transport(self, send):
            attached.append(send)

    live._sessions["warm-1"] = Warmed()
    built = []
    monkeypatch.setattr(live, "persona_for", lambda *a, **kw: built.append(1))

    def transport(_frame: bytes) -> None: ...
    session = await live.start(tenant, call, transport)

    assert session is live._sessions["warm-1"]
    assert attached == [transport], "the warmed session was not given the line"
    assert built == [], "a second agent was built over the warm one"

    stored = await call_repo.get_call("t-1", "warm-1")
    assert stored.status is CallStatus.IN_PROGRESS
    assert stored.answered_at is not None
    live._sessions.pop("warm-1", None)


@pytest.mark.asyncio
async def test_a_failed_warm_up_leaves_nothing_behind(fake_db, monkeypatch):
    """Warming is an optimisation. If it fails, `start` must still be able to
    do the work the old way rather than find a broken session in its place."""
    from app.models.call import Call, Channel
    from app.models.tenant import Tenant
    from app.services.agent import live

    async def explode(*a, **kw):
        raise RuntimeError("the model is having a moment")

    monkeypatch.setattr(live, "persona_for", explode)
    call = Call(id="warm-2", tenantId="t-1", channel=Channel.PHONE, counterparty="+92300")
    await live.warm_up(Tenant(phoneNumberId="t-1", name="Acme"), call)

    assert "warm-2" not in live._sessions


def test_the_prompt_text_is_cached_between_calls(fake_db, monkeypatch):
    """1.7 seconds of the old delay was re-reading tens of thousands of
    characters that had not changed since the previous call.

    Counted rather than timed: a stopwatch assertion fails on a busy machine
    for reasons that have nothing to do with the cache.
    """
    from app.repositories import knowledge as knowledge_repo

    asyncio.run(knowledge_repo.save("t-9", "Fees", "BS CS costs 28,000."))

    reads = []
    original = knowledge_repo._read_context

    async def counted(*args, **kwargs):
        reads.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(knowledge_repo, "_read_context", counted)

    first = asyncio.run(knowledge_repo.context_for("t-9"))
    assert "28,000" in first
    assert len(reads) == 1

    assert asyncio.run(knowledge_repo.context_for("t-9")) == first
    assert len(reads) == 1, "the second call went back to the database"

    # A new document must not be stuck behind the cache.
    asyncio.run(knowledge_repo.save("t-9", "Timings", "Campus opens at 8."))
    assert "Campus opens at 8" in asyncio.run(knowledge_repo.context_for("t-9"))
    assert len(reads) == 2
