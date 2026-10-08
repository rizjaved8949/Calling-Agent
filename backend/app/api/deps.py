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


def _bearer(value: str | None) -> str:
    if not value:
        return ""
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return value.strip()


async def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """The platform surface: creating and editing companies.

    A blank ADMIN_API_KEY disables these routes rather than leaving them open.
    An admin surface that is unauthenticated because nobody set a variable is
    the kind of default that ends up on the internet.
    """
    expected = settings.admin_api_key.strip()
    if not expected:
        raise Forbidden(
            "The admin API is disabled because ADMIN_API_KEY is not set on the server."
        )
    if not x_admin_key or not secrets.compare_digest(x_admin_key.strip(), expected):
        raise Unauthorized("A valid X-Admin-Key is required.")


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
    admin_expected = settings.admin_api_key.strip()
    if (
        admin_expected
        and x_admin_key
        and secrets.compare_digest(x_admin_key.strip(), admin_expected)
    ):
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
