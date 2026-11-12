"""
A request the caller made is recorded for real, with a real reference.

The agent used to say "your complaint has been submitted, reference
CMP-20261108-01" with nothing written anywhere. Now nothing is said until
the details are complete, read back and confirmed, and the reference comes
from the store.
"""
from __future__ import annotations

import asyncio
import re
from io import BytesIO

import pytest

from app.models.call import Call, Channel, Direction
from app.models.request import RequestType
from app.models.tenant import Tenant
from app.repositories import requests as request_repo
from app.services.agent import live


def _tenant(pid="t-1", **extra) -> Tenant:
    return Tenant(phoneNumberId=pid, name="Acme", defaultCountryCode="92", **extra)


def _call(pid="t-1", **extra) -> Call:
    fields = dict(tenantId=pid, channel=Channel.PHONE, direction=Direction.INBOUND,
                  counterparty="+923191611020")
    return Call(**{**fields, **extra})


class _Line:
    handler = type("H", (), {"value": "agent"})()
    frames_out = 10

    def __init__(self, transcript):
        self.transcript = list(transcript)
        self.caller_spoke_at = 0.0

    def caller_spoke_within(self, _seconds):
        return False

    async def end(self, _reason):
        return None

    def transcript_text(self):
        return "\n".join(f"{who}: {text}" for who, text in self.transcript)


@pytest.fixture(autouse=True)
def _no_sessions():
    live._sessions.clear()
    yield
    live._sessions.clear()


DETAILS = {
    "type": "test_drive", "name": "Saman Sajid", "mobile": "0300 123 4567",
    "city": "Lahore", "vehicle_model": "SEAL Premium",
    "preferred_times": ["Saturday 9:30", "Saturday 10:30"],
    "details": "wants a test drive before buying",
}
GAVE_NUMBER = [("caller", "mera naam Saman Sajid hai, number 0300 123 4567, Lahore")]
CONFIRMED = GAVE_NUMBER + [("agent", "...sahi hai?"), ("caller", "ji bilkul sahi hai")]


def _rows(fake_db):
    return list(fake_db.tables.get(request_repo.TABLE, {}).values())


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

async def test_missing_fields_are_named_two_at_a_time(fake_db):
    call = _call()
    live._sessions[call.id] = _Line(GAVE_NUMBER)
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {"type": "test_drive", "name": "Saman"})
    assert answer.startswith("Not recorded yet. Still needed:")
    assert "a mobile number" in answer and "the vehicle model" in answer
    assert "Ask for at most two at a time" in answer
    assert _rows(fake_db) == []


async def test_a_read_back_comes_before_anything_is_stored(fake_db):
    call = _call()
    live._sessions[call.id] = _Line(GAVE_NUMBER)
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {**DETAILS, "confirmed": False})
    assert answer.startswith("Not recorded yet. Read this back")
    assert "0300 123 4567" in answer and "SEAL Premium" in answer and "Saman Sajid" in answer
    assert "confirmed true" in answer
    assert _rows(fake_db) == []


async def test_it_is_stored_only_after_the_caller_confirms(fake_db):
    from app.repositories import calls as call_repo

    tenant, call = _tenant(), _call()
    await call_repo.save_call(call)
    live._sessions[call.id] = _Line(GAVE_NUMBER)
    handle = live._tool_handler(tenant, call)

    # confirmed true, but the caller has not heard the read-back yet.
    answer = await handle("record_request", {**DETAILS, "confirmed": True})
    assert "Read this back" in answer and _rows(fake_db) == []

    live._sessions[call.id].transcript = list(CONFIRMED)
    answer = await handle("record_request", {**DETAILS, "confirmed": True})
    assert re.match(r"^Recorded\. Reference TD-\d{8}-01\. ", answer), answer
    assert "a request, not a confirmation" in answer
    [row] = _rows(fake_db)
    data = row["data"]
    assert data["mobile"] == "+923001234567" and data["type"] == "test_drive"
    assert data["callId"] == call.id and data["callerNumber"] == "+923191611020"
    assert data["status"] == "new"

    saved = await call_repo.get_call("t-1", call.id)
    assert "Request recorded: TD-" in saved.summary
    assert "SEAL Premium" in saved.summary and "0300 123 4567" in saved.summary


