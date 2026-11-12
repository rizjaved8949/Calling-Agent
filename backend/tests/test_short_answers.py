"""
The agent answers what was asked: every part, in order, briefly.

From a real call: the agent had about fifteen seconds of one answer still
queued when the caller cut in. And from the evaluation that followed: a
four-part question answered in part, with "the team will tell you the
duration" for a figure that was in the material.
"""
from __future__ import annotations

from app.services.agent import prompt


def _built(**extra) -> str:
    return " ".join(prompt.build(persona="You answer calls.", context="FEES: ...",
                                 **extra).split())


def test_every_question_is_answered_briefly_and_in_order():
    text = _built()
    assert "Answer every question the caller asked, in the order they asked" in text
    assert "one or two short sentences each, then stop" in text
    assert "Do not add background they did not ask for" in text


def test_an_answer_in_the_material_is_never_deferred():
    text = _built()
    assert "If the answer is in your material, give it now" in text
    assert "Never say the team will tell them, never move it to WhatsApp" in text


def test_long_lists_are_given_in_full_only_when_asked():
    assert "give them all briefly if asked for all; otherwise name two or three" in _built()


def test_the_rules_reach_the_model_with_the_material():
    """In the prompt the model gets, not only in the settings screen."""
    text = _built()
    assert "FEES: ..." in text and "in the order they asked" in text


def test_a_company_that_wrote_its_own_speaking_rules_keeps_them():
    text = _built(written={"speaking": "Explain everything in full detail."})
    assert "Explain everything in full detail." in text
    assert "in the order they asked" not in text
