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
from ...errors import AppError, Conflict, NotFound
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
    fields = payload.model_dump(by_alias=True, exclude_none=True)
    owner_email = str(fields.pop("ownerEmail", "") or "").strip().lower()
    owner_password = str(fields.pop("ownerPassword", "") or "")
    owner_name = str(fields.pop("ownerName", "") or "").strip()

    tenant = Tenant(**fields, apiKey=api_key)
    await tenant_repo.save(tenant)
    log.info("onboarded company %s (%s)", tenant.name, tenant.phone_number_id)

    owner: dict[str, str] | None = None
    if owner_email:
        try:
            owner = await _create_owner(
                tenant, email=owner_email, password=owner_password, name=owner_name
            )
        except AppError:
            # The company exists but nobody can sign in, which is worse than
            # not having created it: the operator would have to find and
            # delete the row before trying again.
            await tenant_repo.delete(tenant.phone_number_id)
            raise

    return {
        **tenant.public(),
        # Shown once. There is no endpoint that returns it again.
        "apiKey": api_key,
        "apiKeyNotice": "Store this now — it is not shown again.",
        "owner": owner,
    }


async def _create_owner(
    tenant: Tenant, *, email: str, password: str, name: str
) -> dict[str, str]:
    """Give the new company somebody who can sign in.

    A sign-in needs Firebase, which this deployment may not have configured —
    and a company registered with an owner nobody can log in as is a trap, so
    that is refused rather than half-done.
    """
    from firebase_admin import auth as fb_auth

    from ...repositories import users as user_repo
    from ...services import firebase

    if "@" not in email:
        raise AppError(422, "Enter a valid email address for the owner.", code="bad_email")
    if len(password) < 8:
        raise AppError(
            422, "Give the owner a password of at least 8 characters.", code="weak_password"
        )
    if not firebase.configured():
        raise AppError(
            503,
            "Sign-in is not configured on this server, so an owner cannot be "
            "created. Register the company without one and invite them later.",
            code="no_sign_in",
        )

    app = firebase._app()
    try:
        existing = fb_auth.get_user_by_email(email, app=app)
    except fb_auth.UserNotFoundError:
        existing = None
    if existing is not None:
        if await user_repo.get(existing.uid) is not None:
            raise AppError(
                409,
                f"{email} already belongs to a company. Use a different address.",
                code="email_taken",
            )
        user = existing
        fb_auth.update_user(user.uid, password=password, app=app)
    else:
        user = fb_auth.create_user(
            email=email, password=password, display_name=name or email,
            email_verified=True, app=app,
        )

    await user_repo.create(
        user.uid, email=email, display_name=name or email,
        phone_number_id=tenant.phone_number_id, role="owner",
    )
    log.info("company %s: owner %s can sign in", tenant.phone_number_id, email)
    return {"email": email, "role": "owner"}


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

    The support conversation is the exception, and goes. It is a thread with
    *us*, not a record of the company's own business, and the photos and
    videos uploaded into it would otherwise sit in the bucket forever belonging
    to nobody — invisible, because the operator's thread list is drawn from the
    companies that exist.
    """
    tenant = await tenant_repo.get(phone_number_id)
    if tenant is None:
        raise NotFound("Company")
    await _purge_support(phone_number_id)
    await tenant_repo.delete(phone_number_id)
    log.info("removed company %s", phone_number_id)


async def _purge_support(phone_number_id: str) -> None:
    """Erase the support thread and anything uploaded into it."""
    from ...repositories import support as support_repo
    from ...services import storage

    for message in await support_repo.history(phone_number_id, limit=500):
        if message.attachment and message.attachment.reference:
            await storage.delete_object(message.attachment.reference)
    await support_repo.purge(phone_number_id)
