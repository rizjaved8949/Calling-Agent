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

import asyncio
import hashlib
import logging
import re
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from ..db.supabase import supabase

log = logging.getLogger(__name__)

TABLE = "voice_knowledge"

# Roughly what one long-context request should carry. Gemini allows far more,
# but every character is paid for on every message, and a handbook that does
# not fit is a sign the company wants retrieval rather than a bigger prompt.
# The default for `KNOWLEDGE_MAX_CHARS`; `max_chars()` is what is in force.
MAX_CONTEXT_CHARS = 120_000


def max_chars() -> int:
    """How much knowledge text one call may carry, from `KNOWLEDGE_MAX_CHARS`.

    A setting rather than the constant above, because the right number
    depends on the model's window and on how much of it the conversation
    needs: raise it only after `scripts/knowledge_size.py` has counted the
    tokens of the instructions plus the material.
    """
    return max(1_000, int(settings.knowledge_max_chars or MAX_CONTEXT_CHARS))


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
    # Tables that lost cells on the way in (a PDF's merged cells) are the
    # commonest reason an agent states a wrong figure with confidence: the
    # values shift left and "SEAL Premium: 321 kW" reads as fact. Noted on
    # the document, where the listing shows it, and in the log.
    warnings = table_warnings(text)
    if warnings:
        document["tableWarnings"] = warnings
        log.warning(
            "tenant %s: %r has %d table row(s) with fewer cells than their header "
            "(first: %s); figures in those rows may be misread",
            tenant_id, name, warnings["count"], ", ".join(warnings["rows"][:3]),
        )
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
                "tableWarnings": data.get("tableWarnings") or None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Tables that lost cells
# ---------------------------------------------------------------------------

_SEPARATOR_ROW = re.compile(r"^[\s|:\-]+$")


