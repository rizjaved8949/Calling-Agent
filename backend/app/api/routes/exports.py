"""
The call-record workbook.

One file, every call the company has taken. This is what the dashboard's
Download Excel button asks for.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Response

from ...config import settings
from ...http_headers import content_disposition
from ...services import reports
from ...services.drive import XLSX_MIME
from ..deps import CurrentTenant

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/calls.xlsx")
async def export_calls(
    tenant: CurrentTenant, limit: int = Query(5000, ge=1, le=20000)
) -> Response:
    workbook = await reports.workbook_for(tenant, limit=limit)
    # The company's name in the filename, so a folder of these from several
    # customers is still navigable.
    stem = (tenant.name or "calls").strip() or "calls"
    name = f"{stem} - {settings.excel_filename}"
    return Response(
        content=workbook,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": content_disposition(name)},
    )
