"""
Who is asking, and which company they are asking about.

Two kinds of caller:

* **the platform operator**, presenting `X-Admin-Key`. Can create companies and
  read any of them. This is you, not a customer.
* **a company**, presenting `Authorization: Bearer <their api key>`. Can only
  ever reach its own calls, recordings and messages — the tenant is derived
  *from the key*, never from a path or query parameter, so there is no request
  shape that asks for someone else's data.

That last point is the whole isolation model. A route that takes a tenant id
from the URL and trusts it would undo it, which is why `current_tenant` has no
parameter to override.
"""
from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, Request

from ..config import settings
from ..errors import Forbidden, NotFound, Unauthorized
from ..models.tenant import Tenant
from ..repositories import tenants as tenant_repo
from ..services import firebase


def _bearer(value: str | None) -> str:
    if not value:
        return ""
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return value.strip()


async def is_admin(x_admin_key: str | None) -> bool:
    """The server's ADMIN_API_KEY, or a signed-in super admin's session."""
    from ..security import superadmin

    presented = (x_admin_key or "").strip()
    if not presented:
        return False
    expected = settings.admin_api_key.strip()
    if expected and secrets.compare_digest(presented, expected):
        return True
    return await superadmin.token_valid(presented)


async def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """The platform surface: creating and editing companies.

    A blank ADMIN_API_KEY disables these routes rather than leaving them open.
    An admin surface that is unauthenticated because nobody set a variable is
    the kind of default that ends up on the internet.
    """
    if not await is_admin(x_admin_key):
        raise Unauthorized("Sign in as the super admin, or present a valid X-Admin-Key.")


async def current_tenant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> Tenant:
    """The company this request speaks for.

    An admin key may name a company with `X-Company-Id`, which is how the
    platform dashboard looks at a customer's calls without holding that
    customer's API key. Nothing else can.
    """
    if await is_admin(x_admin_key):
        impersonated = request.headers.get("x-company-id", "").strip()
        if impersonated:
            tenant = await tenant_repo.get(impersonated)
            if tenant is None:
                raise NotFound("Company")
            return tenant

    key = _bearer(authorization)
    if not key:
        raise Unauthorized("Present the company API key as a bearer token.")
    tenant = await tenant_repo.by_api_key(key)
    if tenant is None:
        # Deliberately the same message as a missing key: telling an attacker
        # that a key is well-formed but unknown is a hint they can work with.
        raise Unauthorized("Present the company API key as a bearer token.")
    if tenant.suspended:
        # 403 rather than 401: the credential is perfectly good, and telling
        # them to sign in again would send them round a loop that cannot end.
        raise Forbidden(
            tenant.suspended_reason
            or "This account is switched off. Contact the platform operator."
        )
    return tenant


CurrentTenant = Annotated[Tenant, Depends(current_tenant)]
AdminOnly = Annotated[None, Depends(require_admin)]


class Member:
    """The person behind a request, when there is one.

    Every route authenticates by the *company* API key, and every employee of
    a company holds the same one — so until now the server could tell which
    company was asking but never which person, and a rule like "staff cannot
    delete a recording" could only ever be drawn in the UI, which is a
    suggestion rather than a rule.

    The browser sends its Firebase ID token alongside the company key, in
    `X-User-Token`, and that is what names the person. It is optional on
    purpose: a script holding the API key has no Firebase session, and
    refusing those would break every integration. Such a caller is treated as
    the owner, which is exactly what holding the company key already grants.
    """

    __slots__ = ("uid", "email", "role")

    def __init__(self, uid: str = "", email: str = "", role: str = "owner"):
        self.uid = uid
        self.email = email
        self.role = role

    @property
    def is_staff(self) -> bool:
        return self.role == "staff"

    def describe(self) -> str:
        """How this person is recorded on a call they placed."""
        return self.email or self.uid


async def current_member(
    tenant: CurrentTenant,
    x_user_token: Annotated[str | None, Header()] = None,
) -> Member:
    """Who is asking, within the company that `current_tenant` resolved.

    A token that does not verify, or that belongs to somebody outside this
    company, is treated as no token at all rather than as a failure: the
    company key is still perfectly good, and the only consequence is that the
    request is handled with the permissions that key already carries.
    """
    token = _bearer(x_user_token)
    if not token:
        return Member()
    try:
        claims = firebase.verify_id_token(token)
    except Exception:  # noqa: BLE001 — an unreadable token is simply no token
        return Member()

    uid = str(claims.get("uid") or "")
    email = str(claims.get("email") or "")
    if not uid:
        return Member()

    from ..repositories import users as user_repo

    link = await user_repo.get(uid)
    if not link or str(link.get("phoneNumberId") or "") != tenant.phone_number_id:
        # Signed in, but not as a member of the company this key names. Their
        # Firebase identity tells us nothing about what they may do here.
        return Member()
    return Member(uid=uid, email=email or str(link.get("email") or ""),
                  role=str(link.get("role") or "owner"))


CurrentMember = Annotated[Member, Depends(current_member)]


def refuse_staff(member: Member, what: str) -> None:
    """Stop here if this is an employee, saying who can do it instead."""
    if member.is_staff:
        raise Forbidden(
            f"Only an owner or admin can {what}. Ask whoever runs this account."
        )


class FirebaseIdentity:
    """A person, proven by Firebase, before anything is known about which
    company — if any — they belong to."""

    __slots__ = ("uid", "email", "display_name")

    def __init__(self, uid: str, email: str, display_name: str):
        self.uid = uid
        self.email = email
        self.display_name = display_name


async def current_person(
    authorization: Annotated[str | None, Header()] = None,
) -> FirebaseIdentity:
    """Who is signed in — a human, not a company.

    This is `/api/auth/*`'s own front door and nowhere else: every other
    route still identifies its caller by the company API key `current_tenant`
    resolves, so a browser that has signed in still needs the key `/auth/
    login` hands back before anything about calls, messages, or campaigns
    becomes reachable.
    """
    token = _bearer(authorization)
    if not token:
        raise Unauthorized("Present a Firebase ID token as a bearer token.")
    claims = firebase.verify_id_token(token)
    return FirebaseIdentity(
        uid=claims["uid"],
        email=claims.get("email", ""),
        display_name=claims.get("name", ""),
    )


CurrentPerson = Annotated[FirebaseIdentity, Depends(current_person)]
