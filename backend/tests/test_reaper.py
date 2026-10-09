"""
Calls the provider never reported the end of.

One unsettled row made a number unreachable for an afternoon: WhatsApp refuses
a second call to somebody it believes is already on one. An account was found
with eight open calls, the oldest twenty-seven days old.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.models.call import Call, CallStatus, Channel, RecordingState
from app.services import reaper


def _save(call: Call) -> None:
    from app.repositories import calls as call_repo
    asyncio.run(call_repo.save_call(call))


def _get(tenant_id: str, call_id: str) -> Call:
    from app.repositories import calls as call_repo
    return asyncio.run(call_repo.get_call(tenant_id, call_id))


def test_an_abandoned_call_is_settled(client, tenant_factory):
    tenant, _ = tenant_factory("900")
    old = time.time() - 3 * 60 * 60
    _save(Call(id="abandoned", tenantId="900", channel=Channel.PHONE,
               status=CallStatus.IN_PROGRESS, startedAt=old, answeredAt=old + 2,
               recordingState=RecordingState.PENDING, counterparty="+92300"))

    assert asyncio.run(reaper.sweep_tenant(tenant)) == 1
    settled = _get("900", "abandoned")
    assert settled.status is CallStatus.COMPLETED
    assert settled.duration_seconds > 0
    assert settled.ended_at is not None
    # Audio that has not arrived in hours is not going to.
    assert settled.recording_state is RecordingState.ABSENT
    assert "never reported" in settled.error


def test_a_call_nobody_answered_is_not_called_completed(client, tenant_factory):
    tenant, _ = tenant_factory("901")
    _save(Call(id="never-answered", tenantId="901", channel=Channel.PHONE,
               status=CallStatus.RINGING, startedAt=time.time() - 3 * 60 * 60,
               counterparty="+92300"))
    asyncio.run(reaper.sweep_tenant(tenant))
    assert _get("901", "never-answered").status is CallStatus.NO_ANSWER


def test_a_recent_call_is_left_alone(client, tenant_factory):
    """The grace period is what keeps this from cutting a long conversation."""
    tenant, _ = tenant_factory("902")
    _save(Call(id="recent", tenantId="902", channel=Channel.PHONE,
               status=CallStatus.IN_PROGRESS, startedAt=time.time() - 60,
               counterparty="+92300"))
    assert asyncio.run(reaper.sweep_tenant(tenant)) == 0
    assert _get("902", "recent").status is CallStatus.IN_PROGRESS


def test_a_call_with_audio_still_moving_is_never_cut(client, tenant_factory, monkeypatch):
    """A real call always has a live session, however long it runs."""
    from app.services.agent import live

    tenant, _ = tenant_factory("903")
    _save(Call(id="long-but-real", tenantId="903", channel=Channel.PHONE,
               status=CallStatus.IN_PROGRESS, startedAt=time.time() - 5 * 60 * 60,
               counterparty="+92300"))
    monkeypatch.setattr(live, "get", lambda call_id: object())

    assert asyncio.run(reaper.sweep_tenant(tenant)) == 0
    assert _get("903", "long-but-real").status is CallStatus.IN_PROGRESS


def test_a_finished_call_is_not_touched_again(client, tenant_factory):
    tenant, _ = tenant_factory("904")
    _save(Call(id="done", tenantId="904", channel=Channel.PHONE,
               status=CallStatus.COMPLETED, startedAt=time.time() - 9 * 60 * 60,
               endedAt=time.time() - 8 * 60 * 60, durationSeconds=42,
               counterparty="+92300"))
    assert asyncio.run(reaper.sweep_tenant(tenant)) == 0
    assert _get("904", "done").duration_seconds == 42


def test_one_unreadable_company_does_not_stop_the_sweep(client, tenant_factory, monkeypatch):
    tenant, _ = tenant_factory("905")
    from app.repositories import calls as call_repo

    async def refuse(*a, **kw):
        raise RuntimeError("database having a moment")

    monkeypatch.setattr(call_repo, "list_calls", refuse)
    assert asyncio.run(reaper.sweep_tenant(tenant)) == 0
