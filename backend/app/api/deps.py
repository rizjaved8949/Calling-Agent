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
    return tenant


CurrentTenant = Annotated[Tenant, Depends(current_tenant)]
AdminOnly = Annotated[None, Depends(require_admin)]


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
