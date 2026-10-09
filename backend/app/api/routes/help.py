"""
The assistant on the Guides page.

Takes a question and, optionally, something to look at — a screenshot of an
error, a PDF of a provider's settings page. Asking somebody to transcribe an
error message before you will look at it is a poor way to help.

Multipart rather than JSON, because the attachment is a file and base64 in a
JSON body would inflate an 8 MB screenshot to 11 MB for no benefit.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ...security.rate_limit import check_expensive
from ...services import help as help_service
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/help", tags=["help"])


@router.post("", dependencies=[Depends(check_expensive)])
async def ask_for_help(
    tenant: CurrentTenant,
    question: str = Form(default=""),
    file: UploadFile | None = File(default=None),
) -> dict:
    """Answer a question about the product, grounded in its documentation.

    Deliberately not given the company's own knowledge base: this answers
    "where is that setting", not "what are our fees". Mixing the two would
    have it quoting a company's price list at somebody asking how to connect
    a number.
    """
    attachment = await file.read() if file is not None else None
    if file is not None:
        log.info("tenant %s asked for help about a %s (%d KB)",
                 tenant.phone_number_id, file.content_type, len(attachment or b"") // 1024)
    return await help_service.answer(
        question,
        attachment=attachment,
        attachment_type=(file.content_type or "") if file else "",
        attachment_name=(file.filename or "") if file else "",
    )


@router.get("/available")
async def is_available(tenant: CurrentTenant) -> dict:
    return {"available": help_service.available()}
