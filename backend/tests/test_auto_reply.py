"""
The prompt an auto-reply is built from, and the knowledge it draws on.

Not the model's answer — that is Gemini's job and changes between versions.
What is pinned here is everything the model is *told*, because those are the
instructions that stop it inventing a price the business then has to honour,
or answering in markdown that WhatsApp renders as punctuation.
"""
from __future__ import annotations

import pytest

from app.models.tenant import Tenant
from app.services.agent.reply import MAX_REPLY_CHARS, build_instructions


def _tenant(**kw) -> Tenant:
    return Tenant(phoneNumberId="1", **kw)


# ---------------------------------------------------------------------------
# The instructions
# ---------------------------------------------------------------------------


def test_the_companys_own_persona_leads():
    text = build_instructions(_tenant(persona="You are Ayesha from UCP admissions."), "")
    assert text.startswith("You are Ayesha from UCP admissions.")


def test_a_company_with_no_persona_still_gets_sensible_instructions():
    text = build_instructions(_tenant(), "")
    assert "assistant" in text.lower()


def test_markdown_is_forbidden():
    """WhatsApp renders none of it; it arrives as asterisks and hashes."""
    text = build_instructions(_tenant(), "")
    assert "markdown" in text.lower()
    assert "bullet" in text.lower()


def test_the_length_limit_is_stated():
    assert str(MAX_REPLY_CHARS) in build_instructions(_tenant(), "")


def test_knowledge_is_included_and_fenced_with_a_no_guessing_rule():
    text = build_instructions(_tenant(), "Fees are 120,000 per semester.")
    assert "Fees are 120,000 per semester." in text
    assert "do not guess" in text.lower()


def test_without_knowledge_the_agent_is_told_to_state_no_facts():
    """The dangerous case: asked about fees with nothing to go on."""
    text = build_instructions(_tenant(), "")
    assert "no reference material" in text.lower()
    assert "prices" in text.lower()


def test_the_business_language_is_passed_through():
    text = build_instructions(_tenant(language="ur-PK"), "")
    assert "ur-PK" in text


def test_without_a_language_it_mirrors_the_writer():
    text = build_instructions(_tenant(), "")
    assert "whatever language" in text.lower()


# ---------------------------------------------------------------------------
# Assembling the knowledge
# ---------------------------------------------------------------------------


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.configured = True

    async def select(self, _table, *, params=None):
        return list(self.rows)


@pytest.fixture
def documents(monkeypatch):
    def install(*docs):
        rows = [
            {"id": f"t:{i}", "tenant_id": "t",
             "data": {"name": name, "text": text, "chars": len(text)}}
            for i, (name, text) in enumerate(docs)
        ]
        from app.repositories import knowledge

        monkeypatch.setattr(knowledge, "supabase", FakeDb(rows))
    return install


async def test_every_document_is_offered_with_its_name(documents):
    from app.repositories import knowledge

    documents(("Prices", "A costs 10"), ("Hours", "Open at nine"))
    context = await knowledge.context_for("t")
    assert "--- Prices ---" in context and "A costs 10" in context
    assert "--- Hours ---" in context and "Open at nine" in context


async def test_a_document_too_large_is_cut_at_a_line_not_mid_sentence(documents):
    """A fact cut in half invites the model to finish it, and that reads
    exactly like the rest of the answer."""
    from app.repositories import knowledge

    body = "\n".join(f"line {i} of the handbook" for i in range(500))
    documents(("Handbook", body))
    context = await knowledge.context_for("t", limit=2000)
    assert len(context) <= 2100
    assert context.rstrip().endswith("[…truncated]")


async def test_an_empty_document_is_skipped(documents):
    from app.repositories import knowledge

    documents(("Empty", "   "), ("Real", "something true"))
    context = await knowledge.context_for("t")
    assert "Empty" not in context
    assert "something true" in context


async def test_no_documents_is_an_empty_context_not_an_error(documents):
    from app.repositories import knowledge

    documents()
    assert await knowledge.context_for("t") == ""


# ---------------------------------------------------------------------------
# The auto-reply switch
# ---------------------------------------------------------------------------


def test_auto_reply_is_off_until_a_company_turns_it_on():
    """A company that has uploaded nothing would answer questions from nothing."""
    assert _tenant().auto_reply is False
    assert _tenant(autoReply=True).auto_reply is True


def test_the_switch_is_visible_to_the_settings_screen():
    assert _tenant(autoReply=True).public()["autoReply"] is True
