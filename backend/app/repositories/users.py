"""
The facts sign-in needs that Firebase itself does not hold: which company a
signed-in person belongs to, and what they may do there.

Firebase Authentication proves *who* someone is (a `uid`) and nothing about
*what they may do* here — that mapping lives in Firestore, in a `users`
collection keyed by `uid`, kept deliberately small. Everything about the
company itself (credentials, persona, call log) still lives in Supabase,
addressed by the `phone_number_id` this collection points at.

A second collection, `invites`, is how a second person joins a company that
already has one: the owner creates an invite, and whoever signs in with the
invited email and presents its token is linked with the role the owner chose.

Firestore's client is synchronous; every call here is pushed to a thread so
it does not block the event loop the way the rest of this backend's `await
httpx...` calls do not either.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from ..services.firebase import firestore_client

USERS = "users"
INVITES = "invites"


def _user_doc(uid: str):
    return firestore_client().collection(USERS).document(uid)


def _invite_doc(token: str):
    return firestore_client().collection(INVITES).document(token)


async def get(uid: str) -> dict[str, Any] | None:
    snapshot = await asyncio.to_thread(_user_doc(uid).get)
    return snapshot.to_dict() if snapshot.exists else None


async def create(
    uid: str, *, email: str, display_name: str, phone_number_id: str, role: str = "owner"
) -> None:
    """Link a Firebase user to a company — the owner at signup, or an invited
    person accepting an invite.

    `set` rather than an upsert that merges: a second link attempt for a
    `uid` that already belongs somewhere should fail loudly in the route
    above this, not quietly move them to a different company.
    """
    await asyncio.to_thread(
        _user_doc(uid).set,
        {
            "email": email,
            "displayName": display_name,
            "phoneNumberId": phone_number_id,
            "role": role,
            "createdAt": time.time(),
        },
    )


async def list_for_company(phone_number_id: str) -> list[dict[str, Any]]:
    """Everyone linked to a company — the team screen's one query.

    Firestore needs a composite index for `where + order_by` on different
    fields; sorted here instead; a company's team is small enough that this
    is not a performance question.
    """
    docs = await asyncio.to_thread(
        lambda: list(
            firestore_client()
            .collection(USERS)
            .where("phoneNumberId", "==", phone_number_id)
            .stream()
        )
    )
    people = [{"uid": d.id, **d.to_dict()} for d in docs]
    people.sort(key=lambda p: p.get("createdAt", 0))
    return people


async def remove(uid: str) -> None:
    """Unlink a person from their company.

    Does not touch their Firebase account — they can still sign in, they just
    land in `needs_signup` the same as anyone who has never joined a company,
    because nothing in Firestore points them anywhere any more.

    This does **not** revoke a copy of the company's API key they already
    hold: that key is shared by every signed-in person at the company (see
    `api/deps.py`), not issued per person, so removing someone from the team
    stops them signing in again but not a session already under way. Rotating
    the key (the existing admin `rotate-key` route) is the way to cut that
    off immediately — at the cost of every other signed-in person needing to
    sign in again too.
    """
    await asyncio.to_thread(_user_doc(uid).delete)


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------

def new_invite_token() -> str:
    return secrets.token_urlsafe(24)


async def create_invite(
    *, email: str, phone_number_id: str, role: str, invited_by: str
) -> str:
    token = new_invite_token()
    await asyncio.to_thread(
        _invite_doc(token).set,
        {
            "email": email.strip().lower(),
            "phoneNumberId": phone_number_id,
            "role": role,
            "invitedBy": invited_by,
            "status": "pending",
            "createdAt": time.time(),
        },
    )
    return token


async def get_invite(token: str) -> dict[str, Any] | None:
    snapshot = await asyncio.to_thread(_invite_doc(token).get)
    return snapshot.to_dict() if snapshot.exists else None


async def consume_invite(token: str) -> None:
    """Marks an invite spent, so the same link cannot link a second account."""
    await asyncio.to_thread(_invite_doc(token).update, {"status": "accepted"})


async def list_invites(phone_number_id: str) -> list[dict[str, Any]]:
    docs = await asyncio.to_thread(
        lambda: list(
            firestore_client()
            .collection(INVITES)
            .where("phoneNumberId", "==", phone_number_id)
            .where("status", "==", "pending")
            .stream()
        )
    )
    return [{"token": d.id, **d.to_dict()} for d in docs]


async def revoke_invite(token: str) -> None:
    await asyncio.to_thread(_invite_doc(token).delete)
