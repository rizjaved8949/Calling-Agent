"""
A call does not wait for things that could have been loaded before it rang.

From a real call, the first after a deploy: nine seconds passed between Twilio
connecting the audio and the agent being ready, all of it silence for the
caller. Now every step of getting an agent ready is timed in the log, each
company's documents stay loaded between calls (re-read in the background,
never while a caller waits), and the server loads them when it starts.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.repositories import knowledge


@pytest.fixture
def reads(monkeypatch):
    """Counts the slow read behind the knowledge text, and slows it down."""
    seen: list[tuple[str, str]] = []
    version = {"n": 1}

    async def read(tenant_id, _limit=0, knowledge_base_id=""):
        seen.append((tenant_id, knowledge_base_id))
        await asyncio.sleep(0.05)
        return f"documents v{version['n']}"

    monkeypatch.setattr(knowledge, "_read_context", read)
    knowledge.forget_context()
    knowledge._refreshing.clear()
    yield seen, version
    knowledge.forget_context()


# ---------------------------------------------------------------------------
# Documents stay loaded
# ---------------------------------------------------------------------------

async def test_the_first_call_reads_and_the_next_does_not(reads):
    seen, _ = reads
    assert await knowledge.context_for("900") == "documents v1"
    assert await knowledge.context_for("900") == "documents v1"
    assert len(seen) == 1


async def test_an_old_copy_is_used_at_once_and_refreshed_behind_the_call(reads, monkeypatch):
    seen, version = reads
    await knowledge.context_for("900")
    monkeypatch.setattr(knowledge, "_CONTEXT_TTL_SECONDS", 0.0)
    version["n"] = 2

    started = time.monotonic()
    text = await knowledge.context_for("900")
    assert time.monotonic() - started < 0.02, "the call waited for the re-read"
    assert text == "documents v1"

    await asyncio.sleep(0.1)                        # the refresh lands
    monkeypatch.setattr(knowledge, "_CONTEXT_TTL_SECONDS", 120.0)
    assert await knowledge.context_for("900") == "documents v2"
    assert len(seen) == 2


async def test_only_one_refresh_runs_at_a_time(reads, monkeypatch):
    seen, _ = reads
    await knowledge.context_for("900")
    monkeypatch.setattr(knowledge, "_CONTEXT_TTL_SECONDS", 0.0)
    for _ in range(5):
        await knowledge.context_for("900")
    await asyncio.sleep(0.1)
    assert len(seen) == 2


async def test_an_upload_is_never_hidden_by_an_old_copy(reads, monkeypatch):
    """Dropped on upload, read fresh by the next call; and a refresh that was
    already running does not put the old text back."""
    seen, version = reads
    await knowledge.context_for("900")
    monkeypatch.setattr(knowledge, "_CONTEXT_TTL_SECONDS", 0.0)
    await knowledge.context_for("900")              # a refresh starts...
    knowledge.forget_context("900")                 # ...an upload lands
    version["n"] = 3
    await asyncio.sleep(0.1)
    assert ("900", "", knowledge.MAX_CONTEXT_CHARS) not in knowledge._context_cache
    assert await knowledge.context_for("900") == "documents v3"


async def test_a_failed_refresh_keeps_the_copy_in_hand(reads, monkeypatch):
    await knowledge.context_for("900")
    monkeypatch.setattr(knowledge, "_CONTEXT_TTL_SECONDS", 0.0)

    async def broken(*_a, **_k):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(knowledge, "_read_context", broken)
    assert await knowledge.context_for("900") == "documents v1"
    await asyncio.sleep(0.05)
    assert await knowledge.context_for("900") == "documents v1"


async def test_warming_loads_every_knowledge_base(reads):
    seen, _ = reads
    await knowledge.warm("900", ["kb-1", "kb-2"])
    assert sorted(seen) == [("900", ""), ("900", "kb-1"), ("900", "kb-2")]


# ---------------------------------------------------------------------------
# Loaded when the server starts
# ---------------------------------------------------------------------------

async def test_prepare_loads_every_companys_documents(fake_db, reads, monkeypatch):
    from app.models.tenant import Tenant
    from app.repositories import agents as agent_repo
    from app.repositories import tenants as tenant_repo
    from app.services.agent import live

    seen, _ = reads
    await tenant_repo.save(Tenant(phoneNumberId="900", name="Acme"))
    await tenant_repo.save(Tenant(phoneNumberId="901", name="Beta"))

    async def bases(tenant_id):
        return [type("KB", (), {"id": f"kb-{tenant_id}"})()]

    monkeypatch.setattr(agent_repo, "list_knowledge_bases", bases)
    await live.prepare()
    assert sorted(seen) == [("900", ""), ("900", "kb-900"), ("901", ""), ("901", "kb-901")]

    # And the call that follows reads nothing.
    seen.clear()
    await knowledge.context_for("900", knowledge_base_id="kb-900")
    assert seen == []


async def test_prepare_never_fails_the_server(fake_db, monkeypatch):
    from app.repositories import tenants as tenant_repo
    from app.services.agent import live

    async def broken(*_a, **_k):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(tenant_repo, "list_all", broken)
    await live.prepare()                            # no exception


def test_the_server_prepares_when_it_starts(fake_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.services.agent import live

    ran: list[bool] = []

    async def prepare():
        ran.append(True)

    monkeypatch.setattr(live, "prepare", prepare)
    monkeypatch.setattr(settings, "prepare_on_start", True)
    with TestClient(app):
        pass
    assert ran == [True]


def test_tests_do_not_prepare_behind_themselves():
    from app.config import settings

    assert settings.prepare_on_start is False


# ---------------------------------------------------------------------------
# Every step timed
# ---------------------------------------------------------------------------

async def test_getting_an_agent_ready_is_timed_step_by_step(fake_db, monkeypatch, caplog):
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.models.tenant import Tenant
    from app.services.agent import live
    from app.services.agent.gemini import LiveSettings

    async def settings_now(_tenant=None):
        return LiveSettings(model="m", api_key="k")

    monkeypatch.setattr(live, "live_settings_now", settings_now)
    tenant = Tenant(phoneNumberId="900", name="Acme")
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.RINGING, counterparty="+923001112222")

    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        await live._prepared(tenant, call)
    [line] = [r.getMessage() for r in caplog.records if "agent prepared in" in r.getMessage()]
    for step in ("engine settings", "routing", "documents", "WhatsApp check"):
        assert step in line, f"{step} is not timed"


async def test_a_slow_start_is_a_warning(fake_db, monkeypatch, caplog):
    from app.models.call import Call, CallStatus, Channel, Direction
    from app.models.tenant import Tenant
    from app.services.agent import live
    from app.services.agent.gemini import LiveSettings

    async def settings_now(_tenant=None):
        return LiveSettings(model="m", api_key="k")

    monkeypatch.setattr(live, "live_settings_now", settings_now)
    monkeypatch.setattr(live, "SLOW_PREPARE_SECONDS", 0.0)
    tenant = Tenant(phoneNumberId="900", name="Acme")
    call = Call(tenantId="900", channel=Channel.PHONE, direction=Direction.INBOUND,
                status=CallStatus.RINGING, counterparty="+923001112222")
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        await live._prepared(tenant, call)
    assert any(r.levelno == logging.WARNING and "agent prepared in" in r.getMessage()
               for r in caplog.records)
