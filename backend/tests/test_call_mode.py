"""
The dialer's two options, kept apart: "human" connects the employee, "ai" the agent.

The bug these pin: the repository writes a legacy `mode` alias (who handled
the call) over the canonical fields on every save, and `Call.mode` used the
same key. A dialer call saved as "human" read back as "agent", and since the
live call is always built from the saved row, the AI answered calls that
employees had placed to make themselves.

So these go the long way round, the way production does: a call placed
through the API, saved, then read back by the carrier's audio socket. The
Twilio pair is in test_twilio.py, beside the fixtures it needs.
"""
from __future__ import annotations

import time

import pytest


def _wait_for_session(call_id: str, timeout: float = 3.0):
    """The live session once the socket handler has started it."""
    from app.services.agent import live

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = live.get(call_id)
        if session is not None:
            return session
        time.sleep(0.01)
    raise AssertionError("no live session was started for the call")


@pytest.fixture
def agent_starts(monkeypatch):
    """The model is never reached; this records which sessions asked for it."""
    from app.services.agent.session import CallSession

    started: list[str] = []

    async def start_agent(self):
        started.append(self.call_id)

    monkeypatch.setattr(CallSession, "_start_agent", start_agent)
    return started


@pytest.fixture
def infobip_dialer(client, tenant_factory, auth, monkeypatch, fake_db):
    """A signed-in company on its own Infobip credentials, with the carrier faked."""
    from app.services import carriers
    from app.services.agent import claims

    claims._waiting.clear()
    placed: list[str] = []
    hung_up: list[str] = []

    class FakeInfobip:
        configured = True

        def __init__(self, _tenant):
            pass

        async def place_call(self, to, **_kw):
            placed.append(to)
            return {"id": f"ib-{len(placed)}"}

        async def hangup(self, provider_call_id):
            hung_up.append(provider_call_id)

    monkeypatch.setattr(carriers, "carrier_for", FakeInfobip)
    _tenant, key = tenant_factory("700", "Acme", infobipApiKey="k",
                                  infobipBaseUrl="https://x", infobipPhoneNumber="+9251")
    yield {"headers": auth(key), "placed": placed, "hung_up": hung_up}
    claims._waiting.clear()


def _dial(client, headers, *, human: bool):
    from app.repositories import calls as call_repo
    import asyncio

    resp = client.post("/api/calls", json={"to": "+923001112222", "human": human},
                       headers=headers)
    assert resp.status_code == 201, resp.text
    assert resp.json()["mode"] == ("human" if human else "ai")
    return asyncio.run(call_repo.get_call("700", resp.json()["id"]))


# ---------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["human", "ai"])
async def test_the_mode_survives_being_saved(fake_db, mode):
    from app.models.call import Call, Channel, Direction
    from app.repositories import calls as call_repo

    call = Call(tenantId="t", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                counterparty="+92300", mode=mode)
    await call_repo.save_call(call)
    back = await call_repo.get_call("t", call.id)
    assert back.mode == mode


async def test_the_older_dashboard_still_reads_who_handled_the_call(fake_db):
    from app.models.call import Call, Channel, Direction
    from app.repositories import calls as call_repo

    call = Call(tenantId="t", channel=Channel.PHONE, direction=Direction.OUTBOUND,
                counterparty="+92300", mode="human", handledBy="operator")
    await call_repo.save_call(call)
    [row] = fake_db.tables[call_repo.CALLS_TABLE].values()
    assert row["data"]["mode"] == "operator"
    assert row["data"]["callMode"] == "human"


async def test_a_row_from_before_the_fix_is_still_an_ai_call(fake_db):
    """Old rows carry only the legacy `mode` ("agent"), never "human"."""
    from app.models.call import Call, Channel, Direction
    from app.repositories import calls as call_repo

    await call_repo.save_call(Call(tenantId="t", channel=Channel.PHONE,
                                   direction=Direction.OUTBOUND, counterparty="+92300"))
    [row] = fake_db.tables[call_repo.CALLS_TABLE].values()
    row["data"].pop("callMode")
    back = await call_repo.get_call("t", row["id"])
    assert back.mode != "human"


# ---------------------------------------------------------------------------
# Infobip: who ends up on the line
# ---------------------------------------------------------------------------

def test_a_human_infobip_dialer_call_connects_the_employee(client, infobip_dialer,
                                                          agent_starts):
    from app.security import playback
    from app.services.agent import claims
    from app.services.agent.session import Handler

    call = _dial(client, infobip_dialer["headers"], human=True)
    claims.reserve(call.id, "700")
    with client.websocket_connect("/api/media/infobip") as carrier:
        session = _wait_for_session(call.id)
        assert session.human and session.handler is Handler.OPERATOR
        assert agent_starts == [], "the AI was started on an employee's call"

        token = playback.mint("700", call.id, ttl=600)
        with client.websocket_connect(f"/api/media/operator/{call.id}?token={token}") as browser:
            assert browser.receive_json()["type"] == "handover"
            carrier.send_bytes(b"\x01\x00" * 320)     # the person on the phone speaks
            assert browser.receive_bytes() == b"\x01\x00" * 320

    assert infobip_dialer["hung_up"] == ["ib-1"], "the employee hanging up left the call up"


def test_an_ai_infobip_dialer_call_connects_the_agent(client, infobip_dialer, agent_starts):
    from app.services.agent import claims
    from app.services.agent.session import Handler

    call = _dial(client, infobip_dialer["headers"], human=False)
    claims.reserve(call.id, "700")
    with client.websocket_connect("/api/media/infobip"):
        session = _wait_for_session(call.id)
        assert not session.human and session.handler is Handler.AGENT
        assert agent_starts == [call.id], "the agent was not started"
