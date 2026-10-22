"""
Taking only as many calls as the process can actually carry, and staying awake.

Both exist for the same reason: a call that is lost is lost silently. An
overloaded process does not refuse the eleventh call, it degrades all eleven,
and the complaint arrives as "the audio was broken" with nothing in the log. A
sleeping instance does not log the webhook it missed, because it was not
running to log it.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.services import keepalive
from app.services.agent import live


# --- how many calls at once ------------------------------------------------

@pytest.fixture
def no_sessions():
    """Start and end with an empty session table, whatever the test did."""
    live._sessions.clear()
    yield live._sessions
    live._sessions.clear()


def test_ten_calls_fit_and_the_eleventh_does_not(no_sessions, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_calls", 10)

    for i in range(9):
        no_sessions[f"call-{i}"] = object()
    assert live.active() == 9
    assert not live.at_capacity(), "nine of ten should still admit one more"

    no_sessions["call-9"] = object()
    assert live.active() == 10
    assert live.at_capacity(), "the tenth fills the last slot"


def test_a_refused_call_names_the_limit(no_sessions, monkeypatch):
    """The refusal has to be readable: it is what the operator sees."""
    monkeypatch.setattr(settings, "max_concurrent_calls", 2)
    no_sessions.update({"a": object(), "b": object()})

    with pytest.raises(live.AtCapacity) as raised:
        asyncio.run(live.start(_tenant(), _call("c"), lambda _frame: None))

    assert "2 calls at once" in str(raised.value)


def test_a_warmed_call_is_still_answered_when_full(fake_db, no_sessions, monkeypatch):
    """The cap must not drop a call it already accepted.

    A warmed session is already counted in the total. If `start` re-checked
    capacity for it, the call that was prepared early — the carrier leg
    connected, the caller on the line — would be the one refused.
    """
    monkeypatch.setattr(settings, "max_concurrent_calls", 1)
    sentinel = _FakeSession()
    no_sessions["warmed"] = sentinel
    assert live.at_capacity()

    session = asyncio.run(live.start(_tenant(), _call("warmed"), lambda _f: None))
    assert session is sentinel, "the warmed session should be handed the line"


def test_zero_means_no_limit(no_sessions, monkeypatch):
    """An operator with a bigger box should be able to turn the cap off."""
    monkeypatch.setattr(settings, "max_concurrent_calls", 0)
    for i in range(50):
        no_sessions[f"call-{i}"] = object()
    assert not live.at_capacity()


def test_warming_up_stops_at_the_limit(no_sessions, monkeypatch):
    """Warming is an optimisation and must not itself fill the last slot."""
    monkeypatch.setattr(settings, "max_concurrent_calls", 1)
    no_sessions["busy"] = object()

    asyncio.run(live.warm_up(_tenant(), _call("next")))
    assert "next" not in no_sessions, "warming should decline when full"


# --- staying awake ---------------------------------------------------------

def test_keepalive_is_off_without_a_public_url(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "")
    monkeypatch.setattr(settings, "keepalive_seconds", 600)
    assert not keepalive.should_run()
    assert keepalive.target() == ""


def test_keepalive_is_off_when_the_interval_is_zero(monkeypatch):
    """Zero is how a paid plan, which never sleeps, says it does not need this."""
    monkeypatch.setattr(settings, "public_base_url", "https://example.onrender.com")
    monkeypatch.setattr(settings, "keepalive_seconds", 0)
    assert not keepalive.should_run()


def test_keepalive_calls_the_public_url_not_localhost(monkeypatch):
    """A request to localhost never leaves the instance, so it keeps nothing awake."""
    monkeypatch.setattr(settings, "public_base_url", "https://example.onrender.com/")
    monkeypatch.setattr(settings, "keepalive_seconds", 600)
    assert keepalive.should_run()
    assert keepalive.target() == "https://example.onrender.com/health"


def test_a_failed_ping_is_not_fatal(monkeypatch):
    """The loop has to survive a blip; the next tick is only minutes away."""
    monkeypatch.setattr(settings, "public_base_url", "https://example.onrender.com")

    class Boom:
        async def get(self, _url, **_kw):
            raise RuntimeError("network down")

    assert asyncio.run(keepalive.ping(Boom())) is False


def test_a_non_200_counts_as_a_failed_ping(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.onrender.com")

    class Sad:
        async def get(self, _url, **_kw):
            return type("R", (), {"status_code": 502})()

    assert asyncio.run(keepalive.ping(Sad())) is False


def test_a_good_ping_reports_success(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://example.onrender.com")
    seen = {}

    class Fine:
        async def get(self, url, **_kw):
            seen["url"] = url
            return type("R", (), {"status_code": 200})()

    assert asyncio.run(keepalive.ping(Fine())) is True
    assert seen["url"] == "https://example.onrender.com/health"


def test_health_reports_headroom(client, no_sessions, monkeypatch):
    """Capacity is readable from outside, during the busy period, not after."""
    monkeypatch.setattr(settings, "max_concurrent_calls", 10)
    body = client.get("/health").json()["capabilities"]
    assert body["callCapacity"] == 10
    assert body["atCapacity"] is False
    assert body["callsInProgress"] == 0


# --- helpers ---------------------------------------------------------------

class _FakeSession:
    def attach_transport(self, _send) -> None:
        pass


def _tenant():
    from app.models.tenant import Tenant
    return Tenant(phoneNumberId="900", displayName="Test", recordCalls=False)


def _call(call_id: str):
    import time

    from app.models.call import Call, Channel, Direction
    return Call(
        id=call_id,
        tenantId="900",
        channel=Channel.PHONE,
        direction=Direction.INBOUND,
        fromNumber="+100",
        toNumber="+200",
        startedAt=time.time(),
    )
