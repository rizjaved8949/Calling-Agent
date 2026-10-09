"""
Live chat between a company and the platform operator.

The things worth pinning here are the isolation (a company reaches its own
thread and nobody else's), the ticket discipline (a signed URL for one file
does not fetch another), and the unread bookkeeping, which is what both sides
actually look at.
"""
from __future__ import annotations

import asyncio
import io

import pytest

from app.models.support import Author
from app.repositories import support as repo
from app.security import tickets
from app.services import support_hub

ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
def fake_storage(monkeypatch):
    """Attachments in a dict. The real backends are exercised elsewhere."""
    from app.services import storage

    objects: dict[str, bytes] = {}

    async def put_object(key: str, data: bytes, mime: str):
        objects[key] = data
        return f"mem://{key}"

    async def get_object(reference: str):
        return objects.get(reference[len("mem://"):]) if reference.startswith("mem://") else None

    async def delete_object(reference: str):
        objects.pop(reference[len("mem://"):], None)

    async def signed_url(reference, seconds=None):
        return None  # force the streaming path, which is the one with the code in it

    monkeypatch.setattr(storage, "put_object", put_object)
    monkeypatch.setattr(storage, "get_object", get_object)
    monkeypatch.setattr(storage, "delete_object", delete_object)
    monkeypatch.setattr(storage, "signed_url", signed_url)
    return objects


@pytest.fixture(autouse=True)
def _no_watchers():
    """Watchers live in a module-level set, so a leaked one from a previous
    test would make the next one's messages arrive pre-read."""
    support_hub._watchers.clear()
    yield
    support_hub._watchers.clear()


# ---------------------------------------------------------------------------
# The conversation
# ---------------------------------------------------------------------------


