"""
Onboarding and editing companies.

Admin-only, because creating a tenant means storing credentials that can send
messages as that business. The one exception is `GET /companies/me`, which a
company reads about itself with its own key.

The API key is returned exactly once, by the create call. It is sealed in the
row afterwards and there is no endpoint that reads it back — a key that can be
re-read is a key that leaks from a support ticket. Losing it means rotating it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status

from ...config import settings
from ...errors import Conflict, NotFound
from ...models.credentials import (
    CredentialsUpdate,
    channel_readiness,
    credential_status,
)
from ...models.tenant import Tenant, TenantCreate, TenantUpdate
from ...repositories import tenants as tenant_repo
from ..deps import AdminOnly, CurrentTenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("/me")
async def my_company(tenant: CurrentTenant) -> dict:
    """What this key belongs to. The onboarding screen's first request."""
    return {
        **tenant.public(),
        "credentials": credential_status(tenant),
        "channels": channel_readiness(tenant),
        # Where the company pastes this into their own Meta console. Showing it
        # here is the difference between a settings page and a support ticket.
        "webhookUrl": _webhook_url(),
    }


@router.patch("/me")
async def update_my_company(tenant: CurrentTenant, payload: CredentialsUpdate) -> dict:
    """The settings screen: a company connecting its own accounts.

    Each company brings its own Meta app and carrier account, so this is the
    route that makes onboarding self-service. Deliberately narrower than the
    admin PATCH: a company cannot move itself to another organisation, reissue
    its own API key, or change the phone number id it is keyed on — that last
    one would be a different company, not an edited one.
    """
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None:
            continue  # not mentioned; "" is how a field is cleared
        if hasattr(tenant, field):
            setattr(tenant, field, value)

    await tenant_repo.save(tenant)
    log.info(
        "company %s updated its settings (%s)",
        tenant.phone_number_id,
        ", ".join(sorted(changes)) or "nothing",
    )
    return {
        **tenant.public(),
        "credentials": credential_status(tenant),
        "channels": channel_readiness(tenant),
        "webhookUrl": _webhook_url(),
    }


@router.get("/me/channels")
async def my_channels(tenant: CurrentTenant) -> dict:
    """Which channels can carry traffic, and what each is still missing."""
    return {"channels": channel_readiness(tenant), "webhookUrl": _webhook_url()}


def _webhook_url() -> str:
    """The callback URL the company pastes into their Meta app.

    Empty when PUBLIC_BASE_URL is unset rather than a guess: a wrong URL here
    is pasted into Meta and then silently never delivers.
    """
    base = settings.public_base_url.strip().rstrip("/")
    return f"{base}/api/webhooks/whatsapp" if base else ""


@router.get("")
async def list_companies(_: AdminOnly, organizationId: str | None = None) -> dict:
    tenants = await tenant_repo.list_all(organizationId)
    return {"companies": [t.public() for t in tenants]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_company(_: AdminOnly, payload: TenantCreate) -> dict:
    existing = await tenant_repo.get(payload.phone_number_id)
    if existing is not None:
        raise Conflict(
            f"A company is already registered on phone number id "
            f"{payload.phone_number_id}."
        )

    api_key = tenant_repo.new_api_key()
    tenant = Tenant(
        **payload.model_dump(by_alias=True, exclude_none=True),
        apiKey=api_key,
    )
    await tenant_repo.save(tenant)
    log.info("onboarded company %s (%s)", tenant.name, tenant.phone_number_id)
    return {
        **tenant.public(),
        # Shown once. There is no endpoint that returns it again.
        "apiKey": api_key,
        "apiKeyNotice": "Store this now — it is not shown again.",
    }


@router.get("/{phone_number_id}")
async def get_company(_: AdminOnly, phone_number_id: str) -> dict:
    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")
    return tenant.public()


@router.patch("/{phone_number_id}")
async def update_company(
    _: AdminOnly, phone_number_id: str, payload: TenantUpdate
) -> dict:
    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")

    # exclude_unset is what makes this a patch: a field the caller did not
    # mention keeps its stored value instead of being blanked.
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        if hasattr(tenant, field):
            setattr(tenant, field, value)
    await tenant_repo.save(tenant)
    return tenant.public()


@router.post("/{phone_number_id}/rotate-key")
async def rotate_key(_: AdminOnly, phone_number_id: str) -> dict:
    """Issue a new API key. The previous one stops working immediately."""
    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")
    api_key = tenant_repo.new_api_key()
    tenant.api_key = api_key
    await tenant_repo.save(tenant)
    log.info("rotated the API key for %s", phone_number_id)
    return {"apiKey": api_key, "apiKeyNotice": "Store this now — it is not shown again."}


@router.delete(
    "/{phone_number_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    # A 204 carries no body, so FastAPI must not infer one from the
    # `-> None` annotation.
    response_model=None,
)
async def delete_company(_: AdminOnly, phone_number_id: str) -> None:
    """Remove the company's registration.

    Their call rows and stored audio are left alone: deleting a tenant is an
    account action, and erasing a year of recordings as a side effect of it is
    not something an operator can undo.
    """
    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")
    await tenant_repo.delete(phone_number_id)
    log.info("removed company %s", phone_number_id)
