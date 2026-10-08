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


# ---------------------------------------------------------------------------
# The platform owner's credentials are the platform owner's
# ---------------------------------------------------------------------------
#
# Each company brings its own Meta app and its own carrier account. The values
# in .env belong to the operator's own number. A customer must never ride on
# them: that would put their minutes on our bill, their caller ID on our
# number, and let any company holding our app secret forge another's webhooks.


def test_a_company_without_a_carrier_key_cannot_use_the_platforms(monkeypatch):
    from app.config import settings
    from app.models.tenant import Tenant
    from app.services.telephony import Infobip

    monkeypatch.setattr(settings, "infobip_api_key", "PLATFORM-OWNER-KEY")
    monkeypatch.setattr(settings, "infobip_base_url", "https://owner.api.infobip.com")

    carrier = Infobip(Tenant(phoneNumberId="customer-1"))
    assert carrier.configured is False
    assert carrier.api_key == ""


def test_a_company_uses_its_own_carrier_key(monkeypatch):
    from app.config import settings
    from app.models.tenant import Tenant
    from app.services.telephony import Infobip

    monkeypatch.setattr(settings, "infobip_api_key", "PLATFORM-OWNER-KEY")

    carrier = Infobip(
        Tenant(
            phoneNumberId="customer-1",
            infobipApiKey="THEIR-KEY",
            infobipBaseUrl="https://theirs.api.infobip.com",
        )
    )
    assert carrier.configured is True
    assert carrier.api_key == "THEIR-KEY"


def test_placing_a_call_without_carrier_credentials_is_refused():
    import asyncio

    from app.errors import AppError
    from app.models.tenant import Tenant
    from app.services.telephony import Infobip

    with pytest.raises(AppError) as raised:
        asyncio.run(Infobip(Tenant(phoneNumberId="customer-1")).place_call("+923001112222"))
    assert raised.value.code == "telephony_not_configured"


def test_the_platform_secret_does_not_validate_a_customers_webhook(monkeypatch):
    from app.config import settings
    from app.models.tenant import Tenant
    from app.services.whatsapp import signature_ok

    monkeypatch.setattr(settings, "meta_app_secret", "PLATFORM-OWNER-SECRET")

    body = b'{"entry":[]}'
    signed_with_ours = hmac.new(b"PLATFORM-OWNER-SECRET", body, hashlib.sha256).hexdigest()
    customer = Tenant(phoneNumberId="customer-1", appSecret="their-secret")

    assert not signature_ok(customer, body, f"sha256={signed_with_ours}")

    signed_with_theirs = hmac.new(b"their-secret", body, hashlib.sha256).hexdigest()
    assert signature_ok(customer, body, f"sha256={signed_with_theirs}")


# ---------------------------------------------------------------------------
# Rows written by the TypeScript voice service
# ---------------------------------------------------------------------------
#
# `voice_calls` already holds a hundred real calls in an older shape. A
# dashboard that silently shows none of them is worse than one that shows them
# imperfectly, so they are translated on read.

LEGACY_ROW = {
    "id": "793rl146ghb43o06ehc83n86jhd0nilc",
    "tenantId": "674871172379324",
    "metaCallId": "wacid.IhggMDBGMUEzNEEzNzI3NEQ4",
    "phoneNumber": "+923001112222",
    "direction": "INBOUND",
    "status": "COMPLETED",
    "mode": "agent",
    "outcome": "REMOTE_TERMINATED",
    "lastEvent": "call_ended",
    "durationSec": 18,
    "startedAt": "2026-09-14T21:29:57.707Z",
    "answeredAt": "2026-09-14T21:29:59.555Z",
    "endedAt": "2026-09-14T21:30:15.246Z",
    "recordingObject": "674871172379324/wacid.IhggMDBGMUEzNEEz",
    "recordingPath": "storage/voice/recordings/wacid.IhggMDBGMUEzNEE",
    "recordingMime": "audio/wav",
    "recordingScope": "two_way",
}


def _read(data):
    from app.repositories.calls import _call_from_row

    return _call_from_row({"id": data["id"], "tenant_id": data.get("tenantId"), "data": data})


def test_a_legacy_call_is_readable():
    call = _read(LEGACY_ROW)
    assert call is not None
    assert call.counterparty == "+923001112222"
    assert call.duration_seconds == 18
    assert call.provider_call_id == "wacid.IhggMDBGMUEzNEEzNzI3NEQ4"


