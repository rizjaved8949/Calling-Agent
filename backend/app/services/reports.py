"""
The master call-record workbook, and its copy in the company's Drive.

One workbook per company, every call in it — never a file per call, and never
only the calls currently on screen. It is what the Download Excel button
produces and what the Google Sheet copy is made from, so the two can never
disagree about what happened.

Written with openpyxl: pure Python, a few hundred KB, and it needs neither
pandas nor Excel on the host for a job that is three sheets of cells.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import httpx

from ..config import settings
from ..models.call import Call, RecordingState
from ..models.tenant import Tenant
from ..repositories import calls as call_repo
from ..repositories import tenants as tenant_repo
from .drive import SHEET_MIME, XLSX_MIME, DriveError, TenantDrive, web_link
from .storage import DRIVE_PREFIX

log = logging.getLogger(__name__)

CALL_HEADERS = (
    "Call ID", "Date", "Time", "Direction", "Channel", "Number", "Status",
    "Duration", "Answered", "Handled by", "Recording", "Topic", "Summary", "Error",
)


def _stamp(epoch: float | None) -> tuple[str, str]:
    """A date and a time, in the company's eventual reading order.

    Split into two columns because a single timestamp cell sorts as text in
    half the spreadsheet software people actually open these in.
    """
    if not epoch:
        return "", ""
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M:%S")


def _clock(seconds: int) -> str:
    """Seconds as m:ss. A duration in raw seconds is not a length anyone reads."""
    if not seconds:
        return ""
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}:{rest:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{rest:02d}"


def _yes_no(value: Any) -> str:
    return "Yes" if value else "No"


def _recording_reference(call: Call) -> str:
    """What the report shows for stored audio.

    A Drive link rather than `gd://<id>`: the point of the column is that
    someone can click it.
    """
    path = call.recording_path
    if path.startswith(DRIVE_PREFIX):
        return web_link(path[len(DRIVE_PREFIX):])
    if call.recording_state == RecordingState.READY:
        return "Stored"
    if call.recording_state == RecordingState.FAILED:
        return call.recording_error or "Failed"
    if call.recording_state == RecordingState.PENDING:
        return "Pending"
    return ""


def _cell(value: Any, limit: int = 32_000) -> str:
    """Excel refuses a cell over 32,767 characters, and a transcript can exceed it."""
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_workbook(tenant: Tenant, calls: list[Call]) -> bytes:
    """The workbook itself. Synchronous and CPU-bound — call it in a thread."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()

    # --- Sheet 1: every call -------------------------------------------------
    sheet = book.active
    sheet.title = "Calls"
    sheet.append(list(CALL_HEADERS))
    for call in calls:
        date, clock = _stamp(call.started_at)
        sheet.append([
            _cell(call.id),
            date,
            clock,
            call.direction.value.title(),
            call.channel.value.replace("_", " ").title(),
            _cell(call.counterparty),
            call.status.value.replace("_", " ").title(),
            _clock(call.duration_seconds),
            _yes_no(call.answered_at),
            _cell(call.handled_by),
            _cell(_recording_reference(call)),
            _cell(call.topic),
            _cell(call.summary),
            _cell(call.error),
        ])

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0F2A3F")
    for column, title in enumerate(CALL_HEADERS, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")
        # Sized from the header plus a sample of the data: measuring every row
        # of a year of calls to set a column width is not worth the pass.
        width = max(len(title) + 4, 12)
        if title in {"Summary", "Topic"}:
            width = 48
        elif title in {"Recording", "Call ID"}:
            width = 38
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    # --- Sheet 2: transcripts -----------------------------------------------
    # Separate because a transcript is paragraphs, and a column wide enough for
    # one makes every other column on the calls sheet unreadable.
    transcripts = book.create_sheet("Transcripts")
    transcripts.append(["Call ID", "Date", "Number", "Transcript"])
    for call in calls:
        if not call.transcript:
            continue
        date, _ = _stamp(call.started_at)
        transcripts.append(
            [_cell(call.id), date, _cell(call.counterparty), _cell(call.transcript)]
        )
    for column, width in enumerate((38, 12, 18, 120), start=1):
        transcripts.column_dimensions[get_column_letter(column)].width = width
    for cell in transcripts["D"]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    transcripts.freeze_panes = "A2"

    # --- Sheet 3: the summary the company actually looks at ------------------
    overview = book.create_sheet("Overview", 0)
    answered = [c for c in calls if c.answered_at]
    stored = [c for c in calls if c.recording_state == RecordingState.READY]
    rows = [
        ("Company", tenant.name or tenant.phone_number_id),
        ("WhatsApp number", tenant.display_phone_number or "—"),
        ("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
        ("", ""),
        ("Calls", len(calls)),
        ("Answered", len(answered)),
        ("Recorded", len(stored)),
        (
            "Average length",
            _clock(round(sum(c.duration_seconds for c in answered) / len(answered)))
            if answered
            else "—",
        ),
        (
            "Total talk time",
            _clock(sum(c.duration_seconds for c in calls)),
        ),
    ]
    for label, value in rows:
        overview.append([label, value])
    for cell in overview["A"]:
        cell.font = Font(bold=True)
    overview.column_dimensions["A"].width = 22
    overview.column_dimensions["B"].width = 38

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def workbook_for(tenant: Tenant, *, limit: int = 5000) -> bytes:
    calls = await call_repo.list_calls(tenant.phone_number_id, limit=limit)
    return await asyncio.to_thread(build_workbook, tenant, calls)


# ---------------------------------------------------------------------------
# The Google Sheet copy
# ---------------------------------------------------------------------------

_sync_tasks: dict[str, asyncio.Task] = {}
_dirty: set[str] = set()


async def sync_to_drive(tenant: Tenant) -> str:
    """Replace the Sheet copy of the workbook, and return its link.

    The same workbook the Download button produces, uploaded so Drive converts
    it. Updating the one file in place keeps its link stable for anyone who
    bookmarked it or had it shared with them.
    """
    drive = TenantDrive(tenant)
    if not drive.configured:
        raise DriveError(409, "this company has not connected Google Drive")

    workbook = await workbook_for(tenant)
    name = f"{tenant.name or 'Calls'} — Call Records".strip()

    sheet_id = tenant.google_drive.sheet_id.strip() or None
    if not sheet_id:
        sheet_id = await drive.find(name, SHEET_MIME, await drive.folder())

    try:
        uploaded = await drive.upload(
            name, workbook, XLSX_MIME, file_id=sheet_id, convert_to=SHEET_MIME
        )
    except DriveError as exc:
        if exc.status != 404 or not sheet_id:
            raise
        # Deleted in Drive by hand since we last looked: make it again.
        uploaded = await drive.upload(name, workbook, XLSX_MIME, convert_to=SHEET_MIME)

    link = (
        uploaded.get("webViewLink")
        or f"https://docs.google.com/spreadsheets/d/{uploaded['id']}/edit"
    )
    tenant.google_drive.sheet_id = uploaded["id"]
    tenant.google_drive.sheet_link = link
    await tenant_repo.save(tenant)
    return link


async def _sync_later(phone_number_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    while phone_number_id in _dirty:
        _dirty.discard(phone_number_id)
        tenant = await tenant_repo.get(phone_number_id)
        if tenant is None:
            return
        try:
            await sync_to_drive(tenant)
            log.info("Sheet copy of the report updated for %s", phone_number_id)
        except (DriveError, httpx.HTTPError) as exc:
            # The download button still works; this copy is a convenience.
            log.warning("could not update the Sheet copy for %s: %s", phone_number_id, exc)
            return
    _sync_tasks.pop(phone_number_id, None)


def schedule_sync(tenant: Tenant, delay: float | None = None) -> None:
    """Refresh the Drive copy soon.

    Calls ending together share one upload: the flag is set now and the
    debounce window swallows the rest, so twenty calls in a minute do not mean
    twenty workbook rebuilds.
    """
    if not settings.google_sheet_sync or not tenant.google_drive.connected:
        return
    key = tenant.phone_number_id
    _dirty.add(key)
    existing = _sync_tasks.get(key)
    if existing and not existing.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    wait = settings.google_sheet_sync_delay_seconds if delay is None else delay
    _sync_tasks[key] = loop.create_task(_sync_later(key, wait))