async def test_a_ten_digit_mobile_is_refused(fake_db):
    call = _call()
    live._sessions[call.id] = _Line([("caller", "0300123456")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {**DETAILS, "mobile": "0300123456"})
    assert "looks incomplete" in answer and "11 digits" in answer
    assert _rows(fake_db) == []


async def test_a_mobile_the_caller_never_said_is_refused(fake_db):
    call = _call()
    live._sessions[call.id] = _Line([("caller", "mera naam Saman hai")])
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {**DETAILS})
    assert "has not said that number" in answer


async def test_a_question_needs_only_a_number_and_the_question(fake_db):
    call = _call()
    live._sessions[call.id] = _Line(CONFIRMED)
    handle = live._tool_handler(_tenant(), call)
    args = {"type": "question", "mobile": "0300 123 4567",
            "details": "Which banks offer financing?", "confirmed": True}
    first = await handle("record_request", args)
    assert "Read this back" in first
    answer = await handle("record_request", args)
    assert re.match(r"^Recorded\. Reference REQ-\d{8}-01\.", answer)
    assert "the team will send the answer on WhatsApp" in answer
    assert "Do not say anything has been sent" in answer
    [row] = _rows(fake_db)
    assert row["data"]["replyBy"] == "whatsapp" and row["data"]["name"] == ""


async def test_a_complaint_needs_a_vehicle_or_a_registration(fake_db):
    call = _call()
    live._sessions[call.id] = _Line(GAVE_NUMBER)
    handle = live._tool_handler(_tenant(), call)
    answer = await handle("record_request", {
        "type": "complaint", "name": "Saman", "mobile": "0300 123 4567",
        "city": "Lahore", "details": "charging stops at 80%",
    })
    assert "the vehicle model or the registration number" in answer


# ---------------------------------------------------------------------------
# The reference
# ---------------------------------------------------------------------------

async def test_references_follow_the_format_and_count_up_per_day(fake_db):
    tenant = _tenant()
    day = request_repo.local_day(tenant)
    first = await request_repo.create(tenant, _call(), RequestType.COMPLAINT, {"details": "a"})
    second = await request_repo.create(tenant, _call(), RequestType.COMPLAINT, {"details": "b"})
    drive = await request_repo.create(tenant, _call(), RequestType.TEST_DRIVE, {"details": "c"})
    assert first.reference == f"CMP-{day}-01"
    assert second.reference == f"CMP-{day}-02"
    assert drive.reference == f"TD-{day}-01"
    assert re.fullmatch(r"CMP-\d{8}-\d{2}", first.reference)
    for kind, prefix in (("service", "SRV"), ("callback", "CB"), ("cancellation", "CXL"),
                         ("question", "REQ"), ("other", "REQ")):
        made = await request_repo.create(tenant, None, RequestType(kind), {})
        assert made.reference.startswith(f"{prefix}-{day}-")


async def test_two_requests_at_once_do_not_share_a_reference(fake_db):
    tenant = _tenant()
    made = await asyncio.gather(*(
        request_repo.create(tenant, _call(), RequestType.CALLBACK, {}) for _ in range(5)))
    assert len({m.reference for m in made}) == 5


def test_the_day_is_the_companys_own():
    """Midnight in Lahore, not in UTC: 20:30 UTC on the 10th is the 11th there."""
    from datetime import datetime, timezone

    at = datetime(2026, 11, 10, 20, 30, tzinfo=timezone.utc).timestamp()
    assert request_repo.local_day(_tenant(), at) == "20261111"
    assert request_repo.local_day(Tenant(phoneNumberId="x", timezone="UTC"), at) == "20261110"
    assert request_repo.local_day(Tenant(phoneNumberId="x", timezone="Asia/Karachi"), at) == "20261111"


# ---------------------------------------------------------------------------
# Where the team sees it
# ---------------------------------------------------------------------------

