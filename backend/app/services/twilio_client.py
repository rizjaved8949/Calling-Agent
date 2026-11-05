"""
Phone calls through Twilio, scoped to one number's account.

The control plane only, like `telephony.Infobip`: place a call, hang one up,
and the three requests verification needs (the account, the number in it, and
pointing that number's webhooks at us). Audio does not pass through here; it
arrives on the media socket in `api/routes/media.py`.

Plain httpx rather than Twilio's SDK, for the same reason `Infobip` is: five
requests do not need a dependency, and keeping every carrier the same shape
keeps `services/carriers.py` a few lines long.

Twilio's REST API is form-encoded in and JSON out, authenticated with HTTP
Basic (Account SID as the user, Auth Token as the password). A refusal carries
a numeric `code`; the ones a company can act on are turned into sentences
here, because "HTTP 400" tells nobody what to fix and "Trial accounts can only
call verified numbers" does.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from typing import Any, Iterable

import httpx

from ..config import settings
from ..errors import AppError, UpstreamError
from ..models.tenant import Tenant

log = logging.getLogger(__name__)

API_BASE = "https://api.twilio.com/2010-04-01"

# "AC" and 32 hex digits. Checked before it goes into a URL path, so a value
# pasted with a stray slash or space is refused here with a sentence instead of
# reaching some other path of Twilio's API.
_ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
# Any Twilio resource id: two letters and 32 hex digits ("CA…", "PN…").
_RESOURCE_SID = re.compile(r"^[A-Z]{2}[0-9a-fA-F]{32}$")

# What Twilio asks us to be told about on an outbound call.
STATUS_EVENTS = ("initiated", "ringing", "answered", "completed")

# The audio socket's own proof, since Twilio does not sign the websocket: a
# `security/tickets` ticket for this audience, naming the call, handed to
# Twilio inside the TwiML and returned to us in the stream's `start` message.
# Two minutes is ample: Twilio opens the socket within a second or two of
# reading the TwiML, and a token that leaks later is already dead.
MEDIA_AUDIENCE = "twilio-media"
MEDIA_TOKEN_TTL_SECONDS = 120


# ---------------------------------------------------------------------------
# Webhook signatures
# ---------------------------------------------------------------------------


def expected_signature(auth_token: str, url: str, params: Iterable[tuple[str, str]]) -> str:
    """What `X-Twilio-Signature` must be for this request.

    Twilio's recipe: the full URL as Twilio called it (query string included),
    then every POST parameter's name and value appended with no separator, in
    name order; HMAC-SHA1 that with the Auth Token and base64 the digest.
    Sorting the (name, value) pairs puts a repeated name's values in order
    too, which is how Twilio's own validator treats them.
    """
    data = url + "".join(name + value for name, value in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def signature_ok(auth_token: str, url: str, params: Iterable[tuple[str, str]],
                 header: str | None) -> bool:
    """Whether Twilio really sent this. Constant-time; no token, no pass."""
    if not auth_token or not header:
        return False
    return hmac.compare_digest(expected_signature(auth_token, url, params), header)


class TwilioRefused(AppError):
    """Twilio said no. `twilio_code` is its own error number, when it gave one.

    Kept as its own class so verification can tell "wrong token" from "number
    not found" without matching on message text.
    """

    def __init__(self, status: int, message: str, *, code: str,
                 twilio_code: int | None = None, http_status: int = 0) -> None:
        super().__init__(status, message, code=code,
                         details={"twilioCode": twilio_code} if twilio_code else None)
        self.twilio_code = twilio_code
        self.http_status = http_status


# Twilio's error number → (our status, our code, what to tell the company).
# Only the ones a company can do something about; anything else falls through
# to a generic refusal that still quotes Twilio's number for the console logs.
_KNOWN: dict[int, tuple[int, str, str]] = {
    20003: (409, "twilio_auth",
            "Twilio rejected the Account SID or Auth Token. Copy both again from "
            "Account Info on the Twilio Console home page."),
    20404: (404, "twilio_not_found",
            "Twilio has no record of that. The call may already have ended."),
    20429: (429, "twilio_rate_limited",
            "Twilio is limiting how fast this account can place calls. Try again "
            "in a moment."),
    21210: (409, "twilio_caller_id",
            "Twilio will not use this line's number as the caller ID: it is not a "
            "number in that Twilio account (Twilio error 21210)."),
    21211: (422, "twilio_bad_destination",
            "Twilio says the number being called is not a valid phone number "
            "(Twilio error 21211)."),
    21215: (409, "twilio_geo_permission",
            "This Twilio account is not allowed to call that country yet "
            "(Twilio error 21215). Turn it on in the Twilio Console under "
            "Voice → Settings → Geo permissions."),
    13227: (409, "twilio_geo_permission",
            "This Twilio account is not allowed to call that country yet "
            "(Twilio error 13227). Turn it on in the Twilio Console under "
            "Voice → Settings → Geo permissions."),
    21217: (422, "twilio_bad_destination",
            "Twilio says the number being called does not look valid "
            "(Twilio error 21217)."),
    21219: (409, "twilio_trial_unverified",
            "Trial accounts can only call verified numbers (Twilio error 21219). "
            "Add this number under Phone Numbers → Verified Caller IDs in the "
            "Twilio Console, or upgrade the account."),
}


def _refusal(response: httpx.Response) -> TwilioRefused | UpstreamError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    twilio_code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(twilio_code, int):
        twilio_code = None
    if twilio_code in _KNOWN:
        status, code, message = _KNOWN[twilio_code]
        return TwilioRefused(status, message, code=code, twilio_code=twilio_code,
                             http_status=response.status_code)
    if response.status_code == 401:
        # No error body, but a 401 from Twilio means one thing.
        status, code, message = _KNOWN[20003]
        return TwilioRefused(status, message, code=code, twilio_code=20003, http_status=401)
    suffix = f", Twilio error {twilio_code}" if twilio_code else ""
    return TwilioRefused(
        502,
        f"Twilio refused the request (HTTP {response.status_code}{suffix}).",
        code="twilio_refused", twilio_code=twilio_code, http_status=response.status_code,
    )


def _client() -> httpx.AsyncClient:
    """One client per request, as `Infobip` does. Its own function so the
    tests can hand it a mock transport without touching httpx globally."""
    return httpx.AsyncClient(timeout=settings.request_timeout_seconds)


class Twilio:
    """Voice API for one number's Twilio account."""

    def __init__(self, tenant: Tenant) -> None:
        self.tenant = tenant
        # Only ever the number's own credentials, or the platform's where the
        # operator allowed it — `repositories/numbers.as_tenant` decided which.
        self.account_sid = tenant.twilio_account_sid.strip()
        self.auth_token = tenant.twilio_auth_token.strip()

    @property
    def configured(self) -> bool:
        return bool(self.account_sid and self.auth_token)

    def _require(self) -> None:
        if not self.configured:
            raise AppError(
                409,
                "This line has no Twilio credentials. Add the Account SID and "
                "Auth Token first.",
                code="telephony_not_configured",
            )
        if not _ACCOUNT_SID.match(self.account_sid):
            raise AppError(
                422,
                "That is not a Twilio Account SID. It starts with AC and is 34 "
                "characters long; copy it from Account Info on the Twilio Console.",
                code="twilio_bad_account_sid",
            )

    @property
    def _account_url(self) -> str:
        return f"{API_BASE}/Accounts/{self.account_sid}"

    async def _request(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        self._require()
        try:
            async with _client() as client:
                response = await client.request(
                    method, url, auth=(self.account_sid, self.auth_token),
                    headers={"Accept": "application/json"}, **kw,
                )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Twilio did not respond: {exc}") from exc
        if response.status_code >= 400:
            error = _refusal(response)
            # The code and status only: Twilio's message often quotes the
            # phone number, which is masked everywhere else in our logs.
            log.warning("twilio %s %s -> %s (code %s)", method,
                        url.removeprefix(API_BASE), response.status_code,
                        getattr(error, "twilio_code", None))
            raise error
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _sid(value: str, what: str) -> str:
        value = (value or "").strip()
        if not _RESOURCE_SID.match(value):
            raise AppError(422, f"That is not a Twilio {what} id.", code="twilio_bad_sid")
        return value

    # ---- Calls -------------------------------------------------------------

    async def place_call(
        self, to: str, *, from_number: str = "", answer_url: str, status_url: str
    ) -> dict[str, Any]:
        """Ring `to`. When they answer, Twilio fetches `answer_url` for TwiML.

        Every status from `initiated` to `completed` is reported to
        `status_url`, which is how a call that is never answered still gets
        settled here rather than sitting at "ringing" for ever.
        """
        self._require()
        caller = (from_number or self.tenant.infobip_phone_number).strip()
        if not caller:
            raise AppError(409, "This line has no phone number to call from.",
                           code="no_caller_id")
        return await self._request("POST", f"{self._account_url}/Calls.json", data={
            "To": to,
            "From": caller,
            "Url": answer_url,
            "Method": "POST",
            "StatusCallback": status_url,
            "StatusCallbackMethod": "POST",
            # A list: httpx sends one form field per event, which is how
            # Twilio expects several values for one parameter.
            "StatusCallbackEvent": list(STATUS_EVENTS),
        })

    async def hangup(self, call_sid: str) -> None:
        """End the call, whether it is still ringing or already talking.

        `completed` rather than `canceled`: Twilio's `canceled` only stops a
        call that has not been answered, and leaves a live one alone.
        """
        sid = self._sid(call_sid, "call")
        await self._request("POST", f"{self._account_url}/Calls/{sid}.json",
                            data={"Status": "completed"})

    # ---- Verification ------------------------------------------------------

    async def account(self) -> dict[str, Any]:
        """The account itself: `status` (active/suspended/closed), `type` (Trial/Full)."""
        return await self._request("GET", f"{self._account_url}.json")

    async def find_number(self, e164: str) -> dict[str, Any] | None:
        """This phone number as the account owns it, or None if it does not."""
        body = await self._request(
            "GET", f"{self._account_url}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": e164},
        )
        found = body.get("incoming_phone_numbers") or []
        return found[0] if found and isinstance(found[0], dict) else None

    async def configure_number(self, pn_sid: str, voice_url: str, status_url: str) -> dict[str, Any]:
        """Point the number's "A call comes in" and status callback at us."""
        sid = self._sid(pn_sid, "phone number")
        return await self._request(
            "POST", f"{self._account_url}/IncomingPhoneNumbers/{sid}.json",
            data={
                "VoiceUrl": voice_url,
                "VoiceMethod": "POST",
                "StatusCallback": status_url,
                "StatusCallbackMethod": "POST",
            },
        )
