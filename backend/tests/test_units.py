"""
The pieces where being wrong is expensive and silent.

Each of these covers a failure that is either a security hole or a recording
that looks saved and is not — the two kinds of bug that do not announce
themselves in a dashboard.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest


# ---------------------------------------------------------------------------
# Credential sealing
# ---------------------------------------------------------------------------


def test_secret_round_trip():
    from app.security.secret_box import decrypt_secret, encrypt_secret, is_encrypted

    sealed = encrypt_secret("EAAG-super-secret-token")
    assert is_encrypted(sealed)
    assert "EAAG" not in sealed  # the plaintext must not survive in the row
    assert decrypt_secret(sealed) == "EAAG-super-secret-token"


def test_sealing_is_not_deterministic():
    """Two seals of the same token must differ, or the ciphertext leaks equality."""
    from app.security.secret_box import encrypt_secret

    assert encrypt_secret("same") != encrypt_secret("same")


def test_sealed_format_matches_secretbox_ts():
    """The wire format Conversation-Agent's secretBox.ts produces and reads."""
    from app.security.secret_box import encrypt_secret

    version, iv, tag, ciphertext = encrypt_secret("x").split(".")
    assert version == "v1"
    assert len(base64.b64decode(iv)) == 12      # GCM nonce
    assert len(base64.b64decode(tag)) == 16     # GCM tag
    assert base64.b64decode(ciphertext)


def test_plaintext_passes_through():
    """Rows written before encryption existed must still be readable."""
    from app.security.secret_box import decrypt_secret, is_encrypted

    assert not is_encrypted("plain-token")
    assert decrypt_secret("plain-token") == "plain-token"


def test_tampered_value_is_refused():
    from app.security.secret_box import SecretBoxError, decrypt_secret, encrypt_secret

    version, iv, tag, ciphertext = encrypt_secret("token").split(".")
    flipped = bytearray(base64.b64decode(ciphertext))
    flipped[0] ^= 0x01
    tampered = ".".join(
        [version, iv, tag, base64.b64encode(bytes(flipped)).decode()]
    )
    with pytest.raises(SecretBoxError):
        decrypt_secret(tampered)


# ---------------------------------------------------------------------------
# Webhook signatures
# ---------------------------------------------------------------------------


def _tenant(app_secret: str = "shhh"):
    from app.models.tenant import Tenant

    return Tenant(phoneNumberId="1", wabaId="w", accessToken="t", appSecret=app_secret)


def test_signature_accepts_a_real_delivery():
    from app.services.whatsapp import signature_ok

    body = b'{"entry":[]}'
    digest = hmac.new(b"shhh", body, hashlib.sha256).hexdigest()
    assert signature_ok(_tenant(), body, f"sha256={digest}")


def test_signature_rejects_a_forgery():
    from app.services.whatsapp import signature_ok

    body = b'{"entry":[]}'
    wrong = hmac.new(b"guess", body, hashlib.sha256).hexdigest()
    assert not signature_ok(_tenant(), body, f"sha256={wrong}")


def test_signature_rejects_a_modified_body():
    from app.services.whatsapp import signature_ok

    digest = hmac.new(b"shhh", b'{"entry":[]}', hashlib.sha256).hexdigest()
    assert not signature_ok(_tenant(), b'{"entry":[{"evil":1}]}', f"sha256={digest}")


def test_no_secret_means_refuse_not_allow(monkeypatch):
    """An unauthenticated webhook is not a degraded mode, it is an open door."""
    from app.config import settings
    from app.services.whatsapp import signature_ok

    monkeypatch.setattr(settings, "meta_app_secret", "")
    body = b"{}"
    digest = hmac.new(b"", body, hashlib.sha256).hexdigest()
    assert not signature_ok(_tenant(app_secret=""), body, f"sha256={digest}")


# ---------------------------------------------------------------------------
# Number normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+923191611020", "+923191611020"),
        ("03191611020", "+923191611020"),   # national trunk code -> country code
        ("00923191611020", "+923191611020"),
        ("923191611020", "+923191611020"),
        ("+92 319 161 1020", "+923191611020"),
        ("+92-319-161-1020", "+923191611020"),
    ],
)
def test_e164(raw, expected):
    from app.services.whatsapp import to_e164

    assert to_e164(raw, _tenant_with_country("92")) == expected


def _tenant_with_country(code: str):
    from app.models.tenant import Tenant

    return Tenant(phoneNumberId="1", defaultCountryCode=code)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "+1", "+1234567890123456789"])
def test_bad_numbers_are_refused(raw):
    from app.errors import AppError
    from app.services.whatsapp import to_e164

    with pytest.raises(AppError):
        to_e164(raw, _tenant_with_country("92"))


