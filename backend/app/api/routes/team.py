"""
Who else at a company can sign in, and what they may do.

Unlike every other route in this backend, these are not scoped by the
company's shared API key (`CurrentPerson`, not `CurrentTenant`) — on purpose.
Deciding who may invite somebody into a company is exactly the kind of
decision the shared key is too blunt an instrument for: anyone holding it can
already do anything *in* the company, but *who belongs to it* is a question
about people, answered from the Firestore link between a person and their
company, same as sign-in itself.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from ...errors import AppError, Forbidden, NotFound
from ...repositories import tenants as tenant_repo
from ...repositories import users as user_repo
from ..deps import CurrentPerson

log = logging.getLogger(__name__)

router = APIRouter(prefix="/team", tags=["team"])

ROLES = {"owner", "staff"}


async def _my_company(person) -> tuple[dict, str]:
    """The signed-in person's own link, and the company it points at.

    Every route here needs this first: nobody can manage a team before they
    are known to belong to one.
    """
    link = await user_repo.get(person.uid)
    if link is None:
        raise AppError(404, "No company is linked to this account yet.", code="needs_signup")
    return link, link["phoneNumberId"]


async def _require_owner(person) -> str:
    link, phone_number_id = await _my_company(person)
    if link.get("role") != "owner":
        raise Forbidden("Only the company's owner can do this.")
    return phone_number_id


@router.get("/invite/{token}")
async def peek_invite(token: str) -> dict:
    """What an invite link is for, before the invitee has an account to sign
    in with. No `CurrentPerson` on purpose — this is what the invite-accept
    page shows someone who has not signed in yet.

    Says only the company name, the role, and the email it was sent to —
    never the token back, never anything about the company beyond its name.
    An invite token is itself the secret (24 random bytes); this route does
    not widen what holding one reveals.
    """
    invite = await user_repo.get_invite(token)
    if invite is None or invite.get("status") != "pending":
        raise NotFound("Invitation")
    tenant = await tenant_repo.get(invite["phoneNumberId"])
    return {
        "email": invite.get("email", ""),
        "role": invite.get("role", "staff"),
        "companyName": tenant.name if tenant else "",
    }


@router.get("")
async def list_team(person: CurrentPerson) -> dict:
    """Everyone linked to my company, and who is still only invited."""
    _, phone_number_id = await _my_company(person)
    people = await user_repo.list_for_company(phone_number_id)
    invites = await user_repo.list_invites(phone_number_id)
    return {
        "members": [
            {
                "userId": p["uid"],
                "email": p.get("email", ""),
                "name": p.get("displayName") or p.get("email", ""),
                "role": p.get("role", "staff"),
                "isYou": p["uid"] == person.uid,
            }
            for p in people
        ],
        "invites": [
            {"token": i["token"], "email": i["email"], "role": i.get("role", "staff")}
            for i in invites
        ],
    }


class InviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="staff")


@router.post("/invite", status_code=status.HTTP_201_CREATED)
async def invite(person: CurrentPerson, payload: InviteRequest) -> dict:
    """Start adding somebody. Owner only.

    Returns a link rather than sending an email — this deployment has no
    email delivery configured yet (see Privacy Policy and the project's own
    notes on what is still missing). The owner shares the link themselves,
    the same way a calendar invite or a support ticket link gets shared.
    """
    phone_number_id = await _require_owner(person)
    email = payload.email.strip().lower()
    if "@" not in email:
        raise AppError(422, "Enter a valid email address.", code="bad_email")
    role = payload.role if payload.role in ROLES else "staff"
    if role == "owner":
        # One owner per company keeps "who can remove whom" unambiguous.
        # Transferring ownership is a deliberate, separate action, not a side
        # effect of an invite.
        raise AppError(
            422, "Invite as staff — ownership is not transferred by invitation.",
            code="owner_not_invitable",
        )

    token = await user_repo.create_invite(
        email=email, phone_number_id=phone_number_id, role=role, invited_by=person.uid,
    )
    log.info("tenant %s: %s invited %s as %s", phone_number_id, person.email, email, role)
    return {"token": token, "email": email, "role": role}


@router.delete("/invite/{token}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_invite(person: CurrentPerson, token: str) -> None:
    phone_number_id = await _require_owner(person)
    invite = await user_repo.get_invite(token)
    if invite is None or invite.get("phoneNumberId") != phone_number_id:
        raise NotFound("Invitation")
    await user_repo.revoke_invite(token)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_member(person: CurrentPerson, user_id: str) -> None:
    """Remove somebody from the team. Owner only, and not themselves.

    Does not rotate the company's API key — see `repositories/users.remove`
    for why that is a separate, more disruptive action the owner takes
    deliberately from Settings if they need access cut off immediately.
    """
    phone_number_id = await _require_owner(person)
    if user_id == person.uid:
        raise AppError(
            409, "You cannot remove yourself. Transfer ownership first.",
            code="cannot_remove_self",
        )
    target = await user_repo.get(user_id)
    if target is None or target.get("phoneNumberId") != phone_number_id:
        raise NotFound("Team member")
    await user_repo.remove(user_id)
    log.info("tenant %s: %s removed %s from the team", phone_number_id, person.email, user_id)
