"""
The document cache has a memory limit.

Documents now stay loaded between calls and are preloaded at startup, so
without a limit the cache would grow with every company that uploads, until
the server runs out of memory. Past `document_cache_mb` the least recently
used companies are dropped (and read again on their next call), and the
startup preload stops.
"""
from __future__ import annotations

import logging

import pytest

from app.config import settings
from app.repositories import knowledge

#: A text that costs a little over 0.1 MB in memory.
TEXT = "x" * 100_000


@pytest.fixture
def cache(monkeypatch):
    """A 0.25 MB cache (two texts fit, a third does not), and a counted read."""
    seen: list[str] = []

    async def read(tenant_id, _limit=0, knowledge_base_id=""):
        seen.append(tenant_id)
        return TEXT

    monkeypatch.setattr(knowledge, "_read_context", read)
    monkeypatch.setattr(knowledge, "_cache_limit", lambda: 250_000)
    knowledge.forget_context()
    yield seen
    knowledge.forget_context()


async def test_the_cache_never_grows_past_its_limit(cache):
    for company in ("a", "b", "c", "d"):
        await knowledge.context_for(company)
    assert knowledge.cached_bytes() <= 250_000
    assert len(knowledge._context_cache) == 2


async def test_the_least_recently_used_company_is_dropped_first(cache):
    seen = cache
    await knowledge.context_for("a")
    await knowledge.context_for("b")
    await knowledge.context_for("a")                # "a" is used again
    await knowledge.context_for("c")                # so "b" makes room
    seen.clear()
    await knowledge.context_for("a")
    assert seen == [], "the company in use was dropped"
    await knowledge.context_for("b")
    assert seen == ["b"], "a dropped company is read again on its next call"


async def test_the_size_counts_memory_not_characters(cache):
    """Urdu text is stored at up to four bytes a character."""
    await knowledge.context_for("a")
    assert knowledge.cached_bytes() >= len(TEXT)


async def test_forgetting_gives_the_memory_back(cache):
    await knowledge.context_for("a")
    await knowledge.context_for("b")
    knowledge.forget_context("a")
    assert knowledge.cached_bytes() == knowledge._size(TEXT)
    knowledge.forget_context()
    assert knowledge.cached_bytes() == 0


async def test_a_text_bigger_than_the_whole_cache_is_served_but_not_kept(cache, monkeypatch):
    monkeypatch.setattr(knowledge, "_cache_limit", lambda: 1_000)
    assert await knowledge.context_for("a") == TEXT
    assert knowledge.cached_bytes() == 0


async def test_preloading_stops_once_the_cache_is_full(fake_db, cache, monkeypatch, caplog):
    from app.models.tenant import Tenant
    from app.repositories import agents as agent_repo
    from app.repositories import tenants as tenant_repo
    from app.services.agent import live

    seen = cache
    for company in ("901", "902", "903", "904"):
        await tenant_repo.save(Tenant(phoneNumberId=company, name=company))

    async def no_bases(_tenant_id):
        return []

    monkeypatch.setattr(agent_repo, "list_knowledge_bases", no_bases)
    with caplog.at_level(logging.INFO, logger="app.services.agent.live"):
        await live.prepare()
    assert len(seen) < 4, "preloading carried on past a full cache"
    assert knowledge.cached_bytes() <= 250_000
    lines = [r.getMessage() for r in caplog.records]
    assert any("document cache is full" in line for line in lines)
    [ready] = [line for line in lines if line.startswith("prepare: ready")]
    assert "MB of" in ready and "document cache used" in ready


def test_the_limit_is_a_setting_of_about_100_mb():
    from app.config import Settings

    assert Settings.model_fields["document_cache_mb"].default == 100
    assert knowledge._cache_limit() == settings.document_cache_mb * 1024 * 1024
