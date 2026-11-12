"""
The requests callers made, for the team to work through.

Read and updated here; written only by the agent, during a call. There is no
screen for this yet: the call summary, the Excel download and the Google
Sheet already show every request, and this is for whatever comes next.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Query

from ...errors import NotFound
from ...models.request import RequestStatus, RequestType, RequestUpdate
from ...repositories import requests as request_repo
from ..deps import CurrentTenant

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("")
async def list_requests(
    tenant: CurrentTenant,
    status: RequestStatus | None = None,
    kind: RequestType | None = Query(None, alias="type"),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    found = await request_repo.list_for(tenant.phone_number_id, limit=limit,
                                        status=status, kind=kind)
    return {"requests": [r.public() for r in found], "total": len(found)}


@router.patch("/{request_id}")
async def update_request(tenant: CurrentTenant, request_id: str, payload: RequestUpdate) -> dict:
    record = await request_repo.get(tenant.phone_number_id, request_id)
    if record is None:
        raise NotFound("Request")
    if payload.status is not None:
        record.status = payload.status
    if payload.note is not None:
        record.note = payload.note.strip()
    record.updated_at = time.time()
    await request_repo.save(record)
    return record.public()
