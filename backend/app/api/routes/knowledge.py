"""
The material a company's agent answers from.

Upload a document, see what is stored, remove one. The agent reads all of it
when it answers a message — see `services/agent/reply.py`.

Text is extracted here rather than stored raw, because what the model needs is
words. A PDF kept as bytes would have to be parsed on every message; parsed
once on upload, it costs nothing per answer.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status

from ...errors import AppError
from ...repositories import knowledge
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.get("")
async def list_documents(
    tenant: CurrentTenant, knowledgeBaseId: str | None = None
) -> dict:
    """Every document, or one knowledge base's.

    No `knowledgeBaseId` lists all of them; passing one narrows to that
    base, and passing an empty one lists the documents filed under no base
    at all — everything uploaded before knowledge bases existed.
    """
    documents = await knowledge.listing(tenant.phone_number_id, knowledgeBaseId)
    return {
        "documents": documents,
        "totalChars": sum(d["chars"] for d in documents),
        "limitChars": knowledge.MAX_CONTEXT_CHARS,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    tenant: CurrentTenant, request: Request, name: str = "",
    knowledgeBaseId: str = "",
) -> dict:
    """Store a document.

    Takes the file as the raw body, with `?name=` for what to call it. A PDF is
    read for its text; anything else is treated as text. The same name replaces
    rather than duplicates, so correcting a price list is re-uploading it.

    `knowledgeBaseId` files it under one base, so a number pointed at that
    base answers from it and other numbers do not. Left out, it joins the
    company's general pile, which every agent can read.
    """
    raw = await request.body()
    if not raw:
        raise AppError(400, "The upload was empty.", code="empty_body")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise AppError(413, "That file is too large.", code="too_large")

    content_type = (request.headers.get("content-type") or "").lower()
    label = (name or "document").strip()[:120]

    if "pdf" in content_type or raw[:5] == b"%PDF-":
        text = _pdf_text(raw)
        if not text.strip():
            raise AppError(
                422,
                "No text could be read from that PDF. A scanned document needs "
                "OCR before it can be used.",
                code="no_text",
            )
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")

    if knowledgeBaseId:
        from ...repositories import agents as agent_repo

        if await agent_repo.get_knowledge_base(
            tenant.phone_number_id, knowledgeBaseId
        ) is None:
            raise AppError(422, "That knowledge base does not exist.", code="no_such_kb")

    stored = await knowledge.save(
        tenant.phone_number_id, label, text, knowledge_base_id=knowledgeBaseId
    )
    return stored


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
async def remove(tenant: CurrentTenant, document_id: str) -> None:
    await knowledge.delete(tenant.phone_number_id, document_id)


@router.get("/preview")
async def preview(tenant: CurrentTenant) -> dict:
    """Exactly what the agent will be given. Worth seeing before trusting it."""
    context = await knowledge.context_for(tenant.phone_number_id)
    return {
        "chars": len(context),
        "limitChars": knowledge.MAX_CONTEXT_CHARS,
        "preview": context[:2000],
        "truncated": len(context) > 2000,
    }


def _pdf_text(raw: bytes) -> str:
    import io

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AppError(
            503, "PDF support is not installed on the server.", code="no_pdf"
        ) from exc

    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — one unreadable page is not a failed upload
            continue
    return "\n\n".join(pages)
