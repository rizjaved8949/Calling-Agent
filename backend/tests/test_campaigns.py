"""
Campaigns, usage and the gaps report.

The campaign tests matter more than most: starting one places real calls to
real people. A number parsed wrongly is a stranger's phone ringing, and a list
accepted with silent omissions is somebody never contacted who should have
been. Both are pinned here.
"""
from __future__ import annotations

import pytest

ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
def company(client, tenant_factory, auth):
    _, key = tenant_factory("700", "Northwind", defaultCountryCode="92",
                            infobipApiKey="k", infobipBaseUrl="https://x.api.infobip.com",
                            infobipPhoneNumber="+925160284")
    return key


def _create(client, key, auth, **kw):
    body = {"name": "Spring intake", "channel": "PHONE", **kw}
    return client.post("/api/campaigns", headers=auth(key), json=body)


# ---------------------------------------------------------------------------
# Parsing the list
# ---------------------------------------------------------------------------


def test_numbers_are_normalised_to_e164(client, company, auth):
    """What is dialled must not depend on how it was typed."""
    body = _create(client, company, auth,
                   numbers="03001112222\n+923001112223\n0300 111 2224").json()
    numbers = [c["number"] for c in body["contacts"]]
    assert numbers == ["+923001112222", "+923001112223", "+923001112224"]


def test_a_name_after_the_number_is_kept(client, company, auth):
    body = _create(client, company, auth, numbers="03001112222, Ayesha Khan").json()
    assert body["contacts"][0]["name"] == "Ayesha Khan"


def test_the_same_person_twice_is_called_once(client, company, auth):
    body = _create(client, company, auth,
                   numbers="03001112222\n+923001112222\n0300 111 2222").json()
    assert body["total"] == 1


def test_a_bad_number_rejects_the_whole_list(client, company, auth):
    """Accepting the rest would silently skip somebody, and the person who
    pasted the list would have no way to know which."""
    response = _create(client, company, auth, numbers="03001112222\nnot-a-number\n03001112224")
    assert response.status_code == 422
    body = response.json()
    assert "not-a-number" in str(body["error"]["details"])
    assert client.get("/api/campaigns", headers=auth(company)).json()["campaigns"] == []


def test_blank_lines_and_stray_quotes_survive_a_paste(client, company, auth):
    body = _create(client, company, auth,
                   numbers='\n"03001112222", "Ayesha"\n\n  03001112223  \n').json()
    assert body["total"] == 2
    assert body["contacts"][0]["name"] == "Ayesha"


# ---------------------------------------------------------------------------
# Starting one
# ---------------------------------------------------------------------------


def test_an_empty_list_cannot_be_started(client, company, auth):
    body = _create(client, company, auth, numbers="").json()
    response = client.post(f"/api/campaigns/{body['id']}/start", headers=auth(company))
    assert response.status_code == 409


def test_a_company_without_carrier_credentials_is_told_before_it_starts(
    client, tenant_factory, auth
):
    """Discovering it on the first call fails in front of a customer and
    leaves the campaign half-run."""
    _, key = tenant_factory("701", "NoCarrier", defaultCountryCode="92")
    created = client.post("/api/campaigns", headers=auth(key),
                          json={"name": "x", "channel": "PHONE",
                                "numbers": "03001112222"}).json()
    response = client.post(f"/api/campaigns/{created['id']}/start", headers=auth(key))
    assert response.status_code == 409
    assert "credentials" in response.json()["error"]["message"].lower()


def test_a_finished_list_cannot_be_restarted(client, company, auth):
    import asyncio

    from app.models.campaign import ContactState
    from app.repositories import campaigns as repo

    created = _create(client, company, auth, numbers="03001112222").json()
    campaign = asyncio.run(repo.get("700", created["id"]))
    campaign.contacts[0].state = ContactState.DONE
    asyncio.run(repo.save(campaign))

    response = client.post(f"/api/campaigns/{created['id']}/start", headers=auth(company))
    assert response.status_code == 409
    assert "already been called" in response.json()["error"]["message"]


