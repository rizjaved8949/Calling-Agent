"""
The Meta Graph client, scoped to one tenant.

Every call is made with *that company's* access token against *that company's*
phone_number_id, which is what keeps one customer's messages from going out
under another's business name.

Numbers are normalised to E.164 before they reach Meta, which accepts nothing
else: a number typed in national form (`03191611020`) is expanded using the
tenant's own country code, because a Pakistani college and a UK clinic do not
share a default.
"""
from __future__ import annotations

import hashlib
import hmac
import contextlib
import logging
import re
from typing import Any

import httpx

from ..config import settings
from ..errors import AppError, UpstreamError
from ..models.call import Message, MessageDirection
from ..models.tenant import Tenant
from ..repositories import calls as call_repo

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"
# Meta's own cap on a text body.
MAX_BODY = 4096


def graph_version(tenant: Tenant) -> str:
    return (tenant.graph_api_version or settings.meta_graph_api_version or "v23.0").strip()


def to_e164(number: str, tenant: Tenant) -> str:
    """Normalise a number the way Meta requires.

    A leading `00` is the international prefix written out; a leading `0` is a
    national trunk code and is replaced by the country code. Anything already
    in `+…` form is left alone.
    """
    raw = re.sub(r"[^\d+]", "", (number or "").strip())
    if not raw:
        raise AppError(400, "A phone number is required.", code="bad_number")
    if raw.startswith("+"):
        digits = raw[1:]
    elif raw.startswith("00"):
        digits = raw[2:]
    elif raw.startswith("0"):
        country = (tenant.default_country_code or settings.default_country_code).strip()
        if not country:
            raise AppError(
                400,
                "That number is in national form and this company has no default "
                "country code. Enter it as +<country><number>.",
                code="bad_number",
            )
        digits = f"{country}{raw[1:]}"
    else:
        digits = raw
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        raise AppError(400, f"{number!r} is not a usable phone number.", code="bad_number")
    return f"+{digits}"


def msisdn(number: str, tenant: Tenant) -> str:
    """E.164 without the plus, which is the form Graph expects in `to`."""
    return to_e164(number, tenant).lstrip("+")


def mask(number: str) -> str:
    """For logs. The last four digits are enough to identify a call in support."""
    digits = re.sub(r"\D", "", number or "")
    return f"…{digits[-4:]}" if len(digits) >= 4 else "…"


def signature_ok(tenant: Tenant | None, raw: bytes, header: str | None) -> bool:
    """Verify Meta actually sent this.

    The webhook endpoint is public and triggers an LLM, so without this anyone
    who finds the URL can spend the customer's tokens and put words in their
    agent's mouth. Meta signs every delivery with the app secret.

    Compared in constant time. If no app secret is configured the request is
    refused outright rather than waved through — an unauthenticated webhook is
    not a degraded mode, it is an open door.

    Each company brings its own Meta app, so the secret is theirs. There is no
    fall back to the platform's: verifying a customer's delivery against our
    secret would mean any company able to sign with it could forge traffic for
    any other. The env value is the platform owner's own number and is reached
    only through the legacy single-tenant row, which carries it as that row's
    own `app_secret`.
    """
    secret = (tenant.app_secret if tenant else settings.meta_app_secret).strip()
    if not secret:
        log.error(
            "no app secret for %s — refusing the webhook unverified",
            tenant.phone_number_id if tenant else "an unknown number",
        )
        return False
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header.split("=", 1)[1])


