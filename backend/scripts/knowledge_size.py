#!/usr/bin/env python3
"""
How much of a company's material actually reaches the agent.

    python scripts/knowledge_size.py --tenant <phone number id>
    python scripts/knowledge_size.py --tenant <id> --base <knowledge base id>
    python scripts/knowledge_size.py                 # every company

Read-only. For each company it prints every document (name, knowledge base,
characters), the total, whether the agent's copy is cut at KNOWLEDGE_MAX_CHARS
and the first heading that falls off, any table whose rows lost cells, and,
when a Gemini key is configured, how many tokens the instructions plus the
material come to against the model's 131,072-token window. Raise the limit
only while that stays under about 60%; the rest is the conversation's.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.repositories import agents as agent_repo  # noqa: E402
from app.repositories import knowledge  # noqa: E402
from app.repositories import tenants as tenant_repo  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")

WINDOW_TOKENS = 131_072
#: Keep the instructions and the material under this share of the window.
SAFE_SHARE = 0.6


def _say(line: str = "") -> None:
    print(line, flush=True)


async def _count_tokens(text: str) -> int | None:
    """Gemini's own count for `text`, or None without a key or a reachable model."""
    key = settings.gemini_api_key.strip()
    if not key:
        with contextlib.suppress(Exception):
            from app.services import platform_settings

            key = (await platform_settings.current()).get("apiKey", "")
    if not key:
        return None
    try:
        from google import genai
    except ImportError:
        return None
    client = genai.Client(api_key=key)
    # The live model may not count; the text model's tokenizer is the same
    # family and close enough to size a prompt by.
    for model in (settings.gemini_live_model, settings.gemini_text_model):
        try:
            result = await client.aio.models.count_tokens(model=model, contents=text)
            return int(getattr(result, "total_tokens", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            _say(f"  (count_tokens on {model} failed: {type(exc).__name__}: {str(exc)[:100]})")
    return None


async def _instructions_for(tenant) -> str:
    """The prompt as a call would build it, with the material in it."""
    from app.services.agent import live

    persona = await live.persona_for(tenant)
    return persona.instructions


async def _one(tenant, base_id: str, *, tokens: bool) -> None:
    _say(f"== {tenant.name or tenant.phone_number_id} ({tenant.phone_number_id})")
    bases = {kb.id: kb.name for kb in await agent_repo.list_knowledge_bases(tenant.phone_number_id)}
    built = await knowledge.report(tenant.phone_number_id, base_id)
    if not built.documents:
        _say("  no documents")
        return
    width = max(len(d["name"]) for d in built.documents)
    for doc in built.documents:
        base = bases.get(doc["knowledgeBaseId"], doc["knowledgeBaseId"] or "(no base)")
        note = "" if doc["included"] == doc["chars"] else (
            "  <- CUT" if doc["included"] else "  <- NOT INCLUDED")
        _say(f"  {doc['name']:<{width}}  {base:<24} {doc['chars']:>9,} chars{note}")
        warnings = doc.get("tableWarnings")
        if warnings:
            _say(f"      ! {warnings['count']} table row(s) have fewer cells than their "
                 f"header: {', '.join(warnings['rows'])}")
    limit = knowledge.max_chars()
    _say(f"  total {built.chars:,} chars; limit {limit:,} (KNOWLEDGE_MAX_CHARS)")
    if built.cut:
        _say(f"  CUT: the agent will not see {built.cut_at!r} onwards")
    else:
        _say("  fits: nothing is cut")
    if tokens:
        text = await _instructions_for(tenant)
        counted = await _count_tokens(text)
        if counted is None:
            _say("  tokens: no Gemini key or no reachable model, so not counted")
        else:
            share = counted / WINDOW_TOKENS
            verdict = "ok" if share <= SAFE_SHARE else "TOO MUCH: lower KNOWLEDGE_MAX_CHARS"
            _say(f"  tokens: instructions + material = {counted:,} of {WINDOW_TOKENS:,} "
                 f"({share:.0%}; keep under {SAFE_SHARE:.0%}) -> {verdict}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--tenant", default="", help="one company's phone number id")
    parser.add_argument("--base", default="", help="one knowledge base id (default: all)")
    parser.add_argument("--no-tokens", action="store_true", help="skip the token count")
    args = parser.parse_args()

    if args.tenant:
        tenant = await tenant_repo.get(args.tenant)
        tenants = [tenant] if tenant else []
        if not tenants:
            _say(f"no company with id {args.tenant!r}")
            return 1
    else:
        tenants = await tenant_repo.list_all()
        if not tenants:
            _say("no companies found (is the database configured?)")
            return 1
    for tenant in tenants:
        await _one(tenant, args.base, tokens=not args.no_tokens)
        _say()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
