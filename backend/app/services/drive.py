"""
Google Drive, one connection per customer.

Each company connects its own Drive by OAuth, so its call audio is written into
its own account, under its own quota, and revoking our access in their Google
security settings is enough to cut us off completely. That is the difference
from the single-tenant services this is ported from, where one refresh token in
the environment owned every recording.

Scope is `drive.file`: the backend can create, read and delete *its own* files
and cannot see anything else in that Drive. Uploads are resumable regardless of
size — one code path, and a long call's audio is not capped by the 5 MB
multipart limit.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx

from ..config import settings
from ..models.tenant import Tenant

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Drive wants `drive.file` for its own files; userinfo.email is only so the
# dashboard can say *which* account is connected.
SCOPES = (
    "https://www.googleapis.com/auth/drive.file "
    "https://www.googleapis.com/auth/userinfo.email"
)


class DriveError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Google Drive {status}: {detail[:300]}")
        self.status = status
        self.detail = detail[:300]


def quote(value: str) -> str:
    """Escape a value for a Drive search query string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class _AccessTokens:
    """Short-lived access tokens, cached per refresh token.

    Refreshing costs a round trip to Google on a path that runs while a call is
    ending, and the token is valid for an hour. Keyed by refresh token rather
    than by tenant so that a tenant reconnecting a different Google account
    cannot pick up the previous account's token.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def forget(self, refresh_token: str) -> None:
        self._tokens.pop(refresh_token, None)

    async def get(self, client: httpx.AsyncClient, refresh_token: str) -> str:
        cached = self._tokens.get(refresh_token)
        if cached and time.time() < cached[1] - 60:
            return cached[0]
        async with self._lock(refresh_token):
            cached = self._tokens.get(refresh_token)
            if cached and time.time() < cached[1] - 60:
                return cached[0]
            response = await client.post(
                TOKEN_URL,
                data={
                    "client_id": settings.google_client_id.strip(),
                    "client_secret": settings.google_client_secret.strip(),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if response.status_code >= 400:
                # invalid_grant means the customer revoked access or the token
                # expired; only reconnecting in the app fixes it, so say so.
                raise DriveError(response.status_code, response.text)
            body = response.json()
            token = body["access_token"]
            self._tokens[refresh_token] = (
                token,
                time.time() + int(body.get("expires_in", 3600)),
            )
            return token


_tokens = _AccessTokens()
_client: httpx.AsyncClient | None = None
# One lock per tenant: two recordings finishing together must not each create
# a folder and leave the customer with two.
_folder_locks: dict[str, asyncio.Lock] = {}


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=120)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class TenantDrive:
    """The customer's Drive folder: call audio and the report copy."""

    def __init__(self, tenant: Tenant) -> None:
        self.tenant = tenant
        self.refresh_token = tenant.google_drive.refresh_token.strip()

    @property
    def configured(self) -> bool:
        return bool(self.refresh_token and settings.google_oauth_configured)

    async def _auth(self) -> dict[str, str]:
        token = await _tokens.get(_http(), self.refresh_token)
        return {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        extra = kw.pop("headers", {})
        response = await _http().request(
            method, url, headers={**extra, **await self._auth()}, **kw
        )
        if response.status_code == 401:
            # Revoked or rotated before its stated expiry: refresh once, retry once.
            _tokens.forget(self.refresh_token)
            response = await _http().request(
                method, url, headers={**extra, **await self._auth()}, **kw
            )
        if response.status_code >= 400:
            raise DriveError(response.status_code, response.text)
        return response

    async def find(self, name: str, mime: str, parent: str | None = None) -> str | None:
        query = f"name = '{quote(name)}' and mimeType = '{mime}' and trashed = false"
        if parent:
            query += f" and '{quote(parent)}' in parents"
        response = await self._request(
            "GET",
            f"{API}/files",
            params={"q": query, "fields": "files(id)", "pageSize": 1, "spaces": "drive"},
        )
        files = response.json().get("files") or []
        return files[0]["id"] if files else None

    async def folder(self) -> str:
        """The customer's folder id, creating it the first time."""
        cached = self.tenant.google_drive.folder_id.strip()
        if cached:
            return cached

        key = self.tenant.phone_number_id
        lock = _folder_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self.tenant.google_drive.folder_id.strip()
            if cached:
                return cached
            name = (
                self.tenant.google_drive.folder_name.strip()
                or f"{settings.google_drive_folder_name} — {self.tenant.name}".strip()
            )
            found = await self.find(name, FOLDER_MIME)
            if not found:
                created = await self._request(
                    "POST",
                    f"{API}/files",
                    params={"fields": "id"},
                    json={"name": name, "mimeType": FOLDER_MIME},
                )
                found = created.json()["id"]
                log.info("created Drive folder %r for tenant %s", name, key)
            # Remembered on the tenant so the next upload skips the lookup;
            # persisted by the caller, which owns the row.
            self.tenant.google_drive.folder_id = found
            self.tenant.google_drive.folder_name = name
            return found

    async def upload(
        self,
        name: str,
        data: bytes,
        mime: str,
        *,
        file_id: str | None = None,
        convert_to: str | None = None,
    ) -> dict[str, Any]:
        """Create a file in the folder, or replace an existing file's content."""
        metadata: dict[str, Any] = {"name": name}
        if file_id:
            method, url = "PATCH", f"{UPLOAD}/files/{file_id}"
        else:
            method, url = "POST", f"{UPLOAD}/files"
            metadata["parents"] = [await self.folder()]
            if convert_to:
                metadata["mimeType"] = convert_to
        start = await self._request(
            method,
            url,
            params={"uploadType": "resumable", "fields": "id,webViewLink"},
            json=metadata,
            headers={"X-Upload-Content-Type": mime},
        )
        session = start.headers.get("Location")
        if not session:
            raise DriveError(500, "no resumable upload session was returned")
        response = await _http().put(
            session, content=data, headers={"Content-Type": mime, **await self._auth()}
        )
        if response.status_code >= 400:
            raise DriveError(response.status_code, response.text)
        return response.json()

    async def download(self, file_id: str) -> bytes | None:
        try:
            response = await self._request(
                "GET", f"{API}/files/{file_id}", params={"alt": "media"}
            )
        except (DriveError, httpx.HTTPError) as exc:
            log.warning("Drive download of %s failed: %s", file_id, exc)
            return None
        return response.content or None

    async def delete(self, file_id: str) -> None:
        """Best effort: a deleted call must still delete."""
        with contextlib.suppress(DriveError, httpx.HTTPError):
            await self._request("DELETE", f"{API}/files/{file_id}")

    async def account_email(self) -> str:
        with contextlib.suppress(DriveError, httpx.HTTPError, KeyError, ValueError):
            response = await self._request("GET", USERINFO_URL)
            return str(response.json().get("email") or "")
        return ""


def web_link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"
