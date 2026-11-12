"""
Prices spoken as lakh and crore, from the material rather than guessed.

On real calls 8,990,000 was read as "9 lakh 90 hazar". The material now
carries the spoken form after each amount, and the agent reads that.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.agent import amounts
from app.services.agent.amounts import annotate, spoken_pkr


@pytest.mark.parametrize("amount, words", [
    (7_290_000, "72 lakh 90 hazar"),
    (8_990_000, "89 lakh 90 hazar"),
    (16_990_000, "1 crore 69 lakh 90 hazar"),
    (12_000, "12 hazar"),
    (1_400_000, "14 lakh"),
    (0, "0"),
    (999, "999"),
    (1_000, "1 hazar"),
    (100_000, "1 lakh"),
    (10_000_000, "1 crore"),
    (12_500, "12 hazar 500"),
    (1_234_567, "12 lakh 34 hazar 567"),
    (1_750_000, "17 lakh 50 hazar"),
    (1_234_567.89, "12 lakh 34 hazar 568"),
    (12_000.4, "12 hazar"),
    (-298_000, "minus 2 lakh 98 hazar"),
    (250_000_000, "25 crore"),
])
def test_amounts_are_spoken_the_way_people_say_them(amount, words):
    assert spoken_pkr(amount) == words


@pytest.mark.parametrize("text, expected", [
    ("Price: PKR 8,990,000 ex-factory.", "Price: PKR 8,990,000 (89 lakh 90 hazar) ex-factory."),
    ("Rs. 12,000 per year", "Rs. 12,000 (12 hazar) per year"),
    ("Rs 12,000 per year", "Rs 12,000 (12 hazar) per year"),
    ("Advance 1,400,000 rupees", "Advance 1,400,000 rupees (14 lakh)"),
    ("PKR 1.75 million", "PKR 1.75 million (17 lakh 50 hazar)"),
    ("Rupees 7,290,000", "Rupees 7,290,000 (72 lakh 90 hazar)"),
    ("₨16,990,000", "₨16,990,000 (1 crore 69 lakh 90 hazar)"),
    ("PKR 89,90,000 (Indian grouping)", "PKR 89,90,000 (89 lakh 90 hazar) (Indian grouping)"),
])
def test_rupee_amounts_get_their_spoken_form_once(text, expected):
    assert annotate(text) == expected


@pytest.mark.parametrize("text", [
    "PKR 8,990,000 (89 lakh 90 hazar)",          # already written by the owner
    "Rs 89 lakh 90 hazar",                        # already in words
    "PKR 1.5 crore",
    "Rs 500",                                     # too small to misread
    "Range 410 km, battery 49.92 kWh",            # not money
    "8,990,000",                                  # no currency marker: could be anything
    "Call 0300 1234567",
])
def test_text_that_needs_nothing_is_left_exactly_as_it_was(text):
    assert annotate(text) == text


def test_annotating_twice_changes_nothing():
    text = "PKR 8,990,000, advance Rs. 1,750,000, Rs 12,000 token tax."
    once = annotate(text)
    assert once == ("PKR 8,990,000 (89 lakh 90 hazar), advance Rs. 1,750,000 "
                    "(17 lakh 50 hazar), Rs 12,000 (12 hazar) token tax.")
    assert annotate(once) == once


def test_a_table_cell_keeps_its_shape():
    row = "| Price (PKR) | 7,290,000 PKR | 8,990,000 PKR |"
    assert annotate(row) == ("| Price (PKR) | 7,290,000 PKR (72 lakh 90 hazar) | "
                             "8,990,000 PKR (89 lakh 90 hazar) |")


def test_who_gets_the_spoken_forms():
    assert amounts.wants_spoken("ur-PK", "")
    assert amounts.wants_spoken("ur", "44")
    assert amounts.wants_spoken("", "92")
    assert amounts.wants_spoken("en", "+92")
    assert not amounts.wants_spoken("en-GB", "44")
    assert not amounts.wants_spoken("", "")


async def test_an_urdu_agent_reads_annotated_material_and_an_english_one_does_not(fake_db):
    from app.repositories import knowledge

    await knowledge.save("t-1", "Prices", "ATTO 3: PKR 8,990,000.")
    urdu = await knowledge.context_for("t-1", spoken_amounts=True)
    english = await knowledge.context_for("t-1", spoken_amounts=False)
    assert "PKR 8,990,000 (89 lakh 90 hazar)" in urdu
    assert "(89 lakh 90 hazar)" not in english
    # The stored document is untouched.
    rows = await knowledge._rows_for("t-1")
    assert "(89 lakh" not in rows[0]["data"]["text"]


async def test_routing_decides_by_the_agents_language_or_the_companys_country(fake_db, monkeypatch):
    from app.models.agent import Agent
    from app.models.tenant import Tenant
    from app.repositories import knowledge
    from app.services import routing

    await knowledge.save("t-1", "Prices", "ATTO 3: PKR 8,990,000.")
    plain = Tenant(phoneNumberId="t-1", name="Acme")
    pakistani = Tenant(phoneNumberId="t-1", name="Acme", defaultCountryCode="92")
    urdu_agent = Agent(tenantId="t-1", name="A", language="ur-PK")
    english_agent = Agent(tenantId="t-1", name="B", language="en")

    def resolved(agent):
        return routing.Resolved(agent=agent, knowledge_base_id="", knowledge_base_name="",
                                resolved_by="company")

    assert "(89 lakh" in await routing.context_for(plain, resolved(urdu_agent))
    assert "(89 lakh" not in await routing.context_for(plain, resolved(english_agent))
    assert "(89 lakh" in await routing.context_for(pakistani, resolved(english_agent))
    assert "(89 lakh" not in await routing.context_for(plain, resolved(None))


def test_the_speaking_rule_names_the_brackets():
    from app.services.agent import prompt

    assert "Say rupee amounts using the words in brackets after them, exactly." in (
        prompt.SPEAKING.default)