def test_legacy_iso_timestamps_become_epoch_seconds():
    """JavaScript writes ISO with a trailing Z; this service keeps numbers."""
    call = _read(LEGACY_ROW)
    assert isinstance(call.started_at, float)
    assert call.answered_at and call.ended_at
    assert round(call.ended_at - call.answered_at) == 16


@pytest.mark.parametrize(
    "legacy,expected",
    [("COMPLETED", "COMPLETED"), ("CONNECTED", "IN_PROGRESS"),
     ("CONNECTING", "RINGING"), ("RINGING", "RINGING"), ("FAILED", "FAILED")],
)
def test_legacy_statuses_map(legacy, expected):
    call = _read({**LEGACY_ROW, "status": legacy})
    assert call.status.value == expected


def test_legacy_recording_points_at_the_object_not_the_display_path():
    """recordingPath is for a human; recordingObject is what storage can fetch."""
    call = _read(LEGACY_ROW)
    assert call.recording_path == "sb://674871172379324/wacid.IhggMDBGMUEzNEEz"
    assert call.recording_state.value == "READY"


def test_a_legacy_call_with_no_recording_reads_as_absent():
    row = {k: v for k, v in LEGACY_ROW.items() if k != "recordingObject"}
    call = _read(row)
    assert call.recording_state.value == "ABSENT"
    assert call.recording_path == ""


def test_an_outcome_is_not_an_error_on_a_call_that_completed():
    assert _read(LEGACY_ROW).error == ""
    assert _read({**LEGACY_ROW, "status": "FAILED"}).error == "REMOTE_TERMINATED"


def test_our_own_rows_are_not_mistaken_for_legacy_ones():
    from app.models.call import Call
    from app.repositories.calls import _is_legacy, _legacy_aliases

    call = Call(id="x", tenantId="1", counterparty="+9230011", durationSeconds=5)
    data = {**call.model_dump(by_alias=True, mode="json"), **_legacy_aliases(call)}
    assert not _is_legacy(data)
    assert _read({**data, "id": "x", "tenantId": "1"}).duration_seconds == 5


def test_we_write_the_aliases_the_older_service_reads():
    """Otherwise a call made here is invisible there — the mirror of this bug."""
    from app.models.call import Call
    from app.repositories.calls import _legacy_aliases

    call = Call(id="x", tenantId="1", counterparty="+9230011", durationSeconds=42,
                providerCallId="wacid.9", startedAt=1_760_000_000.0)
    aliases = _legacy_aliases(call)
    assert aliases["durationSec"] == 42
    assert aliases["phoneNumber"] == "+9230011"
    assert aliases["metaCallId"] == "wacid.9"
    assert aliases["createdAt"].endswith("Z")


# ---------------------------------------------------------------------------
# Supabase Storage headers
# ---------------------------------------------------------------------------
#
# Getting these wrong returns 404 on a file that exists, which sends you
# looking for a missing object rather than a missing header.


def test_a_legacy_jwt_key_is_sent_as_a_bearer_token(monkeypatch):
    from app.config import settings
    from app.db.supabase import SupabaseClient

    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "eyJhbGciOiJIUzI1NiJ9.aaa.bbb")
    headers = SupabaseClient().storage().headers
    assert headers["apikey"].startswith("eyJ")
    assert headers["authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb"


def test_a_new_secret_key_is_not_sent_as_a_bearer_token(monkeypatch):
    """Storage rejects a non-JWT there with "Invalid Compact JWS"."""
    from app.config import settings
    from app.db.supabase import SupabaseClient

    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "sb_secret_abcdef")
    headers = SupabaseClient().storage().headers
    assert headers["apikey"] == "sb_secret_abcdef"
    assert "authorization" not in headers


