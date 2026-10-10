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


# ---------------------------------------------------------------------------
# The agent that answers the number answers the message
# ---------------------------------------------------------------------------
#
# This used to read the company's tenant-level persona whatever the number
# said, so the agent chosen for messages contributed its knowledge base and
# nothing else — the Numbers page promised "this message is answered by X"
# and then something else answered.


def test_the_agents_own_persona_wins_over_the_companys():
    text = build_instructions(
        _tenant(persona="You are the company's general assistant."),
        "",
        {"persona": "You are Bilal, the Jazz helpline."},
    )
    assert text.startswith("You are Bilal, the Jazz helpline.")
    assert "general assistant" not in text


def test_without_an_agent_the_company_persona_is_still_used():
    """A company that never built an agent keeps working exactly as before."""
    text = build_instructions(_tenant(persona="You are the clinic."), "", {})
    assert text.startswith("You are the clinic.")


def test_the_agents_tone_and_rules_reach_the_reply():
    """A company could write "never promise a refund" on its agent and watch
    the WhatsApp reply promise a refund, because none of this was read."""
    text = build_instructions(_tenant(), "", {
        "persona": "You are Bilal.",
        "tone": "Dry and brief. Never promise a refund.",
        "escalation": "Anything legal goes to a person.",
        "extraRules": "Always greet in Urdu first.",
    })
    assert "Never promise a refund." in text
    assert "Anything legal goes to a person." in text
    assert "Always greet in Urdu first." in text


def test_the_agents_language_wins_over_the_companys():
    text = build_instructions(_tenant(language="en"), "", {"language": "ur-PK"})
    assert "ur-PK" in text


# ---------------------------------------------------------------------------
# Written, not spoken
# ---------------------------------------------------------------------------


def test_it_is_told_to_write_figures_as_digits():
    """The persona is shared with the voice agent, which is told to say
    figures as words so a phone line does not garble them. In writing that
    produced "ninety-eight percent" where a reader expects 98%."""
    text = build_instructions(_tenant(), "")
    assert "digits" in text
    assert "98%" in text


def test_it_is_told_to_finish_its_sentences():
    assert "mid-sentence" in build_instructions(_tenant(), "")


# ---------------------------------------------------------------------------
# Running out of room
# ---------------------------------------------------------------------------
#
# Measured on a real reply with the 66k-character university knowledge base:
# 573 of a 600-token budget went on thinking and 23 on the answer, which
# arrived cut mid-sentence — "you may be eligible for an estimated" — and was
# sent to the customer, who replied "Estimated what?".


class _Candidate:
    def __init__(self, reason):
        self.finish_reason = reason


class _Response:
    def __init__(self, reason):
        self.candidates = [_Candidate(reason)]


def test_being_cut_off_is_noticed():
    from app.services.agent.reply import _ran_out_of_room

    assert _ran_out_of_room(_Response("MAX_TOKENS")) is True
    assert _ran_out_of_room(_Response("FinishReason.MAX_TOKENS")) is True
    assert _ran_out_of_room(_Response("STOP")) is False
    assert _ran_out_of_room(_Response(None)) is False


def test_a_cut_off_reply_is_rolled_back_to_its_last_whole_sentence():
    from app.services.agent.reply import _last_whole_sentence

    cut = ("Fees are 120,000 per semester. With 98% you may be eligible for "
           "an estimated")
    assert _last_whole_sentence(cut) == "Fees are 120,000 per semester."


def test_a_question_or_exclamation_also_counts_as_finished():
    from app.services.agent.reply import _last_whole_sentence

    assert _last_whole_sentence("Which programme? And also the fee str") \
        == "Which programme?"
    assert _last_whole_sentence("Congratulations! Now about the fee str") \
        == "Congratulations!"


def test_half_a_thought_is_sent_as_nothing_at_all():
    """The caller treats an empty reply as "the agent had nothing to say",
    which is better than a sentence that stops in the middle."""
    from app.services.agent.reply import _last_whole_sentence

    assert _last_whole_sentence("With ninety-eight percent marks you may be") == ""
    assert _last_whole_sentence("") == ""


def test_the_token_budget_leaves_room_for_an_answer():
    """Thinking comes out of this. 600 was not enough and the proof was a
    customer asking "Estimated what?"."""
    from app.services.agent.reply import MAX_OUTPUT_TOKENS

    assert MAX_OUTPUT_TOKENS >= 1200
