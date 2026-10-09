"""
Super admin sign-in: one-time setup, login, forgot password.

See `security/superadmin.py`. These are reachable without a key on purpose —
they are how a key-less browser becomes the operator — and sit behind the
stricter rate limit, since a password form is exactly what gets hammered.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, Field

from ...errors import Unauthorized
from ...security import superadmin

router = APIRouter(prefix="/platform", tags=["platform"])


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
