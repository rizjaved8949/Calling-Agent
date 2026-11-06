"""
The agent answers in a sentence or two, not a recital.

From a real call: the agent had about fifteen seconds of one answer still
queued when the caller cut in, which means it was reading out far more than
anyone on a phone can follow.
"""
from __future__ import annotations

from app.services.agent import prompt


def _built(**extra) -> str:
    return " ".join(prompt.build(persona="You answer calls.", context="FEES: ...",
                                 **extra).split())


def test_every_answer_is_kept_to_a_sentence_or_two():
    text = _built()
    assert "Keep every answer short: one or two sentences, about ten seconds of speech" in text
    assert "Answer exactly what they asked, then stop" in text


def test_a_long_answer_is_offered_in_parts():
    text = _built()
    assert "give the most important part, then ask whether they want the rest" in text
    assert "Aur detail bataun?" in text


def test_lists_are_not_read_out():
    assert "Never read out a list. Name two or three at most" in _built()


def test_the_rules_reach_the_model_with_the_material():
    """In the prompt the model gets, not only in the settings screen."""
    text = _built()
    assert "FEES: ..." in text and "about ten seconds of speech" in text


def test_a_company_that_wrote_its_own_speaking_rules_keeps_them():
    text = _built(written={"speaking": "Explain everything in full detail."})
    assert "Explain everything in full detail." in text
    assert "about ten seconds of speech" not in text
