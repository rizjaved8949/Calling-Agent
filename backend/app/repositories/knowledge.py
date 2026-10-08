"""
What a company's agent is allowed to know.

Documents the company uploads — a price list, an FAQ, a prospectus — kept per
tenant and handed to the model when it answers. Stored in `voice_knowledge`,
which already existed for exactly this.

Text, not embeddings. A modern long-context model can read a whole handbook in
the prompt, and skipping retrieval removes an ONNX runtime, an embedding store
and a similarity threshold that all have to be right before a single question
can be answered. When a company's material outgrows the context window this
becomes the place retrieval goes, behind the same `context_for()` call.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from ..db.supabase import supabase

log = logging.getLogger(__name__)

TABLE = "voice_knowledge"

# Roughly what one long-context request should carry. Gemini allows far more,
# but every character is paid for on every message, and a handbook that does
# not fit is a sign the company wants retrieval rather than a bigger prompt.
MAX_CONTEXT_CHARS = 120_000


def _row_id(tenant_id: str, name: str) -> str:
    """Stable per tenant and document name, so re-uploading replaces."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{tenant_id}:{digest}"


async def save(tenant_id: str, name: str, text: str) -> dict[str, Any]:
    document = {
        "name": name,
        "text": text,
        "chars": len(text),
        "updatedAt": time.time(),
    }
    await supabase.upsert(
        TABLE,
        {
            "id": _row_id(tenant_id, name),
            "tenant_id": tenant_id,
            "data": document,
            "updated_at": _timestamp(),
        },
    )
    log.info("tenant %s: stored %r (%d chars)", tenant_id, name, len(text))
    return {"id": _row_id(tenant_id, name), **{k: v for k, v in document.items() if k != "text"}}


async def listing(tenant_id: str) -> list[dict[str, Any]]:
    """Documents without their text — the settings screen only needs names."""
    rows = await supabase.select(
        TABLE, params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc"}
    )
    out = []
    for row in rows:
        data = row.get("data") or {}
        out.append(
            {
                "id": row.get("id"),
                "name": data.get("name", ""),
                "chars": data.get("chars", 0),
                "updatedAt": data.get("updatedAt"),
            }
        )
    return out


async def delete(tenant_id: str, document_id: str) -> None:
    # Scoped by tenant as well as id: an id alone would let one company delete
    # another's material by guessing.
    await supabase.delete(
        TABLE, params={"id": f"eq.{document_id}", "tenant_id": f"eq.{tenant_id}"}
    )


async def context_for(tenant_id: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    """Everything the agent may quote, as one block for the prompt.

    Newest first, and truncated at a boundary rather than mid-sentence: a
    document cut in half mid-fact invites the model to finish the sentence
    itself, which reads exactly like the rest of the answer.
    """
    rows = await supabase.select(
        TABLE, params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc"}
    )
    parts: list[str] = []
    used = 0
    for row in rows:
        data = row.get("data") or {}
        text = str(data.get("text") or "").strip()
        if not text:
            continue
        header = f"--- {data.get('name', 'document')} ---\n"
        room = limit - used - len(header)
        if room <= 200:
            break
        if len(text) > room:
            cut = text.rfind("\n", 0, room)
            text = text[: cut if cut > room // 2 else room] + "\n[…truncated]"
        parts.append(header + text)
        used += len(header) + len(text)
    return "\n\n".join(parts)


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