def test_pausing_puts_a_half_dialled_contact_back_in_the_queue(client, company, auth):
    import asyncio

    from app.models.campaign import CampaignStatus, ContactState
    from app.repositories import campaigns as repo

    created = _create(client, company, auth, numbers="03001112222\n03001112223").json()
    campaign = asyncio.run(repo.get("700", created["id"]))
    campaign.status = CampaignStatus.RUNNING
    campaign.contacts[0].state = ContactState.CALLING
    asyncio.run(repo.save(campaign))

    body = client.post(f"/api/campaigns/{created['id']}/pause", headers=auth(company)).json()
    assert body["status"] == "PAUSED"
    assert body["counts"]["WAITING"] == 2, "a contact was lost mid-dial"


def test_editing_a_running_campaign_is_refused(client, company, auth):
    import asyncio

    from app.models.campaign import CampaignStatus
    from app.repositories import campaigns as repo

    created = _create(client, company, auth, numbers="03001112222").json()
    campaign = asyncio.run(repo.get("700", created["id"]))
    campaign.status = CampaignStatus.RUNNING
    asyncio.run(repo.save(campaign))

    response = client.patch(f"/api/campaigns/{created['id']}", headers=auth(company),
                            json={"name": "renamed"})
    assert response.status_code == 409


def test_editing_the_list_keeps_what_already_happened(client, company, auth):
    """Otherwise correcting a spelling calls somebody a second time."""
    import asyncio

    from app.models.campaign import ContactState
    from app.repositories import campaigns as repo

    created = _create(client, company, auth, numbers="03001112222\n03001112223").json()
    campaign = asyncio.run(repo.get("700", created["id"]))
    campaign.contacts[0].state = ContactState.DONE
    asyncio.run(repo.save(campaign))

    body = client.patch(f"/api/campaigns/{created['id']}", headers=auth(company),
                        json={"numbers": "03001112222, Ayesha\n03001112223\n03001112224"}).json()
    states = {c["number"]: c["state"] for c in body["contacts"]}
    assert states["+923001112222"] == "DONE", "an already-called contact was reset"
    assert states["+923001112224"] == "WAITING"


def test_campaigns_are_scoped_to_their_company(client, company, auth, tenant_factory):
    created = _create(client, company, auth, numbers="03001112222").json()
    _, other = tenant_factory("702", "Other")
    assert client.get(f"/api/campaigns/{created['id']}", headers=auth(other)).status_code == 404


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def test_usage_counts_from_the_call_log(client, tenant_factory, auth):
    import asyncio

    from app.models.call import Call, CallStatus, RecordingState
    from app.repositories import calls as call_repo

    _, key = tenant_factory("710")
    for seconds in (30, 90, 200):
        asyncio.run(call_repo.save_call(Call(
            tenantId="710", status=CallStatus.COMPLETED, answeredAt=1.0,
            durationSeconds=seconds, recordingState=RecordingState.READY,
            recordingBytes=1000,
        )))
    body = client.get("/api/usage", headers=auth(key)).json()
    assert body["allTime"]["calls"] == 3
    # 320 seconds rounds up to 6 minutes: a 20-second call is a minute to
    # anyone billing it.
    assert body["allTime"]["minutes"] == 6
    assert body["allTime"]["recordings"] == 3
    assert body["storage"]["recordingBytes"] == 3000


def test_usage_for_a_company_with_nothing_is_zeroes_not_an_error(client, tenant_factory, auth):
    _, key = tenant_factory("711")
    body = client.get("/api/usage", headers=auth(key)).json()
    assert body["allTime"]["calls"] == 0
    assert body["thisMonth"]["minutes"] == 0


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


def test_a_deferral_is_recognised_and_the_question_kept(client, tenant_factory, auth):
    import asyncio

    from app.models.call import Call
    from app.repositories import calls as call_repo

    _, key = tenant_factory("720")
    asyncio.run(call_repo.save_call(Call(
        tenantId="720", counterparty="+923001112222",
        transcript=(
            "caller: hello\n"
            "agent: Assalam-o-Alaikum, how can I help?\n"
            "caller: do you have a hostel for girls?\n"
            "agent: I will check on that and have someone follow up with you.\n"
        ),
    )))
    body = client.get("/api/gaps", headers=auth(key)).json()
    assert body["total"] == 1
    assert "hostel" in body["gaps"][0]["question"]


