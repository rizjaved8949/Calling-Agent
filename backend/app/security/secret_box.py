"""
Encryption for credentials held at rest.

A tenant's WhatsApp access token and app secret can send messages and place
calls as that business. Once they move out of the environment and into a
database row, anything that can read the row — a leaked service key, a backup,
a support engineer running a query — can impersonate the customer. Sealing the
value before the write means the row alone is not enough.

AES-256-GCM, because it authenticates as well as encrypts: a tampered
ciphertext fails to open rather than decrypting to something unintended.

**Wire-compatible with Conversation-Agent's `voice/secretBox.ts`** — same
format, same KDF, same salt — so a credential written by the TypeScript service
opens here and vice versa. Do not change the salt or the scrypt parameters
without changing both sides together.

Format: ``v1.<iv-b64>.<tag-b64>.<ciphertext-b64>``. The version prefix is what
lets plaintext rows written before this existed still be read, and lets the
scheme change later without a flag day.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from ..config import settings

log = logging.getLogger(__name__)

PREFIX = "v1"
# A fixed salt, so the same secret yields the same key across restarts and
# across replicas. A random salt would make every instance unable to read what
# the others wrote.
_SALT = b"conversaigent.tenant.v1"
# Node's crypto.scryptSync defaults. Changing any of these breaks interop.
_N, _R, _P, _DKLEN = 16384, 8, 1, 32

_key_cache: bytes | None = None
_warned = False


class SecretBoxError(RuntimeError):
    """A sealed value could not be opened."""


def encryption_configured() -> bool:
    return bool(settings.credentials_secret.strip())


def _key() -> bytes | None:
    """The derived key, or None when no secret is configured.

    scrypt is deliberately slow, and the secret never changes at runtime, so
    the result is cached for the life of the process.
    """
    global _key_cache
    secret = settings.credentials_secret.strip()
    if not secret:
        return None
    if _key_cache is None:
        _key_cache = hashlib.scrypt(
            secret.encode("utf-8"), salt=_SALT, n=_N, r=_R, p=_P, dklen=_DKLEN,
            maxmem=64 * 1024 * 1024,
        )
    return _key_cache


def is_encrypted(value: object) -> bool:
    """True for a value this module (or secretBox.ts) produced."""
    return isinstance(value, str) and value.startswith(f"{PREFIX}.")


def encrypt_secret(plain: str) -> str:
    """Seal a secret.

    Returns the input unchanged when no key is configured, so a deployment
    without CREDENTIALS_SECRET still works — less safely, and the warning says
    so once rather than on every write.
    """
    global _warned
    if not plain:
        return plain
    if is_encrypted(plain):
        return plain  # already sealed; do not double-wrap
    key = _key()
    if key is None:
        if not _warned:
            _warned = True
            log.warning(
                "CREDENTIALS_SECRET is not set — tenant credentials are being stored "
                "in plain text. Set it before onboarding real customers."
            )
        return plain

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # local: optional dep

    iv = _random(12)
    sealed = AESGCM(key).encrypt(iv, plain.encode("utf-8"), None)
    # AESGCM appends the 16-byte tag; the TS side keeps them in separate fields.
    ciphertext, tag = sealed[:-16], sealed[-16:]
    return ".".join(
        (PREFIX, _b64(iv), _b64(tag), _b64(ciphertext))
    )


def decrypt_secret(value: str) -> str:
    """Open a value produced by `encrypt_secret` or by secretBox.ts.

    A value that was never encrypted is returned as-is, which is what lets rows
    written before the key existed keep working.
    """
    if not is_encrypted(value):
        return value
    key = _key()
    if key is None:
        raise SecretBoxError(
            "This credential is encrypted but CREDENTIALS_SECRET is not set."
        )
    try:
        _, iv_b64, tag_b64, ct_b64 = value.split(".", 3)
    except ValueError as exc:
        raise SecretBoxError("The stored credential is malformed.") from exc

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # local: optional dep
    from cryptography.exceptions import InvalidTag

    try:
        opened = AESGCM(key).decrypt(
            base64.b64decode(iv_b64),
            base64.b64decode(ct_b64) + base64.b64decode(tag_b64),
            None,
        )
    except (InvalidTag, ValueError) as exc:
        # Either the wrong key, or the row was altered. Both are refusals, not
        # a value to fall back on: returning the ciphertext would mean sending
        # garbage to Meta as if it were a token.
        raise SecretBoxError(
            "The stored credential could not be decrypted — wrong CREDENTIALS_SECRET, "
            "or the row was modified."
        ) from exc
    return opened.decode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _random(size: int) -> bytes:
    return os.urandom(size)
