"""
Proving a number's credentials work, and finding the right credentials for a call.

Verification asks the provider a question only the real owner of these
credentials can get an answer to: Meta is asked about the phone number id with
the access token, Infobip is asked for the account balance with the API key.
A wrong token, a number from another WABA or a typo in the base URL each fail
here, in the settings screen, instead of on the first customer's call.
"""
from __future__ import annotations

import logging
import time

import httpx

from ..config import settings
from ..models.call import Call
from ..errors import AppError
from ..models.number import NumberKind, NumberStatus, PhoneNumber
from ..models.tenant import Tenant
from ..repositories import numbers as number_repo
from .whatsapp import GRAPH, graph_version

log = logging.getLogger(__name__)


async def verify(tenant: Tenant, number: PhoneNumber) -> PhoneNumber:
    """Ask the provider, record the answer on the number, and return it."""
    effective = number_repo.as_tenant(tenant, number)
    try:
        if number.kind is NumberKind.WHATSAPP:
            detail = await _verify_meta(effective)
        else:
            detail = await _verify_infobip(effective)
    except _Refused as exc:
        number.status = NumberStatus.FAILED
        number.status_detail = str(exc)[:400]
        number.verified_at = None
    else:
        number.status = NumberStatus.VERIFIED
        number.status_detail = detail[:400]
        number.verified_at = time.time()
    await number_repo.save(number)
    log.info("number %s (%s) verification: %s", number.id, number.kind.value, number.status.value)
    return number


class _Refused(Exception):
    pass


async def _verify_meta(t: Tenant) -> str:
    missing = [
        name for name, value in (
            ("Phone number ID", t.meta_phone_number_id),
            ("WhatsApp Business Account ID", t.waba_id),
            ("Access token", t.access_token),
            ("App secret", t.app_secret),
        ) if not value
    ]
    if missing:
        raise _Refused("Missing: " + ", ".join(missing) + ".")
    url = f"{GRAPH}/{graph_version(t)}/{t.meta_phone_number_id}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {t.access_token}"},
                params={"fields": "display_phone_number,verified_name,quality_rating"},
            )
    except httpx.HTTPError as exc:
        raise _Refused(f"Could not reach Meta: {exc}") from exc
    body = _json(response)
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message") or response.text[:200]
        raise _Refused(f"Meta refused these credentials: {message}")
    display = body.get("display_phone_number") or ""
    name = body.get("verified_name") or ""
    return f"Meta confirmed {display or 'the number'}" + (f" ({name})" if name else "") + "."


async def _verify_infobip(t: Tenant) -> str:
    """Ask Infobip for the account's calls configurations.

    Not the account balance, which was the obvious choice and the wrong one:
    `/account/1/balance` is absent on some regional hosts (it 404s on
    api-pk2), so a perfectly good key looked refused. Configurations is the
    better question anyway — reaching it proves the key carries the Voice
    scope, which is the permission a call actually needs, and the response
    lets us check the configuration id the company entered really exists
    rather than discovering it is a typo on the first call.
    """
    missing = [
        name for name, value in (
            ("API key", t.infobip_api_key),
            ("Base URL", t.infobip_base_url),
            ("Phone number", t.infobip_phone_number),
        ) if not value
    ]
    if missing:
        raise _Refused("Missing: " + ", ".join(missing) + ".")
    base = normalise_base_url(t.infobip_base_url)
    headers = {"Authorization": f"App {t.infobip_api_key}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{base}/calls/1/configurations", headers=headers)
            if response.status_code == 404:
                # An account without the Calls API enabled at all. The balance
                # endpoint at least distinguishes a bad key from a bad URL.
                response = await client.get(f"{base}/account/1/balance", headers=headers)
                if response.status_code < 300:
                    raise _Refused(
                        "The key works, but this account has no Voice/Calls API. "
                        "Enable Voice in Infobip, or use a key with the Calls scope."
                    )
    except httpx.HTTPError as exc:
        raise _Refused(f"Could not reach Infobip at {base}: {exc}") from exc

    if response.status_code in {401, 403}:
        raise _Refused(
            "Infobip rejected the API key. Check it is correct and has the Voice/Calls scope."
        )
    body = _json(response)
    if response.status_code >= 400:
        text = ((body.get("requestError") or {}).get("serviceException") or {}).get("text")
        raise _Refused(f"Infobip refused the request: {text or response.status_code}")

    configurations = body.get("results") or []
    names = {str(c.get("id")): str(c.get("name") or "") for c in configurations if isinstance(c, dict)}
    chosen = t.infobip_calls_configuration_id.strip()
    if chosen and chosen not in names:
        raise _Refused(
            f"Infobip has no calls configuration with id {chosen!r}. "
            + (f"Available: {', '.join(sorted(names)) }." if names
               else "This account has none configured yet.")
        )
    if chosen:
        return f"Infobip reached, using the {names[chosen] or chosen!r} calls configuration."
    return (
        f"Infobip reached, {len(configurations)} calls "
        f"configuration{'' if len(configurations) == 1 else 's'} available."
    )


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def normalise_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if value and not value.startswith("http"):
        value = "https://" + value
    return value