def test_an_answered_question_is_not_a_gap(client, tenant_factory, auth):
    import asyncio

    from app.models.call import Call
    from app.repositories import calls as call_repo

    _, key = tenant_factory("721")
    asyncio.run(call_repo.save_call(Call(
        tenantId="721",
        transcript="caller: what are the fees?\nagent: They are 120,000 per semester.\n",
    )))
    assert client.get("/api/gaps", headers=auth(key)).json()["total"] == 0


def test_the_same_question_asked_often_is_counted(client, tenant_factory, auth):
    import asyncio

    from app.models.call import Call
    from app.repositories import calls as call_repo

    _, key = tenant_factory("722")
    for _ in range(3):
        asyncio.run(call_repo.save_call(Call(
            tenantId="722",
            transcript=("caller: is there a hostel?\n"
                        "agent: I will check and have someone call you back.\n"),
        )))
    body = client.get("/api/gaps", headers=auth(key)).json()
    assert body["total"] == 3
    assert body["mostAsked"][0]["times"] == 3
    assert body["gaps"][0]["askedTimes"] == 3


@pytest.mark.parametrize("reply,deferred", [
    ("I will check and have someone follow up.", True),
    ("Main check kar ke aap ko batati hoon.", True),
    ("The fee is 120,000 per semester.", False),
    ("", False),
])
def test_deferral_detection(reply, deferred):
    from app.services.agent.gaps import looks_deferred

    assert looks_deferred(reply) is deferred


# ---------------------------------------------------------------------------
# The runner, against a carrier that cannot place a real call
# ---------------------------------------------------------------------------
#
# These reproduce a bug found by a test run that really did dial a phone:
# cancelling a campaign between the carrier accepting a call and us recording
# its id left a live call nobody could hang up.


class FakeCarrier:
    """Stands in for Infobip. Records what it was asked, places nothing."""

    def __init__(self, dial_seconds: float = 0.0):
        self.dial_seconds = dial_seconds
        self.placed: list[str] = []
        self.hung_up: list[str] = []

    def __call__(self, tenant):
        carrier = self

        class _Infobip:
            configured = True

            async def place_call(self, to, **_kw):
                import asyncio

                await asyncio.sleep(carrier.dial_seconds)
                carrier.placed.append(to)
                return {"id": f"carrier-{len(carrier.placed)}"}

            async def hangup(self, provider_call_id):
                carrier.hung_up.append(provider_call_id)

        return _Infobip()


@pytest.fixture
def runner(monkeypatch, fake_db):
    from app.services import campaign_runner

    carrier = FakeCarrier(dial_seconds=0.3)
    monkeypatch.setattr(campaign_runner, "Infobip", carrier)
    # The wait for a call to finish is five seconds per poll in production.
    real_sleep = campaign_runner.asyncio.sleep

    async def quick(seconds, *a, **k):
        await real_sleep(min(seconds, 0.02))

    monkeypatch.setattr(campaign_runner.asyncio, "sleep", quick)
    # Nothing settles a fake call the way a carrier webhook would, so the wait
    # for it to finish would run to its real four-minute limit.
    monkeypatch.setattr(campaign_runner, "MAX_CALL_SECONDS", 0.4)
    return campaign_runner, carrier


async def _campaign(numbers="03001112222", tenant_id="800"):
    from app.models.campaign import Campaign, CampaignStatus, Contact
    from app.repositories import campaigns as repo

    campaign = Campaign(
        tenantId=tenant_id, name="t", status=CampaignStatus.RUNNING, gapSeconds=5,
        contacts=[Contact(number=n) for n in numbers.split(",")],
    )
    await repo.save(campaign)
    return campaign


def _tenant():
    from app.models.tenant import Tenant

    return Tenant(phoneNumberId="800", defaultCountryCode="92",
                  infobipApiKey="k", infobipBaseUrl="https://x", infobipPhoneNumber="+9251")