def test_national_number_without_a_country_code_is_refused():
    """Guessing a country here would dial a stranger."""
    from app.errors import AppError
    from app.services.whatsapp import to_e164

    with pytest.raises(AppError):
        to_e164("03191611020", _tenant_with_country(""))


def test_mask_keeps_only_the_last_four():
    from app.services.whatsapp import mask

    assert mask("+923191611020") == "…1020"
    assert "9231916" not in mask("+923191611020")


# ---------------------------------------------------------------------------
# Recording completeness — the check that stops a half-saved call
# ---------------------------------------------------------------------------


def test_short_download_is_incomplete():
    """A 4:18 call once stored as 1:05 because this check was not made."""
    from app.services.recordings import is_complete

    assert not is_complete("c1", b"x" * 1000, {"size": 100_000}, 0)


def test_full_download_is_complete():
    from app.services.recordings import is_complete

    assert is_complete("c1", b"x" * 100_000, {"size": 100_000}, 0)


def test_tolerates_container_padding():
    from app.services.recordings import is_complete

    assert is_complete("c1", b"x" * 99_000, {"size": 100_000}, 0)


def test_short_duration_is_incomplete():
    from app.services.recordings import is_complete

    assert not is_complete("c1", b"x", {"duration": 65}, 258)


def test_missing_metadata_is_accepted():
    """With nothing to check against, refusing would lose every recording."""
    from app.services.recordings import is_complete

    assert is_complete("c1", b"x" * 10, {}, 0)


# ---------------------------------------------------------------------------
# Infobip's recording shape
# ---------------------------------------------------------------------------


def test_composed_file_is_preferred_over_legs():
    """A single leg is one voice and silence where the other person spoke."""
    from app.services.telephony import recording_files

    payload = {
        "composedFiles": [{"id": "mixed"}],
        "callRecordings": [{"files": [{"id": "leg-a"}, {"id": "leg-b"}]}],
    }
    assert [f["id"] for f in recording_files(payload)] == ["mixed"]


def test_wrapped_results_shape_is_read():
    """Reading `payload["composedFiles"]` directly misses this one silently."""
    from app.services.telephony import recording_files

    payload = {"results": [{"composedFiles": [{"id": "mixed"}]}]}
    assert [f["id"] for f in recording_files(payload)] == ["mixed"]


def test_legs_are_used_when_there_is_no_composition():
    from app.services.telephony import recording_files

    payload = {"callRecordings": [{"files": [{"id": "leg-a"}, {"id": "leg-b"}]}]}
    assert len(recording_files(payload)) == 2


def test_empty_payload_yields_nothing():
    from app.services.telephony import recording_files

    assert recording_files({}) == []
    assert recording_files(None) == []


# ---------------------------------------------------------------------------
# Storage references
# ---------------------------------------------------------------------------


def test_reference_schemes_are_distinguishable():
    """A Drive id must never be mistaken for a bucket key or a local path."""
    from app.services.storage import describe

    assert describe("gd://abc123") == "google-drive"
    assert describe("sb://tenants/1/calls/x.mp3") == "supabase"
    assert describe("data/recordings/1/x.mp3") == "local-disk"
    assert describe("") == "none"


def test_extension_matches_the_bytes():
    """Naming a webm file .wav gives a download the player refuses to open."""
    from app.services.audio import extension_for

    assert extension_for("audio/webm;codecs=opus") == "webm"
    assert extension_for("audio/mpeg") == "mp3"
    assert extension_for("audio/ogg") == "ogg"
    assert extension_for("audio/wav") == "wav"
    assert extension_for("") == "wav"


# ---------------------------------------------------------------------------
# The OAuth state parameter
# ---------------------------------------------------------------------------


def test_state_round_trip():
    from app.services.google_oauth import make_state, read_state

    state = make_state("tenant-9", return_to="https://app.example/settings")
    body = read_state(state)
    assert body["t"] == "tenant-9"
    assert body["r"] == "https://app.example/settings"


def test_forged_state_is_refused():
    """Without this, anyone could attach their Drive to a company they do not own."""
    from app.errors import AppError
    from app.services.google_oauth import read_state

    payload = base64.urlsafe_b64encode(
        json.dumps({"t": "someone-elses-company", "x": 9_999_999_999}).encode()
    ).decode().rstrip("=")
    with pytest.raises(AppError):
        read_state(f"{payload}.AAAA")


def test_expired_state_is_refused(monkeypatch):
    import app.services.google_oauth as oauth
    from app.errors import AppError

    state = oauth.make_state("tenant-9")
    monkeypatch.setattr(oauth.time, "time", lambda: 9_999_999_999)
    with pytest.raises(AppError):
        oauth.read_state(state)
