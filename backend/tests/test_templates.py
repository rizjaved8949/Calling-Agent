"""
WhatsApp templates: how many values one takes, and what it ends up saying.

Sending the wrong number of values is refused with "(#132000) Number of
parameters does not match the expected number of params" — which names neither
the template nor the expected count. A form that guesses produces an error
nobody can act on, so the count is read from the template itself.
"""
from __future__ import annotations

from app.services.whatsapp import describe_template, fill

REAL = {
    "name": "ucp_query_details",
    "status": "APPROVED",
    "category": "UTILITY",
    "language": "en",
    "components": [
        {"type": "BODY",
         "text": "Aap ka sawal: {{1}}\n\nJawab: {{2}}",
         "example": {"body_text": [["Admission documents", "CNIC aur marksheet."]]}},
        {"type": "FOOTER", "text": "University of Central Punjab, Lahore"},
    ],
}


def test_the_number_of_values_comes_from_the_template():
    described = describe_template(REAL)
    assert described["placeholders"] == 2
    assert described["examples"] == ["Admission documents", "CNIC aur marksheet."]
    assert described["footer"] == "University of Central Punjab, Lahore"


def test_a_template_with_no_placeholders_asks_for_nothing():
    plain = {"name": "hello_world", "language": "en_US", "components": [
        {"type": "HEADER", "text": "Hello World"},
        {"type": "BODY", "text": "Welcome and congratulations!"},
    ]}
    described = describe_template(plain)
    assert described["placeholders"] == 0
    assert described["examples"] == []
    assert described["header"] == "Hello World"


def test_the_count_is_the_highest_placeholder_not_how_many_appear():
    """A body may repeat {{1}}, and may not mention {{2}} until later. Meta
    counts positions, so counting occurrences would send one value too few."""
    repeated = {"name": "x", "components": [
        {"type": "BODY", "text": "Hello {{1}}, your order {{2}} is ready, {{1}}."},
    ]}
    assert describe_template(repeated)["placeholders"] == 2


def test_a_template_with_no_body_does_not_crash():
    assert describe_template({"name": "x", "components": []})["placeholders"] == 0
    assert describe_template({"name": "x"})["body"] == ""


def test_filling_a_template_produces_what_the_reader_sees():
    body = "Aap ka sawal: {{1}}\n\nJawab: {{2}}"
    assert fill(body, ["Fees?", "PKR 620."]) == "Aap ka sawal: Fees?\n\nJawab: PKR 620."


def test_an_unfilled_placeholder_is_left_visible():
    """So a preview shows what is still missing rather than a silent gap."""
    assert fill("Ref {{1}} and {{2}}", ["A"]) == "Ref A and {{2}}"


def test_spacing_inside_the_braces_is_tolerated():
    assert fill("Ref {{ 1 }}", ["A"]) == "Ref A"


def test_a_sent_template_is_logged_as_its_words(monkeypatch):
    """The log used to hold the values alone — "482103" tells whoever reads it
    back nothing. It now holds the sentence the person received."""
    import asyncio

    from app.models.tenant import Tenant
    from app.services.whatsapp import WhatsApp

    tenant = Tenant(phoneNumberId="t-1", name="Acme", wabaId="w-1",
                    accessToken="tok", metaPhoneNumberId="m-1")
    client = WhatsApp(tenant)

    async def fake_templates():
        return [describe_template(REAL)]

    async def fake_post(path, payload):
        return {"messages": [{"id": "wamid.1"}]}

    recorded = {}

    async def fake_record(**kw):
        recorded.update(kw)
        return None

    monkeypatch.setattr(client, "templates", fake_templates)
    monkeypatch.setattr(client, "_post", fake_post)
    monkeypatch.setattr(client, "_record", fake_record)

    asyncio.run(client.send_template(
        "+923001112222", "ucp_query_details", language="en",
        parameters=["Fees?", "PKR 620."],
    ))
    assert recorded["body"] == "Aap ka sawal: Fees?\n\nJawab: PKR 620."
