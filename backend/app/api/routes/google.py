"""
Connecting a company's Google Drive.

The company clicks Connect, approves in their own Google account, and comes
back here with a code we exchange for a refresh token. From then on their call
audio is written to their Drive.

Disconnecting forgets the token. It does not delete the files: the recordings
are in the customer's own Drive and are theirs to keep.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from ...config import settings
from ...errors import AppError, NotFound, UpstreamError
from ...repositories import tenants as tenant_repo
from ...services import reports
from ...services.drive import DriveError, TenantDrive
from ...services.google_oauth import authorization_url, exchange_code, read_state
from ..deps import CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/google", tags=["google"])


@router.get("/status")
async def drive_status(tenant: CurrentTenant) -> dict:
    link = tenant.google_drive
    return {
        "connected": link.connected,
        "accountEmail": link.account_email or None,
        "folderName": link.folder_name or None,
        "folderId": link.folder_id or None,
        "sheetLink": link.sheet_link or None,
        "connectedAt": link.connected_at or None,
        "oauthConfigured": settings.google_oauth_configured,
    }


@router.post("/connect")
async def start_connect(tenant: CurrentTenant, returnTo: str = "") -> dict:
    """The URL to send the company to.

    Returned as JSON rather than a redirect because the caller is a dashboard
    doing fetch(); a 302 from fetch() lands in the page's own origin and the
    user never sees Google.
    """
    url = authorization_url(tenant.phone_number_id, return_to=returnTo)
    return {"url": url}


@router.get("/callback")
async def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> RedirectResponse:
    """Where Google sends the browser back.

    Unauthenticated by necessity — it is a browser redirect, not an API call —
    which is exactly why `state` is signed. Without that check anyone could
    attach their own Drive to a company they do not own.
    """
    body = read_state(state)
    phone_number_id = str(body.get("t") or "")
    return_to = str(body.get("r") or "") or settings.cors_origins[0]

    if error:
        log.info("company %s declined the Google connection: %s", phone_number_id, error)
        return RedirectResponse(f"{return_to}?drive=declined", status_code=303)

    if not code:
        raise AppError(400, "Google did not return an authorisation code.", code="no_code")

    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")

    tokens = await exchange_code(code)
    tenant.google_drive.refresh_token = tokens["refresh_token"]
    tenant.google_drive.connected_at = datetime.now(timezone.utc).isoformat()

    # Named now, so the dashboard can show which account is connected rather
    # than just "connected" — the usual confusion is a personal account where
    # the company's shared one was meant.
    drive = TenantDrive(tenant)
    tenant.google_drive.account_email = await drive.account_email()
    try:
        await drive.folder()  # creates and caches the folder id
    except (DriveError, UpstreamError) as exc:
        # The token is good; the folder can be made on the first upload.
        log.warning("could not prepare the Drive folder for %s: %s", phone_number_id, exc)

    await tenant_repo.save(tenant)
    log.info(
        "company %s connected Google Drive (%s)",
        phone_number_id, tenant.google_drive.account_email or "unknown account",
    )
    return RedirectResponse(f"{return_to}?drive=connected", status_code=303)


@router.post("/disconnect")
async def disconnect(tenant: CurrentTenant) -> dict:
    """Forget the company's Drive token.

    Their files stay where they are. Recordings written from here on fall back
    to object storage, which is the behaviour of a company that never connected
    one — not an error.
    """
    tenant.google_drive.refresh_token = ""
    tenant.google_drive.account_email = ""
    tenant.google_drive.folder_id = ""
    tenant.google_drive.sheet_id = ""
    tenant.google_drive.sheet_link = ""
    tenant.google_drive.connected_at = ""
    await tenant_repo.save(tenant)
    log.info("company %s disconnected Google Drive", tenant.phone_number_id)
    return {"connected": False}


@router.post("/sync-report")
async def sync_report(tenant: CurrentTenant) -> dict:
    """Bring the Sheet copy of the call records up to date, and return its link."""
    if not tenant.google_drive.connected:
        raise AppError(
            409, "This company has not connected Google Drive.", code="drive_not_connected"
        )
    try:
        url = await reports.sync_to_drive(tenant)
    except DriveError as exc:
        raise UpstreamError(f"Google Drive refused the upload: {exc.detail}") from exc
    return {"url": url}
