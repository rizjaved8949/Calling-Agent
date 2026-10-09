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


def _row_id(tenant_id: str, name: str, knowledge_base_id: str = "") -> str:
    """Stable per tenant, knowledge base and document name, so re-uploading
    the same file into the same base replaces it — and the same file into a
    different base does not."""
    key = f"{knowledge_base_id}:{name}" if knowledge_base_id else name
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{tenant_id}:{digest}"


async def save(
    tenant_id: str, name: str, text: str, knowledge_base_id: str = ""
) -> dict[str, Any]:
    document = {
        "name": name,
        "text": text,
        "chars": len(text),
        "knowledgeBaseId": knowledge_base_id,
        "updatedAt": time.time(),
    }
    row_id = _row_id(tenant_id, name, knowledge_base_id)
    await supabase.upsert(
        TABLE,
        {
            "id": row_id,
            "tenant_id": tenant_id,
            "data": document,
            "updated_at": _timestamp(),
        },
    )
    log.info("tenant %s: stored %r (%d chars)", tenant_id, name, len(text))
    forget_context(tenant_id)
    return {"id": row_id, **{k: v for k, v in document.items() if k != "text"}}


async def listing(
    tenant_id: str, knowledge_base_id: str | None = None
) -> list[dict[str, Any]]:
    """Documents without their text — the settings screen only needs names.

    `knowledge_base_id=None` lists everything the company has; a string lists
    one base's documents, and `""` lists the ones filed under no base at all
    (every document uploaded before knowledge bases existed).
    """
    rows = await supabase.select(
        TABLE, params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc"}
    )
    out = []
    for row in rows:
        data = row.get("data") or {}
        if knowledge_base_id is not None and str(data.get("knowledgeBaseId") or "") != knowledge_base_id:
            continue
        out.append(
            {
                "id": row.get("id"),
                "name": data.get("name", ""),
                "chars": data.get("chars", 0),
                "knowledgeBaseId": data.get("knowledgeBaseId", ""),
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
    forget_context(tenant_id)


# The prompt text for one knowledge base, briefly. Reading and joining tens of
# thousands of characters took 1.7 seconds of every call's opening silence, and
# documents do not change between two calls a minute apart. Invalidated on
# upload and delete; stale for at most this long otherwise.
_CONTEXT_TTL_SECONDS = 120.0
_context_cache: dict[tuple[str, str, int], tuple[float, str]] = {}


def forget_context(tenant_id: str = "") -> None:
    """Drop cached prompt text after documents change."""
    if not tenant_id:
        _context_cache.clear()
        return
    for key in [k for k in _context_cache if k[0] == tenant_id]:
        _context_cache.pop(key, None)


async def context_for(
    tenant_id: str,
    limit: int = MAX_CONTEXT_CHARS,
    knowledge_base_id: str = "",
) -> str:
    key = (tenant_id, knowledge_base_id, limit)
    cached = _context_cache.get(key)
    if cached and time.monotonic() - cached[0] < _CONTEXT_TTL_SECONDS:
        return cached[1]
    text = await _read_context(tenant_id, limit, knowledge_base_id)
    _context_cache[key] = (time.monotonic(), text)
    return text


async def _read_context(
    tenant_id: str,
    limit: int = MAX_CONTEXT_CHARS,
    knowledge_base_id: str = "",
) -> str:
    """Everything the agent may quote, as one block for the prompt.

    Newest first, and truncated at a boundary rather than mid-sentence: a
    document cut in half mid-fact invites the model to finish the sentence
    itself, which reads exactly like the rest of the answer.

    With a `knowledge_base_id`, only that base's documents — this is what
    makes one number answer from the price list and another from the support
    handbook. Without one, everything the company has, which is both the
    old behaviour and the right fallback for a company that never split its
    documents up.

    A base that exists but holds nothing falls back to the whole pile rather
    than answering from silence: an agent that suddenly knows nothing because
    somebody made an empty base is a worse failure than one that knows too
    much.
    """
    rows = await supabase.select(
        TABLE, params={"tenant_id": f"eq.{tenant_id}", "order": "updated_at.desc"}
    )
    if knowledge_base_id:
        scoped = [
            r for r in rows
            if str((r.get("data") or {}).get("knowledgeBaseId") or "") == knowledge_base_id
        ]
        if scoped:
            rows = scoped
        else:
            log.info(
                "tenant %s: knowledge base %s has no documents; answering from all of them",
                tenant_id, knowledge_base_id,
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