def table_warnings(text: str) -> dict[str, Any] | None:
    """Rows of a pipe-separated table with fewer cells than their header.

    A PDF's merged cells are dropped by text extraction, so a row that read
    `Max power | 130 kW | 150 kW | 230 kW | 321 kW` under six model columns
    has its values shifted left, and the agent reads the last one as the
    last model's. Cheap to spot: count the cells. Returns `None` for a text
    whose tables are whole, else how many rows are short and the labels of
    the first few, which is what the listing and the log show.
    """
    short: list[str] = []
    header_cells = 0
    in_table = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if "|" not in line or len(line) < 3:
            in_table = False
            header_cells = 0
            continue
        if _SEPARATOR_ROW.match(line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            in_table = False
            continue
        if not in_table:
            in_table = True
            header_cells = len(cells)
            continue
        if len(cells) < header_cells:
            short.append(cells[0] or f"row with {len(cells)} cells")
    if not short:
        return None
    return {"count": len(short), "rows": short[:5]}


async def delete(tenant_id: str, document_id: str) -> None:
    # Scoped by tenant as well as id: an id alone would let one company delete
    # another's material by guessing.
    await supabase.delete(
        TABLE, params={"id": f"eq.{document_id}", "tenant_id": f"eq.{tenant_id}"}
    )
    forget_context(tenant_id)


# The prompt text for one knowledge base, kept between calls. Reading and
# joining tens of thousands of characters took 1.7 seconds of a call's opening
# silence on a warm server, and far longer on the first call after a restart.
#
# Every document write goes through `save` and `delete` here, and both drop
# the company's entries, so what is cached is never out of date because of an
# upload. It is still re-read in the background once it is older than
# `_CONTEXT_TTL_SECONDS`, in case the table was changed some other way, but a
# call is never made to wait for that: it gets the copy in hand and the next
# call gets the fresh one. Only a company's very first call after a restart or
# an upload reads while the caller waits, and `warm` covers the restart.
#
# Bounded by `settings.document_cache_mb`, least recently used first out, so
# the cache cannot take the server's memory as more companies upload more
# documents. A company dropped from it is read again on its next call.
_CONTEXT_TTL_SECONDS = 120.0
#: (tenant, knowledge base, limit, spoken amounts) -> (read at, text)
_Key = tuple[str, str, int, bool]
_context_cache: OrderedDict[_Key, tuple[float, str]] = OrderedDict()
_cache_bytes = 0
#: How many entries have been dropped to make room, ever.
_evictions = 0
_refreshing: dict[_Key, "asyncio.Task[None]"] = {}


def _size(text: str) -> int:
    """What a cached text costs in memory: Urdu text is stored at up to four
    bytes a character, so this is not its length."""
    return sys.getsizeof(text)


def _cache_limit() -> int:
    return max(0, settings.document_cache_mb) * 1024 * 1024


def cached_bytes() -> int:
    """How much memory the cached document text is using."""
    return _cache_bytes


def cache_full() -> bool:
    return _cache_bytes >= _cache_limit()


def _drop(key: _Key) -> None:
    global _cache_bytes
    entry = _context_cache.pop(key, None)
    if entry is not None:
        _cache_bytes -= _size(entry[1])


def _store(key: _Key, text: str) -> None:
    """Keep `text`, making room by dropping what was used least recently."""
    global _cache_bytes, _evictions
    _drop(key)
    size = _size(text)
    if size > _cache_limit():
        return                     # bigger than the whole cache: never kept
    while _context_cache and _cache_bytes + size > _cache_limit():
        oldest = next(iter(_context_cache))
        _drop(oldest)
        _evictions += 1
        log.info("document cache full: dropped tenant %s's documents until "
                 "their next call", oldest[0])
    _context_cache[key] = (time.monotonic(), text)
    _cache_bytes += size


def forget_context(tenant_id: str = "") -> None:
    """Drop cached prompt text after documents change."""
    global _cache_bytes
    if not tenant_id:
        _context_cache.clear()
        _cache_bytes = 0
        return
    for key in [k for k in _context_cache if k[0] == tenant_id]:
        _drop(key)


async def context_for(
    tenant_id: str,
    limit: int | None = None,
    knowledge_base_id: str = "",
    *,
    spoken_amounts: bool = False,
) -> str:
    """The prompt text for one company's knowledge base, cached.

    `spoken_amounts` writes the Roman Urdu reading after every rupee amount
    (`services/agent/amounts.py`), for an agent that speaks Urdu or a company
    in Pakistan: the model turns "8,990,000" into the wrong lakh otherwise.
    It is part of the cache key, so a company with an English agent and an
    Urdu one keeps one copy of each. The stored documents are never changed.
    """
    limit = limit or max_chars()
    key = (tenant_id, knowledge_base_id, limit, spoken_amounts)
    cached = _context_cache.get(key)
    if cached:
        _context_cache.move_to_end(key)          # just used
        if time.monotonic() - cached[0] >= _CONTEXT_TTL_SECONDS:
            _refresh_later(key)
        return cached[1]
    text = await _read_context(tenant_id, limit, knowledge_base_id)
    text = _spoken(text, spoken_amounts)
    _store(key, text)
    return text


def _spoken(text: str, spoken_amounts: bool) -> str:
    if not spoken_amounts or not text:
        return text
    from ..services.agent import amounts

    return amounts.annotate(text)


def _refresh_later(key: _Key) -> None:
    """Re-read one cached entry in the background, once at a time."""
    if key in _refreshing:
        return

    async def refresh() -> None:
        try:
            replacing = _context_cache.get(key)
            text = _spoken(await _read_context(key[0], key[2], key[1]), key[3])
            # Only over the very copy this set out to replace. Not if an
            # upload dropped it meanwhile (the next call reads it fresh, with
            # the new document in it), nor over a newer read.
            if replacing is not None and _context_cache.get(key) is replacing:
                _store(key, text)
        except Exception:  # noqa: BLE001 — the copy in hand still serves
            log.exception("tenant %s: could not refresh the knowledge text", key[0])
        finally:
            _refreshing.pop(key, None)

    _refreshing[key] = asyncio.create_task(refresh())


async def warm(tenant_id: str, knowledge_base_ids: list[str], *,
               spoken_amounts: bool = False) -> bool:
    """Read a company's knowledge text before its first call needs it.

    Returns False, having stopped, once the cache is full, that is once
    loading one more means dropping another: preloading past that would
    only push out what was loaded a moment ago.
    """
    for knowledge_base_id in ["", *knowledge_base_ids]:
        if cache_full():
            return False
        before = _evictions
        await context_for(tenant_id, knowledge_base_id=knowledge_base_id,
                          spoken_amounts=spoken_amounts)
        if _evictions != before:
            return False
    return True


@dataclass
class Assembled:
    """What one knowledge base comes to, and whether all of it fits."""

    text: str
    #: Characters of every document, before any was cut.
    chars: int
    cut: bool = False
    #: The first heading (or line) that did not reach the agent.
    cut_at: str = ""
    documents: list[dict[str, Any]] = field(default_factory=list)


_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|\d+(?:\.\d+)*\s+)(\S.*)$")