async def test_the_report_has_a_requests_sheet(fake_db):
    from openpyxl import load_workbook

    from app.services import reports

    tenant = _tenant()
    made = await request_repo.create(tenant, _call(), RequestType.TEST_DRIVE, {
        "name": "Saman", "mobile": "+923001234567", "city": "Lahore",
        "vehicle_model": "SEAL Premium", "preferred_times": ["Sat 9:30"],
        "details": "test drive",
    })
    book = load_workbook(BytesIO(await reports.workbook_for(tenant)))
    assert "Requests" in book.sheetnames
    sheet = book["Requests"]
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][:6] == ("Reference", "Date", "Time", "Type", "Status", "Name")
    assert rows[1][0] == made.reference and rows[1][3] == "Test Drive"
    assert rows[1][5] == "Saman" and rows[1][9] == "SEAL Premium"


def test_requests_can_be_listed_and_updated_through_the_api(client, tenant_factory, auth):
    tenant, key = tenant_factory("111", "Acme")
    made = asyncio.run(request_repo.create(tenant, _call("111"), RequestType.COMPLAINT,
                                           {"name": "Ali", "details": "charging"}))
    listed = client.get("/api/requests", headers=auth(key)).json()
    assert listed["total"] == 1
    assert listed["requests"][0]["reference"] == made.reference
    assert listed["requests"][0]["status"] == "new"

    updated = client.patch(f"/api/requests/{made.id}", headers=auth(key),
                           json={"status": "done", "note": "called back"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "done" and updated.json()["note"] == "called back"
    assert client.get("/api/requests?status=done", headers=auth(key)).json()["total"] == 1
    assert client.get("/api/requests?status=new", headers=auth(key)).json()["total"] == 0
    assert client.get("/api/requests?type=complaint", headers=auth(key)).json()["total"] == 1


def test_one_company_cannot_see_or_change_anothers_requests(client, tenant_factory, auth):
    tenant_a, _key_a = tenant_factory("111", "Acme")
    _tenant_b, key_b = tenant_factory("222", "Beta")
    made = asyncio.run(request_repo.create(tenant_a, _call("111"), RequestType.COMPLAINT,
                                           {"details": "x"}))
    assert client.get("/api/requests", headers=auth(key_b)).json()["total"] == 0
    assert client.patch(f"/api/requests/{made.id}", headers=auth(key_b),
                        json={"status": "done"}).status_code == 404


# ---------------------------------------------------------------------------
# Nothing crosses between calls
# ---------------------------------------------------------------------------

async def test_nothing_from_one_call_reaches_the_next(fake_db):
    """CP-02's "privacy leak" was two tests run in one call. Proven here:
    a second call's instructions and tool state hold nothing from the first."""
    from app.repositories import calls as call_repo

    tenant = _tenant()
    first = _call(id="call-a", counterparty="+923001111111")
    await call_repo.save_call(first)
    live._sessions[first.id] = _Line([
        ("caller", "Mera naam Saman Sajid hai, number 0300 123 4567, ATTO 3 charging "
                   "complaint hai"),
        ("agent", "...sahi hai?"), ("caller", "ji"),
    ])
    handle_a = live._tool_handler(tenant, first)
    args = {"type": "complaint", "name": "Saman Sajid", "mobile": "0300 123 4567",
            "city": "Lahore", "vehicle_model": "ATTO 3", "details": "charging stops",
            "confirmed": True}
    await handle_a("record_request", args)
    recorded = await handle_a("record_request", args)
    assert recorded.startswith("Recorded. Reference CMP-")
    await live.finish(tenant, first.id, "caller hung up")

    second = _call(id="call-b", counterparty="+923002222222")
    persona = await live.persona_for(tenant, second)
    for leaked in ("Saman", "0300 123 4567", "+923001234567", "ATTO 3 charging", "CMP-"):
        assert leaked not in persona.instructions, leaked
    assert live.get(second.id) is None
    # The second call's tool state is its own: the same arguments, confirmed,
    # get a read-back, not a record.
    live._sessions[second.id] = _Line([("caller", "0300 123 4567")])
    answer = await live._tool_handler(tenant, second)("record_request", args)
    assert "Read this back" in answer
