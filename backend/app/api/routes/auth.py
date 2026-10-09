"""
Where a signed-in browser becomes a company's API key.

Firebase Authentication proves who somebody is; everything else in this
backend is built around a company's own API key (`api/deps.py`). This module
is the one place those two worlds meet: it verifies a Firebase ID token, looks
up (or creates) the Firestore link from that person to a company, and hands
back the same kind of key `POST /api/companies` has always issued — so every
other route is unchanged by sign-in existing at all.

The key is handed back on both signup and login, same as `companies.py`
already does once at creation. That is a deliberate widening of that file's
"shown once" rule, not an oversight: a login is already an authenticated
action by the company's own signed-in owner, not a public read, so there is
nobody new being handed a secret they could not already reach another way.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from ...errors import AppError, Conflict
from ...models.tenant import Tenant
from ...repositories import tenants as tenant_repo
from ...repositories import users as user_repo
from ..deps import CurrentPerson

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=200, alias="companyName")

    model_config = {"populate_by_name": True}


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1)


def _response(tenant: Tenant, *, email: str, role: str) -> dict:
    return {
        # Shown on every login, not just once — see this module's docstring.
        "apiKey": tenant.api_key,
        "phoneNumberId": tenant.phone_number_id,
        "companyName": tenant.name,
        "email": email,
        # Drives which screens the frontend shows — Settings, Team and
        # billing are owner-only. See Protected/manage in app.tsx.
        "role": role,
    }


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(person: CurrentPerson, payload: SignupRequest) -> dict:
    """A newly signed-up person's first company.

    Idempotent on purpose: a double-click or a retried request from a flaky
    connection finds the company already linked and simply logs them into it,
    rather than erroring on the second attempt or — worse — silently creating
    a second company nobody can find.
    """
    existing = await user_repo.get(person.uid)
    if existing is not None:
        tenant = await tenant_repo.get(existing["phoneNumberId"])
        if tenant is not None:
            return _response(tenant, email=person.email, role=existing.get("role", "owner"))

    # Meta's real phone_number_id is not known yet — that is connected later,
    # from Settings, the same as every company already does. This placeholder
    # only has to be unique enough to serve as this company's row id until
    # then; `pending-` makes an unconfigured company visible at a glance
    # anywhere a phone_number_id is logged or displayed.
    phone_number_id = f"pending-{uuid.uuid4().hex[:16]}"
    api_key = tenant_repo.new_api_key()
    tenant = Tenant(
        phoneNumberId=phone_number_id,
        name=payload.company_name.strip(),
        apiKey=api_key,
    )
    await tenant_repo.save(tenant)
    try:
        await user_repo.create(
            person.uid,
            email=person.email,
            display_name=person.display_name,
            phone_number_id=phone_number_id,
            role="owner",
        )
    except Exception:
        # The company row exists but nothing points a person at it yet. Undo
        # it rather than leave an orphan nobody can sign into — the person
        # retrying signup is the recovery path, and it should find a clean
        # slate, not a phone_number_id collision with a company that is not
        # reachable any other way.
        await tenant_repo.delete(phone_number_id)
        raise AppError(
            502, "Could not finish setting up your account. Please try again.",
            code="signup_failed",
        )

    log.info("signed up %s as %s (%s)", person.email, tenant.name, phone_number_id)
    return _response(tenant, email=person.email, role="owner")


@router.post("/login")
async def login(person: CurrentPerson) -> dict:
    """An existing person's return visit."""
    link = await user_repo.get(person.uid)
    if link is None:
        raise AppError(
            404,
            "No company is linked to this account yet.",
            code="needs_signup",
        )
    tenant = await tenant_repo.get(link["phoneNumberId"])
    if tenant is None:
        # The company was deleted (by an admin, or by itself) but the
        # Firestore link survived. Surfacing this distinctly rather than as a
        # generic 404 is what lets the frontend show "contact support" rather
        # than quietly routing them back through signup into a second,
        # unrelated company.
        raise Conflict(
            "Your account is linked to a company that no longer exists. "
            "Contact support."
        )
    return _response(tenant, email=person.email, role=link.get("role", "owner"))


@router.post("/accept-invite", status_code=status.HTTP_201_CREATED)
async def accept_invite(person: CurrentPerson, payload: AcceptInviteRequest) -> dict:
    """Join a company somebody already runs, instead of starting a new one.

    The invited email has to match the signed-in person's own — an invite
    names who it is for, and a token leaking (a forwarded link, a shared
    inbox) must not let a stranger into the company with it.
    """
    existing = await user_repo.get(person.uid)
    if existing is not None:
        raise Conflict("This account is already linked to a company.")

    invite = await user_repo.get_invite(payload.token)
    if invite is None or invite.get("status") != "pending":
        raise AppError(404, "That invitation is no longer valid.", code="invalid_invite")
    if invite.get("email", "").lower() != person.email.lower():
        raise AppError(
            403,
            "This invitation was sent to a different email address.",
            code="invite_email_mismatch",
        )

    tenant = await tenant_repo.get(invite["phoneNumberId"])
    if tenant is None:
        raise Conflict("That company no longer exists.")

    await user_repo.create(
        person.uid,
        email=person.email,
        display_name=person.display_name,
        phone_number_id=invite["phoneNumberId"],
        role=invite.get("role", "staff"),
    )
    await user_repo.consume_invite(payload.token)
    log.info("tenant %s: %s accepted an invite as %s",
             tenant.phone_number_id, person.email, invite.get("role", "staff"))
    return _response(tenant, email=person.email, role=invite.get("role", "staff"))
