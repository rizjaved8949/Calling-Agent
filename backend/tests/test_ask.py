"""
A staff member checking something mid-call.

The actual Gemini call is faked — these tests are about the plumbing
(knowledge resolution, the "not configured" response, the shape returned),
not about whether Gemini itself answers well.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def fake_reply(monkeypatch):
    """Stands in for `services.agent.reply.ask`: echoes whether the word
    "fee" appears in the knowledge it was given, so a test can tell which
    knowledge base's documents actually reached it."""
    from app.api.routes import ask as ask_route

    async def fake(tenant, question, knowledge):
        if "fee" in knowledge.lower():
            return {"answered": True, "text": "The fee is 2000 rupees."}
        return {"answered": False, "text": "Not in this knowledge base."}

    monkeypatch.setattr(ask_route.reply, "available", lambda: True)
    monkeypatch.setattr(ask_route.reply, "ask", fake)


def test_asking_answers_from_the_named_knowledge_base(client, tenant_factory, auth, fake_reply):
    _, key = tenant_factory("370")
    kb = client.post("/api/knowledge-bases", json={"name": "Prices", "purpose": "", "isDefault": False},
                     headers=auth(key)).json()
    client.post(f"/api/knowledge?name=prices.txt&knowledgeBaseId={kb['id']}",
               content=b"The fee is 2000 rupees.",
               headers={**auth(key), "Content-Type": "text/plain"})

    resp = client.post("/api/ask", json={"question": "What is the fee?", "knowledgeBaseId": kb["id"]},
                       headers=auth(key))
    assert resp.status_code == 200
    assert resp.json()["answered"] is True


def test_asking_with_no_base_named_uses_everything(client, tenant_factory, auth, fake_reply):
    _, key = tenant_factory("371")
    client.post("/api/knowledge?name=hours.txt", content=b"The fee is 2000 rupees.",
               headers={**auth(key), "Content-Type": "text/plain"})

    resp = client.post("/api/ask", json={"question": "fee?"}, headers=auth(key))
    assert resp.json()["answered"] is True


def test_asking_against_an_unrelated_base_says_it_does_not_know(client, tenant_factory, auth, fake_reply):
    _, key = tenant_factory("372")
    kb = client.post("/api/knowledge-bases", json={"name": "Support", "purpose": "", "isDefault": False},
                     headers=auth(key)).json()
    client.post(f"/api/knowledge?name=support.txt&knowledgeBaseId={kb['id']}",
               content=b"Refunds take five days.",
               headers={**auth(key), "Content-Type": "text/plain"})

    resp = client.post("/api/ask", json={"question": "What is the fee?", "knowledgeBaseId": kb["id"]},
                       headers=auth(key))
    assert resp.json()["answered"] is False


def test_asking_a_knowledge_base_that_does_not_exist_is_rejected(client, tenant_factory, auth, fake_reply):
    _, key = tenant_factory("373")
    resp = client.post("/api/ask", json={"question": "fee?", "knowledgeBaseId": "nope"}, headers=auth(key))
    assert resp.status_code == 422


def test_asking_is_refused_when_not_configured(client, tenant_factory, auth, monkeypatch):
    from app.api.routes import ask as ask_route

    monkeypatch.setattr(ask_route.reply, "available", lambda: False)
    _, key = tenant_factory("374")
    resp = client.post("/api/ask", json={"question": "fee?"}, headers=auth(key))
    assert resp.status_code == 503