async def test_cancelling_mid_dial_still_hangs_the_call_up(runner):
    """The bug: the carrier accepted the call, the task was cancelled before the
    id was saved, and a live call was left that nothing could end."""
    import asyncio

    from app.repositories import calls as call_repo

    module, carrier = runner
    campaign = await _campaign("+923001112222")
    task = asyncio.create_task(module._run(_tenant(), campaign.id))

    await asyncio.sleep(0.1)          # inside place_call, which takes 0.3s
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert carrier.placed, "the dial was abandoned half-way instead of finishing"
    assert carrier.hung_up == ["carrier-1"], "a live call was left running"
    rows = await call_repo.list_calls("800")
    assert rows[0].provider_call_id == "carrier-1", "the carrier's id was never recorded"


async def test_a_cancelled_contact_goes_back_in_the_queue(runner):
    import asyncio

    from app.models.campaign import ContactState
    from app.repositories import campaigns as repo

    module, _ = runner
    campaign = await _campaign("+923001112222")
    task = asyncio.create_task(module._run(_tenant(), campaign.id))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    fresh = await repo.get("800", campaign.id)
    assert fresh.contacts[0].state is ContactState.WAITING, "the contact was lost"


async def test_a_pause_is_not_undone_when_the_call_ends(runner):
    """The runner holds a copy of the campaign from when it started. Writing it
    back after a call would silently un-pause a campaign somebody just paused."""
    import asyncio

    from app.models.campaign import CampaignStatus
    from app.repositories import campaigns as repo

    module, _ = runner
    campaign = await _campaign("+923001112222,+923001112223")
    task = asyncio.create_task(module._run(_tenant(), campaign.id))

    await asyncio.sleep(0.1)
    paused = await repo.get("800", campaign.id)
    paused.status = CampaignStatus.PAUSED
    await repo.save(paused)

    await asyncio.wait_for(task, timeout=5)    # exits at the top of its loop
    final = await repo.get("800", campaign.id)
    assert final.status is CampaignStatus.PAUSED, "the pause was overwritten"


async def test_nobody_is_called_after_a_pause(runner):
    import asyncio

    from app.models.campaign import CampaignStatus
    from app.repositories import campaigns as repo

    module, carrier = runner
    campaign = await _campaign("+923001112222,+923001112223,+923001112224")
    task = asyncio.create_task(module._run(_tenant(), campaign.id))
    await asyncio.sleep(0.1)
    paused = await repo.get("800", campaign.id)
    paused.status = CampaignStatus.PAUSED
    await repo.save(paused)
    await asyncio.wait_for(task, timeout=5)

    assert len(carrier.placed) == 1, f"{len(carrier.placed)} people were called"


async def test_resume_all_restarts_a_campaign_left_running(runner):
    """What a restart used to lose: a RUNNING campaign nobody is working.

    `resume_all` is what the app calls once at boot so a deploy or a
    free-tier spin-down is a blip rather than a campaign that silently stops
    until somebody happens to open it and press start again.
    """
    import asyncio

    from app.repositories import tenants as tenant_repo

    module, carrier = runner
    tenant = _another_tenant()
    await tenant_repo.save(tenant)
    campaign = await _campaign("+923001112222", tenant_id=tenant.phone_number_id)

    assert not module.is_running(campaign.id)
    await module.resume_all()
    try:
        assert module.is_running(campaign.id), "resume_all did not restart it"
        # Polled rather than a single sleep: the fixture's own clamping of
        # `asyncio.sleep` (so the suite does not wait out a real four-minute
        # call) applies process-wide, so a fixed wait here is racing the same
        # clamp rather than a clean multiple of it.
        for _ in range(50):
            if carrier.placed:
                break
            await asyncio.sleep(0.02)
        assert carrier.placed, "the resumed campaign never called anyone"
    finally:
        await module.stop(campaign.id)


def _another_tenant():
    from app.models.tenant import Tenant

    return Tenant(phoneNumberId="801", defaultCountryCode="92",
                  infobipApiKey="k", infobipBaseUrl="https://x", infobipPhoneNumber="+9251")