class WhatsApp:
    """Graph calls for one tenant."""

    def __init__(self, tenant: Tenant) -> None:
        self.tenant = tenant

    @property
    def configured(self) -> bool:
        return self.tenant.configured

    def _require(self) -> None:
        if not self.configured:
            raise AppError(
                409,
                "This company has not finished connecting WhatsApp. Add the access "
                "token, WABA id and phone number id first.",
                code="tenant_not_configured",
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tenant.access_token.strip()}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{GRAPH}/{graph_version(self.tenant)}/{path.lstrip('/')}"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0)) as client:
                response = await client.post(
                    self._url(path), headers=self._headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"WhatsApp did not respond: {exc}") from exc
        if response.status_code >= 400:
            detail = _graph_error(response)
            log.warning("WhatsApp %s -> %s: %s", path, response.status_code, detail)
            raise UpstreamError(f"WhatsApp refused the request: {detail}", details=detail)
        return response.json() or {}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0)) as client:
                response = await client.get(
                    self._url(path), headers=self._headers, params=params
                )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"WhatsApp did not respond: {exc}") from exc
        if response.status_code >= 400:
            detail = _graph_error(response)
            raise UpstreamError(f"WhatsApp refused the request: {detail}", details=detail)
        return response.json() or {}

    # ---- Messages ---------------------------------------------------------

    async def send_text(self, to: str, body: str, *, call_id: str = "") -> Message:
        """Send a plain, non-template message.

        Legal only inside the 24-hour customer service window. Outside it Meta
        accepts this call and never delivers the message, so a template is the
        only thing that reaches someone who has not written recently.
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": msisdn(to, self.tenant),
            "type": "text",
            # Link previews turn a mentioned URL into a card that buries the
            # answer under a thumbnail. These answers are text.
            "text": {"preview_url": False, "body": body[:MAX_BODY]},
        }
        result = await self._post(f"{self.tenant.graph_number_id}/messages", payload)
        return await self._record(
            to=to, body=body, kind="text", result=result, call_id=call_id
        )

    async def send_template(
        self,
        to: str,
        template: str,
        *,
        language: str = "en_US",
        parameters: list[str] | None = None,
        call_id: str = "",
    ) -> Message:
        """Send an approved template — the only way to open a conversation."""
        body_text = ""
        with contextlib.suppress(Exception):
            known = await self.template(template, language)
            if known:
                body_text = fill(known.get("body") or "", parameters or [])
        components: list[dict[str, Any]] = []
        if parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in parameters],
                }
            )
        payload = {
            "messaging_product": "whatsapp",
            "to": msisdn(to, self.tenant),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language},
                **({"components": components} if components else {}),
            },
        }
        result = await self._post(f"{self.tenant.graph_number_id}/messages", payload)
        # Stored as the words the person actually received, not as the list of
        # values we filled in. A log reading "482103" tells whoever reads it
        # back nothing; the resolved sentence tells them everything.
        return await self._record(
            to=to,
            body=body_text or " | ".join(parameters or []),
            kind="template",
            result=result,
            template=template,
            call_id=call_id,
        )

    async def mark_read(self, provider_message_id: str) -> None:
        """Show the blue ticks. Best effort — never worth failing a reply over."""
        try:
            await self._post(
                f"{self.tenant.graph_number_id}/messages",
                {
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": provider_message_id,
                },
            )
        except (AppError, UpstreamError) as exc:
            log.debug("could not mark %s read: %s", provider_message_id, exc)

    async def templates(self) -> list[dict[str, Any]]:
        """The approved templates for this company's WABA."""
        if not self.tenant.waba_id:
            return []
        body = await self._get(
            f"{self.tenant.waba_id}/message_templates",
            {"limit": 100, "fields": "name,status,category,language,components"},
        )
        return [describe_template(t) for t in (body.get("data") or [])]

    async def template(self, name: str, language: str = "") -> dict[str, Any] | None:
        """One template, by name and optionally language."""
        for found in await self.templates():
            if found["name"] == name and (not language or found["language"] == language):
                return found
        return None

    # ---- Calling ----------------------------------------------------------

    async def place_call(self, to: str, sdp_offer: str) -> dict[str, Any]:
        """Start a business-initiated call, offering our end of the audio.

        Only legal once the person has granted call permission — see
        `request_call_permission`. Without it Meta refuses, and rightly: an
        unsolicited voice call from a business is the thing that permission
        exists to prevent.
        """
        return await self._post(
            f"{self.tenant.graph_number_id}/calls",
            {
                "messaging_product": "whatsapp",
                "to": msisdn(to, self.tenant),
                "action": "connect",
                "session": {"sdp_type": "offer", "sdp": sdp_offer},
            },
        )

    async def request_call_permission(
        self, to: str, template: str = "call_permission_request",
        language: str = "en",
    ) -> dict[str, Any]:
        """Ask someone for permission to call them.

        Meta requires this before a business may place a WhatsApp call. It
        arrives as a message with an accept button; granting it opens a window
        during which `place_call` is allowed.

        Sent as an ordinary template, because that is what it is — the name is
        the only part Meta treats specially.
        """
        return await self._post(
            f"{self.tenant.graph_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "to": msisdn(to, self.tenant),
                "type": "template",
                "template": {"name": template, "language": {"code": language}},
            },
        )

    async def answer_call(self, provider_call_id: str, sdp_answer: str) -> dict[str, Any]:
        """Accept an inbound call, handing Meta our SDP answer.

        This is the reply to the offer that arrived on the webhook. Until it is
        sent the caller hears ringing; after it, Meta starts media.
        """
        return await self._post(
            f"{self.tenant.graph_number_id}/calls",
            {
                "messaging_product": "whatsapp",
                "call_id": provider_call_id,
                "action": "accept",
                "session": {"sdp_type": "answer", "sdp": sdp_answer},
            },
        )

    async def reject_call(self, provider_call_id: str) -> dict[str, Any]:
        return await self._post(
            f"{self.tenant.graph_number_id}/calls",
            {
                "messaging_product": "whatsapp",
                "call_id": provider_call_id,
                "action": "reject",
            },
        )

    async def terminate_call(self, provider_call_id: str) -> dict[str, Any]:
        return await self._post(
            f"{self.tenant.graph_number_id}/calls",
            {
                "messaging_product": "whatsapp",
                "call_id": provider_call_id,
                "action": "terminate",
            },
        )

    # ---- Internal ---------------------------------------------------------

    async def _record(
        self,
        *,
        to: str,
        body: str,
        kind: str,
        result: dict[str, Any],
        template: str = "",
        call_id: str = "",
    ) -> Message:
        messages = result.get("messages") or []
        provider_id = (messages[0].get("id") if messages else "") or ""
        message = Message(
            tenantId=self.tenant.phone_number_id,
            providerMessageId=provider_id,
            direction=MessageDirection.OUTBOUND,
            counterparty=to_e164(to, self.tenant),
            kind=kind,
            body=body,
            templateName=template,
            status="accepted",
            callId=call_id,
            lineId=self.tenant.line_id,
        )
        return await call_repo.save_message(message)


