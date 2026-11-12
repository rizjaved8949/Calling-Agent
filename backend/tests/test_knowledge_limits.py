"""
The whole knowledge base reaches the agent, or the log says what did not.

A handbook keeps its conduct rules at the end, which is exactly the part a
character limit cuts. The limit is a setting now, a cut is a warning that
names the first heading lost, and the preview reports the real total. And a
document whose tables lost cells on upload is flagged, because that is how
"SEAL Premium: 321 kW" became a fact.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.config import settings
from app.repositories import knowledge

HANDBOOK = "# 1. Prices\n" + ("ATTO 3 PKR 8,990,000.\n" * 200) + "# 2. Conduct rules\nBe polite.\n"
SECOND = "# 3. Service plan\n" + ("Service every 10,000 km.\n" * 40)


# ---------------------------------------------------------------------------
# D1: the limit
# ---------------------------------------------------------------------------

def test_the_limit_is_a_setting_with_the_old_default():
    from app.config import Settings

    assert Settings.model_fields["knowledge_max_chars"].default == 120_000
    assert knowledge.max_chars() == settings.knowledge_max_chars


async def test_the_setting_is_honoured(fake_db, monkeypatch):
    await knowledge.save("t-1", "Handbook", HANDBOOK)
    await knowledge.save("t-1", "Service", SECOND)
    monkeypatch.setattr(settings, "knowledge_max_chars", 3_000)
    knowledge.forget_context()
    text = await knowledge.context_for("t-1")
    assert len(text) <= 3_000 + 20
    assert "[…truncated]" in text

    monkeypatch.setattr(settings, "knowledge_max_chars", 120_000)
    knowledge.forget_context()
    assert "[…truncated]" not in await knowledge.context_for("t-1")


async def test_a_cut_is_a_warning_naming_the_first_heading_lost(fake_db, monkeypatch, caplog):
    await knowledge.save("t-1", "Handbook", HANDBOOK)
    monkeypatch.setattr(settings, "knowledge_max_chars", 1_200)
    knowledge.forget_context()
    with caplog.at_level(logging.WARNING, logger="app.repositories.knowledge"):
        await knowledge.context_for("t-1")
    [warning] = [r for r in caplog.records if "KNOWLEDGE_MAX_CHARS" in r.getMessage()]
    message = warning.getMessage()
    assert "t-1" in message and str(len(HANDBOOK)) in message
    assert "'# 2. Conduct rules'" in message


async def test_a_whole_document_that_does_not_fit_is_named_too(fake_db, monkeypatch, caplog):
    await knowledge.save("t-1", "Handbook", HANDBOOK)
    await asyncio.sleep(0.01)
    await knowledge.save("t-1", "Service", SECOND)      # newest first: this one fits
    monkeypatch.setattr(settings, "knowledge_max_chars", 1_100)
    knowledge.forget_context()
    with caplog.at_level(logging.WARNING, logger="app.repositories.knowledge"):
        built = await knowledge.report("t-1")
    assert built.cut and built.cut_at == "# 1. Prices"
    assert built.chars == len(HANDBOOK) + len(SECOND)


def test_the_preview_reports_the_real_total_and_whether_it_is_cut(client, tenant_factory, auth,
                                                                  monkeypatch):
    _, key = tenant_factory("111", "Acme")
    asyncio.run(knowledge.save("111", "Handbook", HANDBOOK))
    monkeypatch.setattr(settings, "knowledge_max_chars", 1_200)
    body = client.get("/api/knowledge/preview", headers=auth(key)).json()
    assert body["chars"] == len(HANDBOOK), "chars must be the untruncated total"
    assert body["cut"] is True and body["cutAt"] == "# 2. Conduct rules"
    assert body["limitChars"] == 1_200 and body["includedChars"] <= 1_220

    monkeypatch.setattr(settings, "knowledge_max_chars", 120_000)
    body = client.get("/api/knowledge/preview", headers=auth(key)).json()
    assert body["cut"] is False and body["cutAt"] is None
    assert body["chars"] == len(HANDBOOK)
    assert client.get("/api/knowledge", headers=auth(key)).json()["limitChars"] == 120_000


# ---------------------------------------------------------------------------
# F5: tables that lost cells
# ---------------------------------------------------------------------------

BROKEN = """
3.4 Quick comparison
Specification | ATTO 2 | ATTO 3 | SEAL Dynamic | SEAL Premium | SEALION 7 | SHARK 6
Powertrain | BEV | BEV | BEV | BEV | BEV | PHEV
Max power | 130 kW | 150 kW | 230 kW | 321 kW
Battery | 45.12 kWh | 49.92 kWh | 61.44 kWh | 82.56 kWh
Drive | FWD | FWD | RWD | RWD | RWD | AWD
"""
WHOLE = """
| Specification | ATTO 2 | ATTO 3 |
|---|---|---|
| Max power | 130 kW | 150 kW |
| Battery | 45.12 kWh | 49.92 kWh |

Not a table | at all
"""


def test_a_byd_style_table_with_lost_cells_is_flagged():
    found = knowledge.table_warnings(BROKEN)
    assert found == {"count": 2, "rows": ["Max power", "Battery"]}


def test_a_well_formed_table_is_not():
    assert knowledge.table_warnings(WHOLE) is None
    assert knowledge.table_warnings("plain prose, no pipes") is None
    assert knowledge.table_warnings("") is None


async def test_the_warning_is_stored_logged_and_listed(fake_db, caplog):
    with caplog.at_level(logging.WARNING, logger="app.repositories.knowledge"):
        stored = await knowledge.save("t-1", "Spec sheet", BROKEN)
    assert stored["tableWarnings"] == {"count": 2, "rows": ["Max power", "Battery"]}
    assert any("2 table row(s) with fewer cells" in r.getMessage() for r in caplog.records)
    [listed] = await knowledge.listing("t-1")
    assert listed["tableWarnings"]["count"] == 2

    await knowledge.save("t-1", "Fine", WHOLE)
    fine = next(d for d in await knowledge.listing("t-1") if d["name"] == "Fine")
    assert fine["tableWarnings"] is None


def test_the_listing_route_returns_the_warning(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    client.post("/api/knowledge?name=Spec", headers={**auth(key), "content-type": "text/plain"},
                content=BROKEN.encode())
    [doc] = client.get("/api/knowledge", headers=auth(key)).json()["documents"]
    assert doc["tableWarnings"] == {"count": 2, "rows": ["Max power", "Battery"]}


def test_the_size_script_exists_and_is_read_only():
    from pathlib import Path

    source = Path("scripts/knowledge_size.py").read_text(encoding="utf-8")
    assert "knowledge.report(" in source and "count_tokens" in source
    assert "tableWarnings" in source
    for writes in ("upsert(", ".save(", ".delete("):
        assert writes not in source
