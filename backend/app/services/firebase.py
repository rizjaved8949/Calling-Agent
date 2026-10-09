"""
Who a human is — as distinct from which company is calling.

Everywhere else in this backend, "who is this request for" is answered by a
tenant's own API key (`api/deps.py`). That answers a different question than
sign-in does: a key says which *company*'s data a request may touch; it says
nothing about which *person* is sitting at the keyboard, because the key is
the same for everyone at that company. Firebase Authentication is the layer
that answers the second question — a signed-in browser presents an ID token,
this module verifies it was really issued by this project and has not
expired, and hands back the one thing that matters afterwards: the person's
Firebase `uid`.

What a `uid` is allowed to do — which company it belongs to, whether it is
that company's owner — lives in Firestore, in `app/repositories/users.py`,
not here. This module only answers "is this really them."
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials, firestore

from ..config import settings
from ..errors import Unauthorized

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _app() -> firebase_admin.App | None:
    """The initialised Firebase app, or None if no credential is configured.

    Cached rather than built per-request: initialising talks to Google to
    validate the key, which is wasted work on every single sign-in.
    """
    if settings.firebase_service_account_json.strip():
        try:
            info = json.loads(settings.firebase_service_account_json)
        except json.JSONDecodeError:
            log.error("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON; sign-in is disabled.")
            return None
        cred = credentials.Certificate(info)
    elif settings.firebase_service_account_file is not None:
        cred = credentials.Certificate(str(settings.firebase_service_account_file))
    else:
        return None
    return firebase_admin.initialize_app(cred)


def configured() -> bool:
    return _app() is not None


def verify_id_token(token: str) -> dict:
    """The decoded token's claims, once Firebase confirms it issued it.

    Raises `Unauthorized` for anything wrong with it — expired, forged, from
    a different project, or sign-in simply not configured on this
    deployment — so a route can treat every failure mode the same way: the
    caller is not who they say they are.
    """
    app = _app()
    if app is None:
        raise Unauthorized(
            "Sign-in is not configured on this deployment. Set "
            "FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT_PATH."
        )
    try:
        return firebase_auth.verify_id_token(token, app=app)
    except Exception as exc:  # noqa: BLE001 — every failure reaches the caller the same way
        log.info("rejected a Firebase ID token: %s", exc)
        raise Unauthorized("Your sign-in has expired. Please sign in again.") from None


@lru_cache(maxsize=1)
def firestore_client():
    """A Firestore client bound to the same project as sign-in.

    One Firebase project serves both Authentication and this Firestore
    database, so there is nothing further to configure once `_app()` exists.
    """
    app = _app()
    if app is None:
        raise Unauthorized("Sign-in is not configured on this deployment.")
    return firestore.client(app)
