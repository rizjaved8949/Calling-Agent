"""
The help assistant on the Guides page.

It answers questions about the product, not about a company's customers, and
it can be shown a screenshot rather than asking somebody to transcribe an
error before it will look.
"""
from __future__ import annotations

import pytest

from app.services import help as help_service


def test_it_refuses_a_file_type_it_cannot_read():
    import asyncio

    result = asyncio.run(help_service.answer(
        "what is this?", attachment=b"MZ\x90", attachment_type="application/x-msdownload",
    ))
    assert result["answered"] is False
    assert "cannot be read" in result["text"]


def test_it_refuses_an_attachment_that_is_too_large():
    import asyncio

    too_big = b"x" * (help_service.MAX_ATTACHMENT_BYTES + 1)
    result = asyncio.run(help_service.answer(
        "what is this?", attachment=too_big, attachment_type="image/png",
    ))
    assert result["answered"] is False
    assert "too large" in result["text"]


def test_an_empty_question_with_no_attachment_is_refused():
    import asyncio

    assert asyncio.run(help_service.answer("  "))["answered"] is False


def test_the_documentation_covers_what_people_actually_ask():
    """The assistant is only as good as this text, so the facts it has to
    answer from are pinned rather than left to drift."""
    guide = help_service.PRODUCT_GUIDE
    for fact in [
        "Voice/Calls scope",          # the commonest Infobip mistake
        "System User",                # the commonest Meta mistake
        "24 hours",                   # the WhatsApp window
        "declines incoming calls",    # why a number goes quiet
        "no way to unsend",           # what deleting a message does
        "Dialer",                     # where staff land
        "scanned PDF",                # why an upload reads as empty
    ]:
        assert fact in guide, f"the guide no longer explains {fact!r}"


def test_it_is_not_given_the_companys_own_documents():
    """Mixing the two would have it quoting a price list at somebody asking
    how to connect a number."""
    import inspect

    source = inspect.getsource(help_service.answer)
    assert "knowledge_repo" not in source
    assert "context_for" not in source


def test_the_endpoint_needs_a_company_key(client):
    assert client.post("/api/help", data={"question": "hello"}).status_code == 401


def test_the_endpoint_answers_without_an_attachment(client, tenant_factory, auth, monkeypatch):
    _, key = tenant_factory("995")

    async def fake(question, **kw):
        return {"answered": True, "text": f"answering: {question}"}

    monkeypatch.setattr(help_service, "answer", fake)
    body = client.post("/api/help", data={"question": "How do I connect a number?"},
                       headers=auth(key)).json()
    assert body == {"answered": True, "text": "answering: How do I connect a number?"}


def test_an_attachment_reaches_the_assistant(client, tenant_factory, auth, monkeypatch):
    _, key = tenant_factory("996")
    seen = {}

    async def fake(question, *, attachment=None, attachment_type="", attachment_name=""):
        seen.update(size=len(attachment or b""), type=attachment_type, name=attachment_name)
        return {"answered": True, "text": "I can see it."}

    monkeypatch.setattr(help_service, "answer", fake)
    client.post("/api/help",
                data={"question": "what does this say?"},
                files={"file": ("error.png", b"\x89PNG\r\n\x1a\n", "image/png")},
                headers=auth(key))
    assert seen == {"size": 8, "type": "image/png", "name": "error.png"}