PLACEHOLDER = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def describe_template(raw: dict[str, Any]) -> dict[str, Any]:
    """A template in the shape a screen can actually use.

    Meta returns its parts as an unordered list of components, and the number
    of values it expects is not stated anywhere — it is however many distinct
    {{n}} appear in the body. Sending the wrong number is refused with
    "(#132000) Number of parameters does not match the expected number of
    params", which names no template and no count, so a form that guesses
    produces an error nobody can act on.
    """
    parts = {str(c.get("type", "")).upper(): c for c in (raw.get("components") or [])}
    body = (parts.get("BODY", {}).get("text") or "").strip()
    numbers = {int(n) for n in PLACEHOLDER.findall(body)}
    example = (
        (parts.get("BODY", {}).get("example") or {}).get("body_text") or [[]]
    )[0]
    return {
        "name": raw.get("name", ""),
        "status": raw.get("status", ""),
        "category": raw.get("category", ""),
        "language": raw.get("language", ""),
        "header": (parts.get("HEADER", {}).get("text") or "").strip(),
        "body": body,
        "footer": (parts.get("FOOTER", {}).get("text") or "").strip(),
        # The count Meta will check against, and an example per placeholder so
        # the form can show what belongs there.
        "placeholders": max(numbers) if numbers else 0,
        "examples": [str(x) for x in example],
    }


def fill(body: str, values: list[str]) -> str:
    """The template's words with the values put in, as the reader sees them."""
    def swap(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return values[index] if 0 <= index < len(values) else match.group(0)

    return PLACEHOLDER.sub(swap, body)


def _graph_error(response: httpx.Response) -> str:
    """The human-readable half of a Graph error.

    Graph nests the useful sentence three levels down and returns 400 for
    everything; surfacing `error.message` is the difference between "WhatsApp
    refused the request" and "template name does not exist in the translation".
    """
    try:
        error = (response.json() or {}).get("error") or {}
        parts = [error.get("message"), error.get("error_user_title"), error.get("error_user_msg")]
        detail = " — ".join(p for p in parts if p)
        return detail or response.text[:200]
    except ValueError:
        return response.text[:200]