def test_a_missing_table_names_the_migration(monkeypatch):
    """PGRST205 means a migration was not applied, not that credentials are wrong."""
    import asyncio

    import httpx

    from app.config import settings
    from app.db.supabase import SupabaseClient
    from app.errors import UpstreamError

    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "eyJa.b.c")

    client = SupabaseClient()
    body = ('{"code":"PGRST205","message":"Could not find the table '
            '\'public.voice_messages\' in the schema cache"}')

    async def fake_request(*_args, **_kwargs):
        return httpx.Response(404, text=body, request=httpx.Request("GET", "https://x"))

    monkeypatch.setattr(client.rest(), "request", fake_request)

    with pytest.raises(UpstreamError) as raised:
        asyncio.run(client.select("voice_messages"))
    assert "voice_messages" in raised.value.message
    assert "migrations" in raised.value.message


def test_the_supabase_object_survives_a_rewrite():
    """Otherwise the first save of a legacy row hides it from the older dashboard."""
    from app.repositories.calls import _legacy_aliases

    call = _read(LEGACY_ROW)
    assert call.metadata["supabaseObject"] == "674871172379324/wacid.IhggMDBGMUEzNEEz"
    assert _legacy_aliases(call)["recordingObject"] == "674871172379324/wacid.IhggMDBGMUEzNEEz"


def test_the_backup_pointer_outlives_the_move_to_drive():
    """After the audio moves, Supabase is the backup — and still findable."""
    from app.repositories.calls import _legacy_aliases

    call = _read(LEGACY_ROW)
    call.recording_path = "gd://drive-file-id"
    aliases = _legacy_aliases(call)
    assert aliases["recordingObject"] == "674871172379324/wacid.IhggMDBGMUEzNEEz"


# ---------------------------------------------------------------------------
# Resampling between the model and the line
# ---------------------------------------------------------------------------
#
# Gemini speaks at 24 kHz; a phone line carries 16 kHz. Getting this wrong does
# not fail loudly — it shifts pitch, or clicks on every frame boundary, which
# only a human listening notices.


def _tone(seconds: float, rate: int, hz: float = 440.0) -> bytes:
    import math as _m
    import struct as _s

    n = int(seconds * rate)
    return _s.pack(
        f"<{n}h", *[int(12000 * _m.sin(2 * _m.pi * hz * i / rate)) for i in range(n)]
    )


def test_24k_becomes_16k_at_the_right_length():
    from app.services.agent.audio_rate import gemini_to_phone

    out = gemini_to_phone().process(_tone(1.0, 24_000))
    # 2:3 of one second at 24 kHz is one second at 16 kHz, within a few samples
    # of filter delay.
    assert abs(len(out) // 2 - 16_000) < 200


def test_frames_joined_across_calls_do_not_click():
    """State must carry over: a filter reset per frame is an audible edge."""
    import numpy as np

    from app.services.agent.audio_rate import gemini_to_phone

    source = _tone(0.5, 24_000)
    chunk = 960 * 2  # 20 ms at 24 kHz

    converter = gemini_to_phone()
    streamed = b"".join(
        converter.process(source[i : i + chunk]) for i in range(0, len(source), chunk)
    )
    whole = gemini_to_phone().process(source)

    a = np.frombuffer(streamed, dtype=np.int16).astype(float)
    b = np.frombuffer(whole, dtype=np.int16).astype(float)
    size = min(len(a), len(b))
    assert size > 1000
    # The same signal either way: streaming must not change the result.
    error = np.abs(a[:size] - b[:size]).max()
    assert error < 400, f"streaming differs from one-shot by {error}"


def test_the_tone_survives_at_the_right_pitch():
    """A phase bug shifts pitch, which no length check would catch."""
    import numpy as np

    from app.services.agent.audio_rate import gemini_to_phone

    out = gemini_to_phone().process(_tone(0.5, 24_000, hz=440.0))
    samples = np.frombuffer(out, dtype=np.int16).astype(float)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    peak_hz = np.fft.rfftfreq(len(samples), 1 / 16_000)[int(np.argmax(spectrum))]
    assert abs(peak_hz - 440.0) < 15, f"pitch moved to {peak_hz:.0f} Hz"


def test_empty_input_is_not_an_error():
    from app.services.agent.audio_rate import gemini_to_phone

    assert gemini_to_phone().process(b"") == b""


def test_a_phone_frame_is_twenty_milliseconds():
    from app.services.agent.audio_rate import FRAME_BYTES, PHONE_RATE, SILENT_FRAME

    assert FRAME_BYTES == 640
    assert FRAME_BYTES / 2 / PHONE_RATE == 0.02
    assert len(SILENT_FRAME) == FRAME_BYTES
