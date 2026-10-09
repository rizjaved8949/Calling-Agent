"""
SIM / SIP calling through Infobip, scoped to one tenant.

What is here is the control plane: place a call, hang one up, and fetch the
recording the carrier made. The media plane — the SIP leg carrying audio to a
realtime model — is deliberately not in this build; see the README.

The recording parsing is the part worth reading. Infobip's shape is nested, not
what the field names suggest, and sometimes wrapped, and reading it wrongly is
how a dashboard ends up playing one side of a conversation with silence where
the other person spoke.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any

import httpx

from ..config import settings
from ..errors import AppError, UpstreamError
from ..models.tenant import Tenant
from .agent.audio_rate import PHONE_RATE

log = logging.getLogger(__name__)


def split_recording_files(payload: Any) -> tuple[list[dict], list[dict]]:
    """Pull the audio files out of a dialog-recording response.

    Shapes seen in practice::

        { dialogId, composedFiles: [...], callRecordings: [ { files: [...] } ] }
        { results: [ { …the above… } ] }

    A dialog records each leg separately, so `callRecordings` normally holds
    two entries — the caller and the agent. `composedFiles` is the mixed-down
    single file, and only exists once a composition has been requested.

    Returns (composed, per_leg) rather than one merged list: the caller has to
    know which it got, because handing back a single leg means a recording with
    one voice and silence where the other person spoke. Reading the wrapped
    shape here is what makes composition get requested at all — checking
    ``payload["composedFiles"]`` directly silently misses it on ``results``
    responses and the download falls through to a half-empty leg file.
    """
    if isinstance(payload, dict) and "results" in payload:
        entries = payload.get("results") or []
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = [payload]

    composed: list[dict] = []
    per_leg: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        composed.extend(entry.get("composedFiles") or [])
        for rec in entry.get("callRecordings") or []:
            per_leg.extend(rec.get("files") or [])
        # The per-call endpoint puts its files at the top level instead.
        per_leg.extend(entry.get("files") or [])
    return composed, per_leg


def recording_files(payload: Any) -> list[dict]:
    """The best available files: the composed mix, or the legs if there is none."""
    composed, per_leg = split_recording_files(payload)
    return composed or per_leg


class Infobip:
    """Calls API for one tenant."""

    def __init__(self, tenant: Tenant) -> None:
        self.tenant = tenant
        # A customer's calls go out on *their* carrier account, never the
        # platform's. Falling back to the environment here would put their
        # minutes on our bill and their caller ID on our number — so a company
        # that has not entered its own key simply cannot place a call, and is
        # told so. The env values are the platform owner's own account and are
        # reached only through the legacy single-tenant row.
        self.api_key = tenant.infobip_api_key.strip()
        base = tenant.infobip_base_url.strip().rstrip("/")
        # Infobip's portal shows the base URL without a scheme.
        if base and not base.startswith("http"):
            base = "https://" + base
        self.base_url = base

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _require(self) -> None:
        if not self.configured:
            raise AppError(
                409,
                "This company has no SIM calling credentials. Add the Infobip API "
                "key and base URL first.",
                code="telephony_not_configured",
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"App {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        self._require()
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
                response = await client.request(method, url, headers=self._headers, **kw)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"The carrier did not respond: {exc}") from exc
        if response.status_code >= 400:
            log.warning("infobip %s %s -> %s: %s", method, path, response.status_code,
                        response.text[:300])
            raise UpstreamError(
                f"The carrier refused the request ({response.status_code}).",
                details=response.text[:300],
            )
        return response

    async def place_call(self, to: str, *, from_number: str = "") -> dict[str, Any]:
        # Credentials before caller ID: a company that has connected nothing
        # should be told that, not sent to look for a missing phone number.
        self._require()
        caller = (from_number or self.tenant.infobip_phone_number).strip()
        if not caller:
            raise AppError(
                409, "This company has no outbound number configured.", code="no_caller_id"
            )
        payload: dict[str, Any] = {
            "endpoint": {"type": "PHONE", "phoneNumber": to},
            "from": caller,
        }
        # Omitted rather than sent as null: Infobip rejects an explicit null
        # here, and a tenant without its own configuration uses the account
        # default.
        configuration = self.tenant.infobip_calls_configuration_id.strip()
        if configuration:
            payload["callsConfigurationId"] = configuration
        response = await self._request("POST", "/calls/1/calls", json=payload)
        return response.json() or {}

    async def hangup(self, provider_call_id: str) -> None:
        await self._request("POST", f"/calls/1/calls/{provider_call_id}/hangup", json={})

    async def answer(self, provider_call_id: str) -> None:
        """Pick up an inbound call without bridging anything to it.

        Rarely wanted: `bridge_to_websocket` answers the call as a side effect
        of the agent's leg connecting, which is the arrangement that actually
        carries audio. This is here for hanging up politely after answering.
        """
        await self._request("POST", f"/calls/1/calls/{provider_call_id}/answer", json={})

    # ---- Where the carrier sends this service's audio --------------------

    async def media_stream_configs(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/calls/1/media-stream-configs")
        body = response.json() or {}
        return body.get("results") or [] if isinstance(body, dict) else []

    async def websocket_endpoint_configs(self) -> list[dict[str, Any]]:
        """The WEBSOCKET_ENDPOINT configs on this account.

        They sit under media-stream-configs beside the recording ones, and the
        type is the whole difference: WEBSOCKET_ENDPOINT is a leg the carrier
        *dials into* and talks both ways over, while MEDIA_STREAMING is a
        one-way copy of the audio. The agent needs the first.
        """
        return [
            c for c in await self.media_stream_configs()
            if c.get("type") == "WEBSOCKET_ENDPOINT"
        ]

    async def create_websocket_endpoint_config(self, name: str, url: str) -> dict[str, Any]:
        """Register the socket the carrier should dial for call audio.

        Two things the API will not forgive, both learned the hard way:
        `type` is required and has no default, and `sampleRate` must be a
        *string* — the number is rejected outright. A rejection here reads as
        "Required request body is missing or not valid", which names no field
        and sounds like a malformed request rather than a missing one.
        """
        response = await self._request(
            "POST",
            "/calls/1/media-stream-configs",
            json={
                "type": "WEBSOCKET_ENDPOINT",
                "name": name,
                "url": url,
                "sampleRate": str(PHONE_RATE),
            },
        )
        return response.json() or {}

    async def delete_media_stream_config(self, config_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._request("DELETE", f"/calls/1/media-stream-configs/{config_id}")

    async def bridge_to_websocket(
        self,
        parent_call_id: str,
        *,
        websocket_config_id: str,
        from_number: str = "",
        record: bool = False,
        max_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Put the agent on this call, by dialling a websocket leg into it.

        This is how Infobip carries two-way audio to software: a *dialog* with
        two legs — the caller, and a leg that terminates in our websocket. The
        carrier answers the caller automatically once that second leg connects,
        so an inbound call needs no explicit answer.

        The obvious-looking alternative, `start-media-stream`, is a one-way tap
        for recording. Used for the agent it produces a call that connects and
        then sits in silence, because nothing we send has a path back.
        """
        self._require()
        if not websocket_config_id:
            raise AppError(
                409,
                "This number has no audio endpoint registered with the carrier yet.",
                code="no_websocket_config",
            )
        payload: dict[str, Any] = {
            "parentCallId": parent_call_id,
            "childCallRequest": {
                "endpoint": {
                    "type": "WEBSOCKET",
                    "websocketEndpointConfigId": websocket_config_id,
                },
                "from": (from_number or self.tenant.infobip_phone_number).strip(),
                "connectTimeout": 30,
            },
            "maxDuration": max_seconds,
        }
        if record:
            payload["recording"] = {"recordingType": "AUDIO"}
        response = await self._request("POST", "/calls/1/dialogs", json=payload)
        return response.json() or {}

    async def dialog_recordings(self, dialog_id: str) -> list[dict]:
        response = await self._request("GET", f"/calls/1/recordings/dialogs/{dialog_id}")
        return recording_files(response.json())

    async def call_recordings(self, provider_call_id: str) -> list[dict]:
        response = await self._request("GET", f"/calls/1/recordings/calls/{provider_call_id}")
        return recording_files(response.json())

    async def download_file(self, file_id: str) -> tuple[bytes, str] | None:
        """The recording bytes and their mime type, or None."""
        try:
            response = await self._request("GET", f"/calls/1/files/{file_id}")
        except UpstreamError as exc:
            log.warning("could not fetch recording file %s: %s", file_id, exc)
            return None
        if not response.content:
            return None
        return response.content, response.headers.get("content-type", "audio/wav")
