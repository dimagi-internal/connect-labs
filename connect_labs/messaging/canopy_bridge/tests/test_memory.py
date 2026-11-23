"""Fallback summaries, the OCS history import, erasure (labs and canopy), and "what the agent saw".

Every name and message here is invented.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from django.utils import timezone

from connect_labs.messaging import service
from connect_labs.messaging.canopy_bridge import erasure, ocs_import, summaries
from connect_labs.messaging.canopy_bridge.models import Erasure, WorkerBrief, WorkerCommitment
from connect_labs.messaging.models import Address, Direction, Message, Thread, ThreadSummary
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.users.models import User
from connect_labs.workers.access import WorkerView
from connect_labs.workers.models import (
    ConnectWorker,
    StaffNote,
    Visibility,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
    WorkerSnapshot,
)

pytestmark = pytest.mark.django_db
PHONE = "+234 803 555 0142"


def _thread(worker, **kw):
    address, _ = Address.objects.get_or_create(
        worker=worker,
        channel_source=service.channel_source(PROGRAM),
        defaults={"program_id": PROGRAM, "channel_id": f"c-{worker.username}"},
    )
    return service.open_thread(worker, address=address, direction=Direction.OUTBOUND, program_id=PROGRAM, **kw)


def _say(thread, text, direction="in", **kw):
    return Message.objects.create(
        thread=thread,
        worker=thread.worker,
        direction=direction,
        author_kind="worker" if direction == "in" else "agent",
        author_ref="" if direction == "in" else "connect-coach:learn",
        text=text,
        delivery_status="received" if direction == "in" else "sent",
        **kw,
    )


# --- fallback summaries -------------------------------------------------------------------------


def test_a_closed_thread_with_no_summary_gets_one_from_labs(worker):
    worker.phone = PHONE
    worker.save()
    thread = _thread(worker, playbook_key="learn-stalled")
    _say(thread, "hello, I am stuck on module 2", direction="in")
    _say(thread, "Try reopening the app", direction="out")
    _say(thread, f"ok call me {PHONE}", direction="in")
    _say(thread, "SAFETY-DISCLOSURE", direction="in", visibility=Visibility.RESTRICTED)
    service.close_thread(thread, "no messages for 48 hours", by="exit rule")
    assert summaries.summarize_closed() == 1
    summary = ThreadSummary.objects.get(thread=thread)
    assert (summary.author_kind, summary.author_ref, summary.outcome) == ("system", "labs-job", "timed_out")
    assert "3 messages (2 from the Connect Worker, 1 to them)" in summary.text
    assert "stuck on module 2" in summary.text and "Try reopening the app" in summary.text
    assert "803 555" not in summary.text and "SAFETY" not in summary.text
    assert summaries.summarize_closed() == 0  # once


def test_a_thread_the_agent_summarised_or_still_open_gets_none(worker):
    open_one = _thread(worker)
    _say(open_one, "hi")
    summarised = _thread(worker)
    _say(summarised, "hi")
    service.record_summary(summarised, "Answered", author_kind="agent", outcome="answered")
    service.close_thread(summarised, "done")
    empty = _thread(worker)
    service.close_thread(empty, "done")
    assert summaries.summarize_closed() == 0


def test_the_agents_own_summary_stands_when_its_answer_goes_out_after_it(worker):
    thread = _thread(worker)
    _say(thread, "thanks, that's all")
    service.record_summary(thread, "Resolved their question", author_kind="agent", outcome="resolved")
    _say(thread, "Goodbye!", direction="out")
    service.close_thread(thread, "resolved")
    assert summaries.summarize_closed() == 0
    _say(thread, "wait, one more thing")  # the worker writes after the summary: it is out of date
    assert summaries.needs_summary(thread)


def test_old_closed_threads_are_left_alone(worker):
    thread = _thread(worker)
    _say(thread, "hi")
    service.close_thread(thread, "done")
    later = timezone.now() + summaries.LOOKBACK + dt.timedelta(days=1)
    assert summaries.summarize_closed(now=later) == 0


# --- the OCS history import ---------------------------------------------------------------------

SESSION = "6f1c0c55-0000-4000-8000-00000000abcd"


class FakeOCS:
    def __init__(self, username):
        self.username = username
        self.messages = [
            {
                "role": "assistant",
                "content": "Hi! Let's review your visit flags.",
                "created_at": "2026-10-01T10:00:00Z",
            },
            {"role": "user", "content": "OK, what went wrong?", "created_at": "2026-10-01T10:01:00Z"},
            {"role": "assistant", "content": "Two photos were blurry.", "created_at": "2026-10-01T10:02:00Z"},
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/participants":
            data = [{"chatbot_id": "bot-1"}]
            return httpx.Response(200, json={"results": [{"identifier": self.username, "data": data}]})
        if path == "/api/sessions/":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": SESSION,
                            "experiment": {"id": "bot-1", "name": "Coach Bot"},
                            "participant": {"identifier": self.username},
                            "created_at": "2026-10-01T10:00:00Z",
                            "ended_at": "2026-10-01T10:30:00Z",
                            "participant_data": {"connectTaskId": "77"},
                        }
                    ],
                    "next": None,
                },
            )
        if path == f"/api/sessions/{SESSION}/":
            return httpx.Response(200, json={"id": SESSION, "messages": list(self.messages)})
        return httpx.Response(404)


def test_ocs_sessions_become_closed_threads_once(worker):
    WorkerMembership.objects.create(worker=worker, opportunity_id=9001, program_id=PROGRAM)
    WorkerSnapshot.objects.create(
        worker=worker,
        opportunity_id=9001,
        kind="assigned_tasks",
        key="77",
        data={"mode": "ocs", "assigned_task_id": 77},
    )
    task_event = WorkerEvent.objects.create(
        worker=worker,
        kind="ocs_session",
        source="ocs",
        opportunity_id=9001,
        dedupe_key="ocs-ev",
        ref={"ocs_session_id": SESSION},
    )
    fake = FakeOCS(worker.username)
    client = httpx.Client(base_url="https://ocs.test", transport=httpx.MockTransport(fake.handle))
    counts = ocs_import.import_worker(worker, client=client)
    assert counts == {"sessions": 1, "threads": 1, "messages": 3, "errors": 0}
    thread = Thread.objects.get(worker=worker)
    assert (thread.state, thread.handler_kind, thread.program_id, thread.opportunity_id) == (
        "closed",
        "none",
        PROGRAM,
        9001,
    )
    assert thread.context["ocs_session_id"] == SESSION and thread.started_by == f"ocs:{SESSION}"
    msgs = list(Message.objects.filter(thread=thread).order_by("created_at"))
    assert [(m.direction, m.author_ref) for m in msgs] == [
        ("out", "ocs:Coach Bot"),
        ("in", worker.username),
        ("out", "ocs:Coach Bot"),
    ]
    task_event.refresh_from_db()
    assert task_event.ref["thread_id"] == thread.pk
    # Again: nothing new. A new message in OCS: only it is added.
    assert ocs_import.import_worker(worker, client=client)["messages"] == 0
    fake.messages.append({"role": "user", "content": "Thanks!", "created_at": "2026-10-01T10:05:00Z"})
    assert ocs_import.import_worker(worker, client=client) == {"sessions": 1, "threads": 0, "messages": 1, "errors": 0}
    assert Thread.objects.filter(worker=worker).count() == 1
    # The fallback summary covers it, so the next brief knows it happened.
    summary = ThreadSummary.objects.get(thread=thread)
    assert summary.author_ref == "labs-job" and "Two photos were blurry" in summary.text


def test_the_import_command_needs_names_or_all(worker):
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError):
        call_command("import_ocs_history")


# --- erasure ------------------------------------------------------------------------------------


def _full_record(worker):
    worker.phone = PHONE
    worker.languages = ["ha"]
    worker.save()
    WorkerMembership.objects.create(worker=worker, opportunity_id=9001, program_id=PROGRAM)
    thread = _thread(worker)
    _say(thread, "hi")
    service.record_summary(thread, "said hi", author_kind="agent")
    WorkerFact.objects.create(worker=worker, kind="language", statement="Hausa", asserted_by_kind="agent")
    StaffNote.objects.create(worker=worker, body="note", author="x")
    WorkerEvent.objects.create(worker=worker, kind="visit", source="connect", dedupe_key="v1")
    WorkerCommitment.objects.create(worker=worker, text="call back")
    WorkerBrief.objects.create(hash="h" * 64, body="brief", worker=worker, thread=thread)
    return thread


def test_erasure_deletes_everything_and_tells_canopy(bridge_on, worker, run_now, monkeypatch):
    from connect_labs.messaging.canopy_bridge import tasks

    monkeypatch.setattr(tasks.erase_in_canopy, "delay", lambda pk: tasks.erase_in_canopy(pk))
    from connect_labs.mcp.models import MCPAuditLog

    _full_record(worker)
    bridge_on.contacts["personalid:amina123"] = {"version": 1, "contact_id": 5}
    audit = MCPAuditLog.objects.create(
        tool_name="worker_fact_record",
        is_write=True,
        arguments={"statement": "prefers Swahili"},
        success=True,
        execution={"mode": "subject", "subject": "personalid:amina123"},
    )
    row = erasure.erase_worker(worker, by="nurse.joy")
    audit.refresh_from_db()
    assert audit.arguments == {} and audit.tool_name == "worker_fact_record"
    worker.refresh_from_db()
    assert worker.erased_at and (worker.name, worker.phone, worker.languages) == ("", "", [])
    for model in (
        Message,
        Thread,
        Address,
        WorkerFact,
        StaffNote,
        WorkerEvent,
        WorkerMembership,
        WorkerBrief,
        WorkerCommitment,
        ThreadSummary,
    ):
        assert not model.objects.exists(), model
    row.refresh_from_db()
    assert row.labs_counts["messages"] == 1 and row.labs_counts["facts"] == 1
    assert (row.canopy_status, row.sub) == ("erased", "personalid:amina123")
    assert bridge_on.erased == ["personalid:amina123"]


def test_an_unreachable_canopy_erasure_is_retried(bridge_on, worker, run_now, monkeypatch):
    from connect_labs.messaging.canopy_bridge import tasks

    monkeypatch.setattr(tasks.erase_in_canopy, "delay", lambda pk: tasks.erase_in_canopy(pk))
    bridge_on.fail_next["erase"] = (503, "unavailable: later")
    row = erasure.erase_worker(worker, by="x")
    row.refresh_from_db()
    assert row.canopy_status == "error"
    assert erasure.retry_pending() == 1
    row.refresh_from_db()
    assert row.canopy_status == "not_found" and row.canopy_done_at


def test_canopys_files_remain_is_sent_again_until_it_finishes(bridge_on, worker, run_now, monkeypatch):
    """canopy's 503 files_remain: what it erased stays erased, and the erasure is sent again
    (by the sweep) for as long as it takes, never giving up after MAX_ATTEMPTS."""
    from connect_labs.messaging.canopy_bridge import tasks

    monkeypatch.setattr(tasks.erase_in_canopy, "delay", lambda pk: tasks.erase_in_canopy(pk))
    bridge_on.contacts["personalid:amina123"] = {"version": 1, "contact_id": 5}
    detail = "files_remain: a stored file could not be deleted; send the erasure again to finish it"
    bridge_on.fail_next["erase"] = (503, detail)
    row = erasure.erase_worker(worker, by="x")
    row.refresh_from_db()
    assert (row.canopy_status, row.attempts) == ("error", 1)
    assert row.canopy_detail.startswith("files_remain:")
    Erasure.objects.filter(pk=row.pk).update(attempts=erasure.MAX_ATTEMPTS - 1)
    bridge_on.fail_next["erase"] = (503, detail)
    assert erasure.retry_pending() == 1
    row.refresh_from_db()
    assert row.canopy_status == "error"  # not "failed": canopy asked to be sent it again
    # A transient failure after canopy erased part of them does not end the retries either.
    bridge_on.fail_next["erase"] = (502, "bad_gateway: try later")
    assert erasure.retry_pending() == 1
    row.refresh_from_db()
    assert row.canopy_status == "error" and row.canopy_detail.startswith("files_remain earlier; bad_gateway")
    bridge_on.fail_next["erase"] = (502, "bad_gateway: try later")
    assert erasure.retry_pending() == 1
    row.refresh_from_db()
    assert row.canopy_status == "error"
    assert erasure.retry_pending() == 1
    row.refresh_from_db()
    assert row.canopy_status == "erased" and row.canopy_done_at


def test_erasures_stuck_on_files_never_starve_newer_ones(bridge_on, worker):
    from connect_labs.messaging.canopy_bridge.models import Erasure

    stuck = [
        Erasure.objects.create(
            username=f"old{i}", sub=f"personalid:old{i}", requested_by="x", canopy_status="error", attempts=30
        )
        for i in range(100)
    ]
    fresh = Erasure.objects.create(username="new", sub="personalid:new", requested_by="x", canopy_status="pending")
    assert stuck[0].requested_at < fresh.requested_at
    assert erasure.retry_pending() == 100
    fresh.refresh_from_db()
    assert fresh.canopy_status == "not_found" and fresh.canopy_done_at


def test_a_profile_update_signed_before_canopy_erased_the_worker_is_final(bridge_on, worker):
    from connect_labs.messaging.canopy_bridge import profile_push
    from connect_labs.messaging.canopy_bridge.models import ProfilePush

    Address.objects.create(
        worker=worker, channel_source=service.channel_source(PROGRAM), program_id=PROGRAM, channel_id="ch-1"
    )
    assert profile_push.push([worker])["pushed"] == 1
    worker.refresh_from_db()
    assert worker.canopy_contact_id
    # canopy erased the person after labs signed this update (a late delivery).
    bridge_on.contacts.pop(worker.canopy_sub)
    bridge_on.tombstones[worker.canopy_sub] = 2**40
    assert profile_push.push([worker], force=True)["failed"] == 1
    row = ProfilePush.objects.get(worker=worker)
    assert (row.status, row.dirty) == ("refused", False) and row.reason.startswith("erased:")
    worker.refresh_from_db()
    assert worker.canopy_contact_id == ""
    assert worker.canopy_sub not in bridge_on.contacts


def test_the_sync_never_writes_an_erased_worker_again(worker):
    from connect_labs.workers.models import OpportunitySync
    from connect_labs.workers.sync import _apply

    erasure.erase_worker(worker, by="x")
    opp = OpportunitySync.objects.create(opportunity_id=9001, program_id=PROGRAM)

    class _R:
        def __init__(self, rows):
            self.rows = rows

            class C:
                def as_dict(self):
                    return {"complete": True}

            self.coverage = C()

    from connect_labs.workers import sync

    pulled = {
        sync.USER_DATA: _R([{"username": "amina123", "name": "Amina Bello", "phone": PHONE}]),
        sync.VISITS_THIN: _R([]),
    }
    for kind in sync.SNAPSHOT_KINDS:
        pulled[kind] = _R([])
    _apply(opp, pulled, usernames=None, now=timezone.now())
    worker.refresh_from_db()
    assert worker.name == "" and worker.phone == ""
    assert not WorkerMembership.objects.filter(worker=worker).exists()


# --- the Connect Worker page --------------------------------------------------------------------


@pytest.fixture
def page_access(monkeypatch):
    """Everyone may open the profile (Connect's wider rule); in labs, all but `funder` hold the program."""
    from connect_labs.labs.access import scopes

    def holdings(caller):
        held = frozenset() if scopes._user_of(caller).username == "funder" else frozenset({PROGRAM})
        return scopes.Holdings(frozenset(), frozenset(), held)

    monkeypatch.setattr(scopes, "holdings", holdings)
    view = WorkerView(opportunity_ids=frozenset({9001}), program_ids=frozenset({PROGRAM}), restricted=True)
    monkeypatch.setattr("connect_labs.workers.views.view_for", lambda *a, **k: view)
    monkeypatch.setattr("connect_labs.workers.access.view_for", lambda *a, **k: view)
    monkeypatch.setattr(
        "connect_labs.workers.views.build_profile",
        lambda *a, **k: {
            "worker": {},
            "memberships": [],
            "timeline": [],
            "timeline_total": 0,
            "timeline_counts": {},
            "facts": [],
            "notes": [],
            "coverage": {"statements": []},
        },
    )
    return view


def test_the_page_shows_what_the_agent_saw_and_staff_can_erase(client, worker, page_access, settings):
    thread = _full_record(worker)
    reply = _say(thread, "Hello from the agent", direction="out", brief_hash="h" * 64)
    staff = User.objects.create(username="joy", email="joy@dimagi.com")
    client.force_login(staff)
    page = client.get(f"/labs/workers/{worker.username}/")
    assert page.status_code == 200
    assert b"What the agent saw" in page.content and b"Hello from the agent" in page.content
    assert f"/messaging/canopy/brief/{'h' * 64}/".encode() in page.content and b"erase-button" in page.content
    brief = client.get(f"/messaging/canopy/brief/{'h' * 64}/")
    assert brief.status_code == 200 and b"brief" in brief.content and reply.text.encode() in brief.content
    wrong = client.post(f"/labs/workers/{worker.username}/", {"action": "erase", "confirm_username": "nope"})
    assert wrong.status_code == 302 and not Erasure.objects.exists()
    done = client.post(f"/labs/workers/{worker.username}/", {"action": "erase", "confirm_username": worker.username})
    assert done.status_code == 302 and Erasure.objects.get().requested_by == "joy"
    assert client.get(f"/labs/workers/{worker.username}/").status_code == 404


def test_a_funder_who_may_see_the_profile_does_not_read_the_conversations(client, worker, page_access):
    """Decided 2026-11-23: conversation text follows the Inbox's rule (messaging.access), not the
    profile's wider one, so a funder or supervising organisation sees no agent reply or brief."""
    thread = _full_record(worker)
    _say(thread, "Hello from the agent", direction="out", brief_hash="f" * 64)
    WorkerBrief.objects.create(hash="f" * 64, body="BRIEF-BODY", worker=worker, thread=thread, sections={})
    client.force_login(User.objects.create(username="funder", email="grants@funder.org"))
    page = client.get(f"/labs/workers/{worker.username}/")
    assert page.status_code == 200 and b"Hello from the agent" not in page.content
    assert client.get(f"/messaging/canopy/brief/{'f' * 64}/").status_code == 404


def test_only_staff_may_erase(client, worker, page_access):
    _full_record(worker)
    person = User.objects.create(username="pm", email="pm@partner.org")
    client.force_login(person)
    page = client.get(f"/labs/workers/{worker.username}/")
    assert b"erase-button" not in page.content
    response = client.post(f"/labs/workers/{worker.username}/", {"action": "erase", "confirm_username": "amina123"})
    assert response.status_code == 404 and not Erasure.objects.exists()


def test_a_brief_outside_the_readers_programs_is_404(client, worker, monkeypatch):
    thread = _full_record(worker)
    del thread
    none = WorkerView(opportunity_ids=frozenset(), program_ids=frozenset())
    monkeypatch.setattr("connect_labs.workers.access.view_for", lambda *a, **k: none)
    client.force_login(User.objects.create(username="o", email="o@x.org"))
    assert client.get(f"/messaging/canopy/brief/{'h' * 64}/").status_code == 404
    assert ConnectWorker.objects.count() == 1
