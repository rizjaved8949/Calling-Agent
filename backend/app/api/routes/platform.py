"""
Super admin sign-in: one-time setup, login, forgot password.

See `security/superadmin.py`. These are reachable without a key on purpose —
they are how a key-less browser becomes the operator — and sit behind the
stricter rate limit, since a password form is exactly what gets hammered.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, Field

from ...errors import AppError, NotFound, Unauthorized
from ...security import superadmin
from ..deps import AdminOnly

log = logging.getLogger(__name__)

# Two routers: the password forms get the tight rate limit (they are what
# gets guessed at), while the portal's own read screens get the ordinary one —
# 20 requests a minute is not enough for somebody clicking through companies.
router = APIRouter(prefix="/platform", tags=["platform"])
data_router = APIRouter(prefix="/platform", tags=["platform"])


class SetupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    recoveryCode: str = Field(min_length=1, max_length=400)
    newPassword: str = Field(min_length=1, max_length=200)


class ChangeRequest(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=200)
    newPassword: str = Field(min_length=1, max_length=200)


@router.get("/status")
async def platform_status() -> dict:
    """Whether the one-time setup has happened — decides which form to show."""
    return {"superAdminExists": await superadmin.exists()}


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(payload: SetupRequest) -> dict:
    return await superadmin.create(payload.email, payload.password, payload.name)


@router.post("/login")
async def login(payload: LoginRequest) -> dict:
    return await superadmin.login(payload.email, payload.password)


@router.post("/forgot-password")
async def forgot_password(payload: ResetRequest) -> dict:
    return await superadmin.reset(payload.email, payload.recoveryCode, payload.newPassword)


@router.post("/change-password")
async def change_password(
    payload: ChangeRequest, x_admin_key: str | None = Header(default=None),
) -> dict:
    if not x_admin_key or not await superadmin.token_valid(x_admin_key.strip()):
        raise Unauthorized("Sign in as the super admin first.")
    return await superadmin.change_password(payload.currentPassword, payload.newPassword)


# ---------------------------------------------------------------------------
# What the operator's portal shows
# ---------------------------------------------------------------------------


@data_router.get("/overview")
async def overview(_: AdminOnly) -> dict:
    """Every company, with enough about each to know who needs help.

    One request rather than one per company: the portal's first screen is a
    list, and a list that fires N requests is a list that half-loads.
    """
    from ...db.supabase import supabase
    from ...models.call import CallStatus, RecordingState
    from ...models.number import NumberStatus
    from ...repositories import agents as agent_repo
    from ...repositories import calls as call_repo
    from ...repositories import numbers as number_repo
    from ...repositories import tenants as tenant_repo

    tenants = await tenant_repo.list_all()
    all_numbers = await number_repo.list_all()
    companies = []
    totals = {"companies": len(tenants), "numbers": 0, "verified": 0, "calls": 0,
              "minutes": 0, "recordings": 0, "failed": 0}

    for tenant in tenants:
        mine = [n for n in all_numbers if n.tenant_id == tenant.phone_number_id]
        verified = [n for n in mine if n.status is NumberStatus.VERIFIED]
        agents = await agent_repo.list_agents(tenant.phone_number_id)
        calls = await call_repo.list_calls(tenant.phone_number_id, limit=1000)
        minutes = int(-(-sum(c.duration_seconds for c in calls) // 60))
        failed = sum(1 for c in calls if c.status is CallStatus.FAILED)
        recordings = sum(1 for c in calls if c.recording_state is RecordingState.READY)
        assigned = [n for n in mine if n.inbound_agent_id or n.outbound_agent_id]

        # Said plainly, because "what is wrong with this account" is the only
        # question this screen exists to answer.
        if not mine:
            stage, blocker = "no numbers", "They have not connected a number yet."
        elif not verified:
            stage, blocker = "not verified", "Their credentials were refused by the provider."
        elif not agents:
            stage, blocker = "no agent", "Verified number, but no agent has been created."
        elif not assigned:
            stage, blocker = "not assigned", "An agent exists but no number points at it."
        else:
            stage, blocker = "live", ""

        companies.append({
            "id": tenant.phone_number_id,
            "name": tenant.name,
            "stage": stage,
            "blocker": blocker or None,
            "numbers": len(mine),
            "verifiedNumbers": len(verified),
            "agents": len(agents),
            "calls": len(calls),
            "minutes": minutes,
            "failedCalls": failed,
            "recordings": recordings,
            "lastCallAt": max((c.started_at for c in calls), default=None),
            "driveConnected": tenant.google_drive.connected,
            "allowPlatformCredentials": tenant.allow_platform_credentials,
        })
        totals["numbers"] += len(mine)
        totals["verified"] += len(verified)
        totals["calls"] += len(calls)
        totals["minutes"] += minutes
        totals["recordings"] += recordings
        totals["failed"] += failed

    companies.sort(key=lambda c: (c["stage"] == "live", -(c["lastCallAt"] or 0)))
    return {
        "companies": companies,
        "totals": totals,
        "store": "supabase" if not getattr(supabase, "is_local", False) else "local file",
    }


@data_router.get("/companies/{company_id}")
async def company_detail(_: AdminOnly, company_id: str) -> dict:
    """One company, in full: numbers, agents, recent calls, usage."""
    from ...models.credentials import channel_readiness, credential_status
    from ...repositories import agents as agent_repo
    from ...repositories import calls as call_repo
    from ...repositories import knowledge as knowledge_repo
    from ...repositories import numbers as number_repo
    from ...repositories import tenants as tenant_repo
    from ...services import lines

    tenant = await tenant_repo.get(company_id)
    if tenant is None:
        raise NotFound("Company")
    numbers = await number_repo.list_for(company_id)
    agents = await agent_repo.list_agents(company_id)
    bases = await agent_repo.list_knowledge_bases(company_id)
    documents = await knowledge_repo.listing(company_id)
    calls = await call_repo.list_calls(company_id, limit=25)
    stats = await call_repo.call_stats(company_id)
    return {
        "company": {**tenant.public(), "credentials": credential_status(tenant),
                    "channels": channel_readiness(tenant)},
        "numbers": [n.public(base_url=lines.webhook_base()) for n in numbers],
        "agents": [a.public() for a in agents],
        "knowledgeBases": [k.public() for k in bases],
        "documentCount": len(documents),
        "recentCalls": [c.public() for c in calls],
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# The speech engine, changeable without a redeploy
# ---------------------------------------------------------------------------


class EngineUpdate(BaseModel):
    model_config = {"populate_by_name": True, "extra": "forbid"}

    engine: str = Field(min_length=1, max_length=40)
    model: str = Field(default="", max_length=120)
    # None means "not mentioned", which keeps the stored key: the screen never
    # shows a key back, so a blank field cannot be read as "clear it". An empty
    # string is deliberate and does clear it.
    apiKey: str | None = None


@data_router.get("/engine")
async def read_engine(_: AdminOnly) -> dict:
    from ...services import platform_settings

    return await platform_settings.public()


@data_router.put("/engine")
async def set_engine(_: AdminOnly, payload: EngineUpdate) -> dict:
    """Choose the speech engine and the key it runs on.

    Refuses an engine this build cannot speak: storing one would mean every
    call failing with nothing on screen to say why.
    """
    from ...services import platform_settings

    engine = payload.engine.strip().lower()
    known = {e["id"] for e in platform_settings.ENGINES}
    if engine not in known:
        raise AppError(422, f"{payload.engine!r} is not an engine this platform knows.",
                       code="unknown_engine")
    if engine not in platform_settings.SUPPORTED:
        raise AppError(
            422,
            f"{payload.engine} is not implemented in this build yet, so calls "
            "would connect to silence. Leave the engine on Gemini.",
            code="engine_not_supported",
        )
    return await platform_settings.save(
        engine=engine, model=payload.model, api_key=payload.apiKey
    )


# ---------------------------------------------------------------------------
# Switching a company off, and back on
# ---------------------------------------------------------------------------


class SuspendRequest(BaseModel):
    model_config = {"extra": "forbid"}

    suspended: bool
    reason: str = Field(default="", max_length=300)


@data_router.post("/companies/{company_id}/suspend")
async def suspend_company(_: AdminOnly, company_id: str, payload: SuspendRequest) -> dict:
    """Switch a company off, or back on.

    Not a deletion: every call, recording, number and document is kept, and
    one click undoes it. While it is off nobody at that company can sign in or
    reach the API, and their numbers answer nothing.
    """
    from ...repositories import tenants as tenant_repo

    tenant = await tenant_repo.get(company_id)
    if tenant is None:
        raise NotFound("Company")
    tenant.suspended = payload.suspended
    tenant.suspended_reason = payload.reason.strip() if payload.suspended else ""
    await tenant_repo.save(tenant)
    log.info("company %s %s", company_id, "suspended" if payload.suspended else "reactivated")
    return {"id": company_id, "suspended": tenant.suspended,
            "suspendedReason": tenant.suspended_reason or None}