def first_heading(text: str) -> str:
    """The first markdown or numbered heading in `text`, else its first line."""
    first = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        first = first or line
        if _HEADING.match(line):
            return line[:120]
    return first[:120]


def assemble(rows: list[dict[str, Any]], limit: int) -> Assembled:
    """Join documents into one block, newest first, within `limit` characters.

    Truncated at a line boundary rather than mid-sentence: a document cut in
    half mid-fact invites the model to finish the sentence itself, which
    reads exactly like the rest of the answer. Says what was cut, so the
    warning can name the heading the agent will never see.
    """
    parts: list[str] = []
    used = 0
    total = 0
    cut = False
    cut_at = ""
    documents: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data") or {}
        stored = str(data.get("text") or "")
        text = stored.strip()
        if not text:
            continue
        # Counted as stored, which is the figure the listing shows.
        total += len(stored)
        name = str(data.get("name") or "document")
        entry = {"name": name, "chars": len(stored), "included": 0,
                 "knowledgeBaseId": str(data.get("knowledgeBaseId") or ""),
                 "tableWarnings": data.get("tableWarnings") or None}
        documents.append(entry)
        if cut:
            cut_at = cut_at or first_heading(text)
            continue
        header = f"--- {name} ---\n"
        room = limit - used - len(header)
        if room <= 200:
            cut = True
            cut_at = cut_at or first_heading(text)
            continue
        if len(text) > room:
            at = text.rfind("\n", 0, room)
            at = at if at > room // 2 else room
            cut = True
            cut_at = cut_at or first_heading(text[at:])
            text = text[:at] + "\n[…truncated]"
        entry["included"] = len(text)
        parts.append(header + text)
        used += len(header) + len(text)
    return Assembled(text="\n\n".join(parts), chars=total, cut=cut, cut_at=cut_at,
                     documents=documents)


async def _rows_for(tenant_id: str, knowledge_base_id: str = "") -> list[dict[str, Any]]:
    """A company's documents, newest first; one base's when asked.

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
    return rows


async def report(tenant_id: str, knowledge_base_id: str = "",
                 limit: int | None = None) -> Assembled:
    """Exactly what the agent would be given, with what did not fit. Not cached."""
    return assemble(await _rows_for(tenant_id, knowledge_base_id), limit or max_chars())


async def _read_context(
    tenant_id: str,
    limit: int | None = None,
    knowledge_base_id: str = "",
) -> str:
    """Everything the agent may quote, as one block for the prompt.

    With a `knowledge_base_id`, only that base's documents — this is what
    makes one number answer from the price list and another from the support
    handbook. Without one, everything the company has, which is both the
    old behaviour and the right fallback for a company that never split its
    documents up.

    Logs a warning when the material does not fit: the agent then answers
    without the part that was cut, which is usually the end of the file,
    where a handbook keeps its conduct rules.
    """
    limit = limit or max_chars()
    built = assemble(await _rows_for(tenant_id, knowledge_base_id), limit)
    if built.cut:
        log.warning(
            "tenant %s: knowledge base %r is %d characters, over the %d limit "
            "(KNOWLEDGE_MAX_CHARS); the agent will not see %r onwards",
            tenant_id, knowledge_base_id or "all documents", built.chars, limit,
            built.cut_at,
        )
    return built.text


def _timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