def test_a_company_writes_and_the_operator_sees_it(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")

    written = client.post("/api/support/messages", data={"body": "My number will not verify"},
                          headers=auth(key))
    assert written.status_code == 200, written.text
    assert written.json()["author"] == "company"

    seen = client.get("/api/platform/support/111/messages", headers=ADMIN)
    assert seen.status_code == 200
    bodies = [m["body"] for m in seen.json()["messages"]]
    assert bodies == ["My number will not verify"]
    assert seen.json()["thread"]["unreadForOperator"] == 1


def test_the_operator_replies_and_the_company_sees_it(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    client.post("/api/support/messages", data={"body": "help"}, headers=auth(key))

    reply = client.post("/api/platform/support/111/messages",
                        data={"body": "Use a System User token."}, headers=ADMIN)
    assert reply.status_code == 200
    assert reply.json()["author"] == "operator"

    mine = client.get("/api/support/messages", headers=auth(key))
    assert [m["body"] for m in mine.json()["messages"]] == ["help", "Use a System User token."]
    assert mine.json()["thread"]["unreadForCompany"] == 1


def test_a_company_cannot_read_another_companys_thread(client, tenant_factory, auth):
    _, key_a = tenant_factory("111", "Acme")
    _, key_b = tenant_factory("222", "Other")
    client.post("/api/support/messages", data={"body": "a secret"}, headers=auth(key_a))

    # There is no parameter to override: the thread follows the key.
    theirs = client.get("/api/support/messages", headers=auth(key_b))
    assert theirs.json()["messages"] == []


def test_the_operator_must_be_an_operator(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    refused = client.get("/api/platform/support/111/messages", headers=auth(key))
    assert refused.status_code == 401


def test_writing_to_a_company_that_does_not_exist_is_a_404(client, fake_db):
    missing = client.post("/api/platform/support/nope/messages",
                          data={"body": "hello"}, headers=ADMIN)
    assert missing.status_code == 404


def test_an_empty_message_with_no_file_is_refused(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    refused = client.post("/api/support/messages", data={"body": "   "}, headers=auth(key))
    assert refused.status_code == 400


# ---------------------------------------------------------------------------
# Reading, and the ticks
# ---------------------------------------------------------------------------


def test_marking_read_clears_the_badge_and_ticks_the_message(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    client.post("/api/support/messages", data={"body": "help"}, headers=auth(key))

    assert client.post("/api/platform/support/111/read", headers=ADMIN).json()["read"] == 1

    after = client.get("/api/platform/support/111/messages", headers=ADMIN).json()
    assert after["thread"]["unreadForOperator"] == 0
    assert after["messages"][0]["readAt"] is not None
    # The company's own unread count is untouched: the operator reading does
    # not read anything on the company's behalf.
    assert after["thread"]["unreadForCompany"] == 0


def test_reading_does_not_tick_your_own_messages(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    client.post("/api/support/messages", data={"body": "mine"}, headers=auth(key))

    client.post("/api/support/read", headers=auth(key))
    mine = client.get("/api/support/messages", headers=auth(key)).json()
    assert mine["messages"][0]["readAt"] is None


def test_a_message_is_read_on_arrival_when_the_other_side_is_in_the_room(
    client, tenant_factory, auth,
):
    """The tick means "they have seen it", so it may only appear when that is
    true — and when both are present, it must appear at once."""
    _, key = tenant_factory("111", "Acme")
    watcher = support_hub.join("111", "operator")
    try:
        written = client.post("/api/support/messages", data={"body": "hello"},
                              headers=auth(key)).json()
    finally:
        support_hub.leave(watcher)

    assert written["readAt"] is not None
    thread = client.get("/api/support/messages", headers=auth(key)).json()["thread"]
    assert thread["unreadForOperator"] == 0


def test_the_thread_list_puts_whoever_is_waiting_first(client, tenant_factory):
    tenant_factory("111", "Quiet")
    _, key = tenant_factory("222", "Waiting")
    client.post("/api/support/messages", data={"body": "please help"},
                headers={"Authorization": f"Bearer {key}"})

    listing = client.get("/api/platform/support/threads", headers=ADMIN).json()
    assert listing["waiting"] == 1
    assert listing["threads"][0]["tenantId"] == "222"
    # A company that has never written is still listed, so the operator can
    # start a conversation rather than only answer one.
    assert {t["tenantId"] for t in listing["threads"]} == {"111", "222"}


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def test_a_photo_can_be_sent_and_fetched_back(client, tenant_factory, auth, fake_storage):
    _, key = tenant_factory("111", "Acme")
    sent = client.post(
        "/api/support/messages",
        data={"body": "look at this"},
        files={"file": ("error.png", io.BytesIO(b"\x89PNG-pretend"), "image/png")},
        headers=auth(key),
    )
    assert sent.status_code == 200, sent.text
    attachment = sent.json()["attachment"]
    assert attachment["kind"] == "image"
    assert attachment["bytes"] == len(b"\x89PNG-pretend")

    link = client.get(f"/api/support/attachments/{sent.json()['id']}", headers=auth(key))
    assert link.status_code == 200
    fetched = client.get(link.json()["url"])
    assert fetched.status_code == 200
    assert fetched.content == b"\x89PNG-pretend"


def test_a_voice_note_keeps_its_length(client, tenant_factory, auth, fake_storage):
    _, key = tenant_factory("111", "Acme")
    sent = client.post(
        "/api/support/messages",
        data={"durationSeconds": "7.4"},
        files={"file": ("note.weba", io.BytesIO(b"OggS-pretend"), "audio/webm")},
        headers=auth(key),
    ).json()
    assert sent["attachment"]["kind"] == "voice"
    assert sent["attachment"]["durationSeconds"] == 7.4
    # No body, but the message stands on its own.
    assert sent["body"] == ""


def test_an_executable_cannot_be_attached(client, tenant_factory, auth, fake_storage):
    _, key = tenant_factory("111", "Acme")
    refused = client.post(
        "/api/support/messages",
        files={"file": ("nasty.exe", io.BytesIO(b"MZ\x90"), "application/x-msdownload")},
        headers=auth(key),
    )
    assert refused.status_code == 415


def test_an_oversized_video_is_refused_with_its_own_limit(
    client, tenant_factory, auth, fake_storage,
):
    from app.models.support import AttachmentKind, MAX_BYTES

    _, key = tenant_factory("111", "Acme")
    too_big = b"x" * (MAX_BYTES[AttachmentKind.VIDEO] + 1)
    refused = client.post(
        "/api/support/messages",
        files={"file": ("big.mp4", io.BytesIO(too_big), "video/mp4")},
        headers=auth(key),
    )
    assert refused.status_code == 413
    assert "40 MB" in refused.json()["error"]["message"]


def test_a_photo_sized_video_limit_is_not_applied_to_photos(
    client, tenant_factory, auth, fake_storage,
):
    """A 20 MB image is refused even though a 20 MB video is not."""
    from app.models.support import AttachmentKind, MAX_BYTES

    _, key = tenant_factory("111", "Acme")
    big_image = b"x" * (MAX_BYTES[AttachmentKind.IMAGE] + 1)
    refused = client.post(
        "/api/support/messages",
        files={"file": ("huge.png", io.BytesIO(big_image), "image/png")},
        headers=auth(key),
    )
    assert refused.status_code == 413


def test_a_file_ticket_does_not_open_another_file(client, tenant_factory, auth, fake_storage):
    """One ticket, one attachment. Otherwise a shared link reads the thread."""
    _, key = tenant_factory("111", "Acme")
    first = client.post(
        "/api/support/messages",
        files={"file": ("a.png", io.BytesIO(b"first"), "image/png")}, headers=auth(key),
    ).json()
    second = client.post(
        "/api/support/messages",
        files={"file": ("b.png", io.BytesIO(b"second"), "image/png")}, headers=auth(key),
    ).json()

    ticket = client.get(f"/api/support/attachments/{first['id']}",
                        headers=auth(key)).json()["url"].split("ticket=")[1]
    swapped = client.get(f"/api/support/file/{second['id']}?ticket={ticket}")
    assert swapped.status_code == 403


def test_a_socket_ticket_cannot_fetch_a_file(client, tenant_factory, auth, fake_storage):
    """Audiences are separated, so one ticket is not the other."""
    _, key = tenant_factory("111", "Acme")
    sent = client.post(
        "/api/support/messages",
        files={"file": ("a.png", io.BytesIO(b"x"), "image/png")}, headers=auth(key),
    ).json()
    socket_ticket = client.get("/api/support/ticket", headers=auth(key)).json()["ticket"]

    refused = client.get(f"/api/support/file/{sent['id']}?ticket={socket_ticket}")
    assert refused.status_code == 403


def test_an_unsigned_ticket_is_refused(client, tenant_factory, auth, fake_storage):
    _, key = tenant_factory("111", "Acme")
    sent = client.post(
        "/api/support/messages",
        files={"file": ("a.png", io.BytesIO(b"x"), "image/png")}, headers=auth(key),
    ).json()
    assert client.get(f"/api/support/file/{sent['id']}?ticket=made.up").status_code == 403


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------


def test_a_company_can_withdraw_its_own_message(client, tenant_factory, auth, fake_storage):
    _, key = tenant_factory("111", "Acme")
    sent = client.post("/api/support/messages", data={"body": "oops"},
                       headers=auth(key)).json()

    assert client.delete(f"/api/support/messages/{sent['id']}",
                         headers=auth(key)).status_code == 200
    assert client.get("/api/support/messages", headers=auth(key)).json()["messages"] == []
    # Gone for the operator too: one shared history, not two.
    assert client.get("/api/platform/support/111/messages",
                      headers=ADMIN).json()["messages"] == []


def test_a_company_cannot_delete_the_operators_message(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    reply = client.post("/api/platform/support/111/messages", data={"body": "mine"},
                        headers=ADMIN).json()
    refused = client.delete(f"/api/support/messages/{reply['id']}", headers=auth(key))
    assert refused.status_code == 403


def test_the_operator_can_remove_anything(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    sent = client.post("/api/support/messages", data={"body": "bad upload"},
                       headers=auth(key)).json()
    assert client.delete(f"/api/platform/support/111/messages/{sent['id']}",
                         headers=ADMIN).status_code == 200


def test_deleting_a_message_erases_its_file(client, tenant_factory, auth, fake_storage):
    _, key = tenant_factory("111", "Acme")
    sent = client.post(
        "/api/support/messages",
        files={"file": ("a.png", io.BytesIO(b"bytes"), "image/png")}, headers=auth(key),
    ).json()
    assert fake_storage

    client.delete(f"/api/support/messages/{sent['id']}", headers=auth(key))
    assert fake_storage == {}


def test_deleting_corrects_the_unread_count(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    first = client.post("/api/support/messages", data={"body": "one"},
                        headers=auth(key)).json()
    client.post("/api/support/messages", data={"body": "two"}, headers=auth(key))

    assert client.get("/api/platform/support/threads",
                      headers=ADMIN).json()["threads"][0]["unreadForOperator"] == 2
    client.delete(f"/api/support/messages/{first['id']}", headers=auth(key))
    assert client.get("/api/platform/support/threads",
                      headers=ADMIN).json()["threads"][0]["unreadForOperator"] == 1


def test_deleting_a_company_takes_its_thread_and_uploads(
    client, tenant_factory, auth, fake_storage,
):
    _, key = tenant_factory("111", "Acme")
    client.post(
        "/api/support/messages", data={"body": "hello"},
        files={"file": ("a.png", io.BytesIO(b"bytes"), "image/png")}, headers=auth(key),
    )
    assert fake_storage

    assert client.delete("/api/companies/111", headers=ADMIN).status_code == 204
    assert fake_storage == {}
    assert asyncio.run(repo.history("111")) == []


# ---------------------------------------------------------------------------
# The socket
# ---------------------------------------------------------------------------


def test_the_socket_refuses_a_ticket_it_did_not_sign(client, tenant_factory):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/support/live?ticket=nonsense"):
            pass


def test_the_socket_delivers_a_reply_as_it_is_written(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    ticket = client.get("/api/support/ticket", headers=auth(key)).json()["ticket"]

    with client.websocket_connect(f"/api/support/live?ticket={ticket}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert hello["side"] == "company"

        client.post("/api/platform/support/111/messages",
                    data={"body": "on my way"}, headers=ADMIN)

        event = socket.receive_json()
        while event["type"] in {"ping", "presence"}:
            event = socket.receive_json()
        assert event["type"] == "message"
        assert event["message"]["body"] == "on my way"
        assert event["tenantId"] == "111"


def test_the_lobby_socket_hears_every_company(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    lobby = client.get("/api/platform/support/ticket", headers=ADMIN).json()["ticket"]

    with client.websocket_connect(f"/api/support/live?ticket={lobby}") as socket:
        assert socket.receive_json()["type"] == "hello"
        client.post("/api/support/messages", data={"body": "anyone there"}, headers=auth(key))

        event = socket.receive_json()
        while event["type"] == "ping":
            event = socket.receive_json()
        assert event["type"] == "message"
        assert event["tenantId"] == "111"


def test_a_company_socket_hears_nothing_from_another_company(client, tenant_factory, auth):
    _, key_a = tenant_factory("111", "Acme")
    _, key_b = tenant_factory("222", "Other")
    ticket = client.get("/api/support/ticket", headers=auth(key_a)).json()["ticket"]

    with client.websocket_connect(f"/api/support/live?ticket={ticket}") as socket:
        assert socket.receive_json()["type"] == "hello"
        client.post("/api/support/messages", data={"body": "not for you"}, headers=auth(key_b))
        # Nothing but the keepalive: the only frame due is a ping.
        assert socket.receive_json()["type"] == "ping"


def test_typing_reaches_the_other_side_only(client, tenant_factory, auth):
    _, key = tenant_factory("111", "Acme")
    company = client.get("/api/support/ticket", headers=auth(key)).json()["ticket"]
    operator = client.get("/api/platform/support/ticket?tenantId=111",
                          headers=ADMIN).json()["ticket"]

    with client.websocket_connect(f"/api/support/live?ticket={company}") as theirs:
        assert theirs.receive_json()["type"] == "hello"
        with client.websocket_connect(f"/api/support/live?ticket={operator}") as ours:
            assert ours.receive_json()["type"] == "hello"
            ours.send_json({"type": "typing", "on": True})

            event = theirs.receive_json()
            while event["type"] in {"ping", "presence"}:
                event = theirs.receive_json()
            assert event == {"type": "typing", "side": "operator", "on": True,
                            "tenantId": "111"}


# ---------------------------------------------------------------------------
# The hub and the tickets, directly
# ---------------------------------------------------------------------------


def test_a_watcher_that_stops_reading_is_told_to_resync_rather_than_grown():
    """A suspended tab must not become an unbounded queue in this process."""
    watcher = support_hub.join("111", "company")
    try:
        for index in range(support_hub.QUEUE_LIMIT + 10):
            watcher.offer({"type": "message", "n": index})
        assert watcher.queue.qsize() <= support_hub.QUEUE_LIMIT
        assert watcher.dropped > 0
        drained = []
        while not watcher.queue.empty():
            drained.append(watcher.queue.get_nowait())
        assert any(event["type"] == "resync" for event in drained)
    finally:
        support_hub.leave(watcher)


def test_a_ticket_is_only_valid_for_its_own_audience():
    from app.errors import AppError

    minted = tickets.mint("support-socket", "111")
    assert tickets.verify(minted, "support-socket")["s"] == "111"
    with pytest.raises(AppError):
        tickets.verify(minted, "support-file")


def test_an_expired_ticket_is_refused():
    from app.errors import AppError

    stale = tickets.mint("support-socket", "111", ttl=-1)
    with pytest.raises(AppError) as refusal:
        tickets.verify(stale, "support-socket")
    assert refusal.value.code == "expired_ticket"


def test_recount_rebuilds_a_summary_that_has_drifted(fake_db, tenant_factory):
    """The counters are a cache. This is the repair, so a drifted badge is a
    redraw away from correct rather than wrong forever."""
    from app.models.support import SupportMessage

    tenant_factory("111", "Acme")
    asyncio.run(repo.append(SupportMessage(tenant_id="111", author=Author.COMPANY, body="a")))
    asyncio.run(repo.append(SupportMessage(tenant_id="111", author=Author.COMPANY, body="b")))

    broken = asyncio.run(repo.thread_for("111"))
    broken.unread_for_operator = 99
    asyncio.run(repo.save_thread(broken))

    fixed = asyncio.run(repo.recount("111"))
    assert fixed.unread_for_operator == 2
    assert fixed.last_preview == "b"


def test_a_preview_says_what_was_sent_when_there_are_no_words(fake_db, tenant_factory):
    from app.models.support import Attachment, AttachmentKind, SupportMessage

    tenant_factory("111", "Acme")
    asyncio.run(repo.append(SupportMessage(
        tenant_id="111", author=Author.COMPANY,
        attachment=Attachment(kind=AttachmentKind.VOICE, mime="audio/webm"),
    )))
    assert asyncio.run(repo.thread_for("111")).last_preview == "Sent a voice note"
