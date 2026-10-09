"""
Deleting many recordings at once.

The dangerous part is not the deleting, it is deleting a different set than
the person was looking at. The filters mirror the screen's, and the screen
sends the count it showed so a mismatch is refused rather than taken as
"about that many".
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.call import Call, Channel, RecordingState


def _call(call_id: str, tenant_id: str, channel: Channel, *, recorded=True, **extra):
    from app.repositories import calls as call_repo

    asyncio.run(call_repo.save_call(Call(
        id=call_id, tenantId=tenant_id, channel=channel, counterparty="+92300",
        recordingPath=f"sb://{call_id}.mp3" if recorded else "",
        recordingState=RecordingState.READY if recorded else RecordingState.NONE,
        **extra,
    )))


@pytest.fixture
def storage(monkeypatch):
    erased: list[str] = []
    from app.services import storage as storage_service

    async def fake_delete(_tenant, reference):
        erased.append(reference)

    monkeypatch.setattr(storage_service, "delete", fake_delete)
    return erased


def test_only_the_chosen_channel_is_deleted(client, tenant_factory, auth, storage):
    _, key = tenant_factory("970")
    _call("wa-1", "970", Channel.WHATSAPP_CALL)
    _call("wa-2", "970", Channel.WHATSAPP_CALL)
    _call("sim-1", "970", Channel.PHONE)

    body = client.post("/api/recordings/delete", json={"channel": "WHATSAPP_CALL"},
                       headers=auth(key)).json()
    assert body["deleted"] == 2
    assert sorted(storage) == ["sb://wa-1.mp3", "sb://wa-2.mp3"]

    remaining = client.get("/api/calls", headers=auth(key)).json()["calls"]
    kept = {c["id"]: c["recording"]["available"] for c in remaining}
    assert kept["sim-1"] is True, "the phone recording went with the WhatsApp ones"
    assert kept["wa-1"] is False and kept["wa-2"] is False


def test_the_call_records_survive_by_default(client, tenant_factory, auth, storage):
    """Who rang and when is usually worth keeping after the voice has to go."""
    _, key = tenant_factory("971")
    _call("c-1", "971", Channel.PHONE, transcript="caller: hello")

    client.post("/api/recordings/delete", json={}, headers=auth(key))
    call = client.get("/api/calls/c-1", headers=auth(key)).json()
    assert call["recording"]["available"] is False
    assert call["transcript"] == "caller: hello"


def test_asking_for_the_calls_too_removes_them(client, tenant_factory, auth, storage):
    _, key = tenant_factory("972")
    _call("c-1", "972", Channel.PHONE)
    _call("c-2", "972", Channel.PHONE, recorded=False)

    body = client.post("/api/recordings/delete", json={"deleteCalls": True},
                       headers=auth(key)).json()
    assert body["deleted"] == 2, "a call with no audio was skipped"
    assert client.get("/api/calls", headers=auth(key)).json()["calls"] == []


def test_a_count_that_does_not_match_the_screen_is_refused(client, tenant_factory, auth, storage):
    """Somebody else deleting one while the dialog was open must not turn a
    confirmed "delete 3" into a silent "delete 2"."""
    _, key = tenant_factory("973")
    _call("c-1", "973", Channel.PHONE)
    _call("c-2", "973", Channel.PHONE)

    refused = client.post("/api/recordings/delete", json={"expected": 5}, headers=auth(key))
    assert refused.status_code == 409
    assert "not the 5 on your screen" in refused.json()["error"]["message"]
    assert storage == [], "something was deleted despite the refusal"

    ok = client.post("/api/recordings/delete", json={"expected": 2}, headers=auth(key))
    assert ok.status_code == 200 and ok.json()["deleted"] == 2


def test_another_companys_recordings_are_untouched(client, tenant_factory, auth, storage):
    _, key_a = tenant_factory("974")
    _, _key_b = tenant_factory("975")
    _call("mine", "974", Channel.PHONE)
    _call("theirs", "975", Channel.PHONE)

    body = client.post("/api/recordings/delete", json={}, headers=auth(key_a)).json()
    assert body["deleted"] == 1
    assert storage == ["sb://mine.mp3"]


def test_one_stubborn_file_does_not_stop_the_rest(client, tenant_factory, auth, monkeypatch):
    _, key = tenant_factory("976")
    _call("good-1", "976", Channel.PHONE)
    _call("bad", "976", Channel.PHONE)
    _call("good-2", "976", Channel.PHONE)

    from app.services import storage as storage_service

    async def fussy(_tenant, reference):
        if "bad" in reference:
            raise RuntimeError("storage said no")

    monkeypatch.setattr(storage_service, "delete", fussy)
    body = client.post("/api/recordings/delete", json={}, headers=auth(key)).json()
    assert body["deleted"] == 2 and body["failed"] == 1


def test_deleting_by_id_takes_exactly_those(client, tenant_factory, auth, storage):
    """The screen filters on more than the API does — direction, which number,
    whether there is audio. Sending ids is what makes "delete these" mean
    these, rather than everything a coarser filter would have matched."""
    _, key = tenant_factory("977")
    _call("keep", "977", Channel.PHONE)
    _call("go-1", "977", Channel.PHONE)
    _call("go-2", "977", Channel.PHONE)

    body = client.post("/api/recordings/delete",
                       json={"callIds": ["go-1", "go-2"]}, headers=auth(key)).json()
    assert body["deleted"] == 2
    assert sorted(storage) == ["sb://go-1.mp3", "sb://go-2.mp3"]

    kept = {c["id"]: c["recording"]["available"]
            for c in client.get("/api/calls", headers=auth(key)).json()["calls"]}
    assert kept["keep"] is True


def test_an_id_from_another_company_is_ignored(client, tenant_factory, auth, storage):
    _, key_a = tenant_factory("978")
    _, _key_b = tenant_factory("979")
    _call("mine", "978", Channel.PHONE)
    _call("theirs", "979", Channel.PHONE)

    body = client.post("/api/recordings/delete",
                       json={"callIds": ["mine", "theirs"]}, headers=auth(key_a)).json()
    assert body["deleted"] == 1
    assert storage == ["sb://mine.mp3"]


def test_the_same_id_twice_is_deleted_once(client, tenant_factory, auth, storage):
    _, key = tenant_factory("980")
    _call("c-1", "980", Channel.PHONE)
    body = client.post("/api/recordings/delete",
                       json={"callIds": ["c-1", "c-1"]}, headers=auth(key)).json()
    assert body["deleted"] == 1
