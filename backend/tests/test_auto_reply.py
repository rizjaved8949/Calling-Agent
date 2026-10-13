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


# ---------------------------------------------------------------------------
# Shortening a long reply without butchering it
# ---------------------------------------------------------------------------
#
# The old trim looked only for ". " — a full stop followed by a space. An
# answer to a multi-part question separates its parts with blank lines, so its
# periods are followed by newlines and that search finds almost nothing:
# measured on a real four-part Jazz answer, the last ". " within 900
# characters was at position 141. So the reply was either cut back to a
# seventh of itself or chopped at exactly 900 characters in the middle of a
# word — "I will check and have some", sent to a customer.


def _trim(text, limit=None):
    from app.services.agent.reply import MAX_REPLY_CHARS, _trim_to

    return _trim_to(text, limit or MAX_REPLY_CHARS)


def test_a_short_reply_is_left_alone():
    assert _trim("All done.") == "All done."


def test_a_long_reply_is_cut_at_a_finished_sentence():
    text = "This is a sentence. " * 200
    out = _trim(text)
    assert out.endswith("sentence.")
    assert len(out) <= 1200


def test_a_multipart_answer_is_not_cut_back_to_its_first_line():
    """Its periods are followed by newlines, not spaces. The old search found
    one at position 141 and threw away the rest."""
    text = "Short intro. " + ("Bundle line with no period\n\n" * 60)
    out = _trim(text)
    assert len(out) > 1000, f"kept only {len(out)} characters"


def test_a_reply_is_never_cut_in_the_middle_of_a_word():
    out = _trim("supercalifragilistic " * 200)
    assert out.endswith("…")
    assert out[:-1].endswith("supercalifragilistic")


def test_an_ellipsis_marks_a_reply_that_was_cut_short():
    """Cut at a word boundary it would otherwise read as a finished thought."""
    assert _trim("word " * 400).endswith("…")


def test_an_urdu_full_stop_counts_as_the_end_of_a_sentence():
    """These replies answer in the language they were asked in, and Urdu does
    not use a full stop."""
    text = "Balance check karne ke liye *111* dial karein۔ " * 40
    out = _trim(text)
    assert out.endswith("۔")


def test_a_question_or_exclamation_ends_a_sentence_too():
    text = "Which programme did you mean? " * 100
    assert _trim(text).endswith("?")


def test_the_trim_never_exceeds_the_limit():
    for text in ("x" * 5000, "word " * 500, "Sentence. " * 300):
        assert len(_trim(text)) <= 1201


def test_a_decimal_point_is_not_mistaken_for_a_sentence_end():
    """"91.3 out of 100" must not be cut to "91." — the period has no space
    after it, which is exactly what the lookahead is for."""
    from app.services.agent.reply import _whole_sentences

    assert _whole_sentences("The score is 91.3 out of 100") == ""
    assert _whole_sentences("The score is 91.3 out of 100. Next") \
        == "The score is 91.3 out of 100."


def test_the_cap_leaves_room_for_a_multipart_answer():
    """A real four-part question got a complete, useful 818-character answer.
    A cap that butchers that is worse than one that lets it breathe."""
    from app.services.agent.reply import MAX_REPLY_CHARS

    assert MAX_REPLY_CHARS >= 1000


# ---------------------------------------------------------------------------
# The persona, said again after the material
# ---------------------------------------------------------------------------
#
# The same burial the voice prompt suffers. A persona placed before tens of
# thousands of characters loses to them: the university agent is told to say
# "the university" rather than name it, while its own material names it 177
# times, and the name reached the customer in 1 reply in 21 with the rule only
# at the top. None in 21 with it repeated after.


def test_the_persona_is_repeated_after_the_material():
    text = build_instructions(
        _tenant(persona="You are the clinic. Never name it."),
        "MATERIAL: the clinic is called Acme Health. " * 50,
    )
    assert text.count("You are the clinic. Never name it.") == 2
    assert text.rfind("You are the clinic") > text.find("MATERIAL")


def test_the_repeat_says_which_wins():
    """The material describes the business and will word things its own way.
    Without this the agent has two sources and no order between them."""
    text = build_instructions(_tenant(persona="Say 'the clinic'."), "material " * 100)
    assert "The material describes the business; this describes you." in text


def test_an_agents_persona_is_the_one_repeated():
    text = build_instructions(
        _tenant(persona="the company default"), "material " * 100,
        {"persona": "You are Bilal."},
    )
    assert text.count("You are Bilal.") == 2
    assert "the company default" not in text


def test_nothing_is_repeated_when_there_is_no_material():
    """There is nothing to be buried by, so saying it twice is just noise."""
    text = build_instructions(_tenant(persona="You are the clinic."), "")
    assert text.count("You are the clinic.") == 1
