"""
The one fact sign-in needs that Firebase itself does not hold: which company
a signed-in person belongs to.

Firebase Authentication proves *who* someone is (a `uid`) and nothing about
*what they may do* here — that mapping lives in Firestore, in a `users`
collection keyed by `uid`, kept deliberately small. Everything about the
company itself (credentials, persona, call log) still lives in Supabase,
addressed by the `phone_number_id` this collection points at.

Firestore's client is synchronous; every call here is pushed to a thread so
it does not block the event loop the way the rest of this backend's `await
httpx...` calls do not either.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..services.firebase import firestore_client

COLLECTION = "users"


def _doc(uid: str):
    return firestore_client().collection(COLLECTION).document(uid)


async def get(uid: str) -> dict[str, Any] | None:
    snapshot = await asyncio.to_thread(_doc(uid).get)
    return snapshot.to_dict() if snapshot.exists else None


async def create(uid: str, *, email: str, display_name: str, phone_number_id: str) -> None:
    """Link a brand-new Firebase user to the company just created for them.

    `set` rather than an upsert that merges: a second signup attempt for a
    `uid` that already has a company should fail loudly in the route above
    this, not quietly overwrite which company they belong to.
    """
    await asyncio.to_thread(
        _doc(uid).set,
        {
            "email": email,
            "displayName": display_name,
            "phoneNumberId": phone_number_id,
            "createdAt": time.time(),
        },
    )