async def whatsapp_sender(tenant: Tenant) -> Tenant | None:
    """This company, speaking through a WhatsApp number that can send.

    A call may be running on a SIM line, whose overlay holds carrier
    credentials and no Meta ones. Sending a message from it would fail, so the
    company's own WhatsApp number is found instead. Returns None when there is
    none, which is how the agent is stopped from offering to text somebody.
    """
    company_id = tenant.phone_number_id
    try:
        numbers = await number_repo.list_for(company_id)
    except Exception:  # noqa: BLE001
        log.exception("tenant %s: could not read numbers", company_id)
        return None
    for number in numbers:
        if number.kind is NumberKind.WHATSAPP and number.verified:
            company = tenant.model_copy(update={"line_id": "", "meta_phone_number_id": ""})
            return number_repo.as_tenant(company, number)
    # A company from before numbers existed keeps its credentials on its own
    # row, and those still work.
    return tenant if tenant.configured else None


async def tenant_for_call(tenant: Tenant, call: Call) -> Tenant:
    """The credentials a call was placed or answered with."""
    if tenant.line_id or not call.line_id:
        return tenant
    number = await number_repo.get(tenant.phone_number_id, call.line_id)
    return number_repo.as_tenant(tenant, number) if number else tenant


def webhook_base() -> str:
    return settings.public_base_url.strip().rstrip("/")


def audio_socket_url() -> str:
    """The one address the carrier dials for call audio on this deployment."""
    base = webhook_base()
    if not base:
        return ""
    return base.replace("https://", "wss://").replace("http://", "ws://") + "/api/media/infobip"


async def ensure_audio_endpoint(tenant: Tenant, number: PhoneNumber) -> str:
    """The id of this deployment's audio socket in the company's Infobip account.

    Registered on first use rather than asked for on the settings form: it is
    not a credential and not something a customer could reasonably know — it
    names a socket on *our* server. Reused when one already points at the same
    URL, so a redeploy does not litter the account with duplicates, and
    re-registered automatically if the deployment's address changes.
    """
    from .telephony import Infobip

    url = audio_socket_url()
    if not url:
        raise AppError(
            503,
            "PUBLIC_BASE_URL is not set on the server, so the carrier has nowhere "
            "to send call audio.",
            code="no_public_base_url",
        )
    carrier = Infobip(tenant)
    existing = await carrier.websocket_endpoint_configs()
    match = next((c for c in existing if (c.get("url") or "") == url), None)
    if match is None:
        match = await carrier.create_websocket_endpoint_config(
            f"calling-agent {number.phone_number}".strip(), url
        )
        log.info("number %s: registered the audio endpoint %s with Infobip",
                 number.id, match.get("id"))
    config_id = str(match.get("id") or "")
    if config_id and config_id != number.infobip_websocket_config_id:
        number.infobip_websocket_config_id = config_id
        await number_repo.save(number)
    return config_id
