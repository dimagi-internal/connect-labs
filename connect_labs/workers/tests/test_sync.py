"""connect_worker_sync: upserts, events from snapshot diffs, coverage, state, scheduling."""

import json
from datetime import timedelta

import httpx
import pytest
from django.core.management import call_command
from django.utils import timezone

from connect_labs.workers import events as ev
from connect_labs.workers import sync, tasks
from connect_labs.workers.connect_facade import get_source
from connect_labs.workers.models import (
    Confidence,
    ConnectWorker,
    OpportunitySync,
    Stage,
    WorkerEvent,
    WorkerMembership,
    WorkerSnapshot,
    WorkerState,
)
from connect_labs.workers.ocs_link import link_ocs_tasks, ocs_sessions_for
from connect_labs.workers.tests.fake_connect import FakeConnect, opp_path

pytestmark = pytest.mark.django_db

ALICE, BOB = "alice_pid", "bob_pid"


def _user(username, **kw):
    row = {
        "username": username,
        "name": username.title(),
        "phone": "+254700000001",
        "user_invite_status": "accepted",
        "suspended": False,
        "suspension_reason": "",
        "suspension_date": None,
        "invited_date": "2026-10-01T10:00:00Z",
        "date_learn_started": "2026-10-02T10:00:00Z",
        "completed_learn_date": None,
        "last_active": None,
        "date_claimed": None,
        "payment_accrued": 0,
        "claim_limits": [],
    }
    row.update(kw)
    return row


def _task(**kw):
    row = {
        "id": 5,
        "assigned_task_id": "11111111-1111-1111-1111-111111111111",
        "task_type": 11,
        "task_type_name": "Coaching",
        "username": ALICE,
        "status": "assigned",
        "due_date": "2026-10-20",
        "date_created": "2026-10-10T10:00:00Z",
        "completed_at": None,
    }
    row.update(kw)
    return row


def _payment(**kw):
    row = {"username": ALICE, "created_at": "2026-10-05T10:00:00Z", "amount": 500, "payment_unit": 3}
    row.update(kw)
    return row


def _fake(users=None, tasks_=None, payments=None, **extra):
    lists = {
        opp_path(1, "user_data"): users if users is not None else [_user(ALICE), _user(BOB)],
        opp_path(1, "completed_module"): [
            {"username": ALICE, "module": 7, "date": "2026-10-03T10:00:00Z", "duration": "0:10:00"}
        ],
        opp_path(1, "assessment"): [],
        opp_path(1, "completed_works"): [],
        opp_path(1, "payment"): payments if payments is not None else [_payment()],
        opp_path(1, "task_types"): [{"id": 11, "task_type_id": "tt", "name": "Coaching", "slug": "c", "mode": "ocs"}],
        opp_path(1, "assigned_tasks"): tasks_ if tasks_ is not None else [_task()],
        opp_path(1, "audit_report_entries"): [],
        opp_path(1, "work_areas"): [],
    }
    lists.update(extra.pop("lists", {}))
    objects = {
        "/export/opportunity/1/": {"id": 1, "learn_app": {"learn_modules": [{"id": 7, "name": "Hand washing"}]}},
        "/export/opp_org_program_list/": {
            "organizations": [],
            "programs": [],
            "opportunities": [{"id": 1, "name": "Opp One", "program": 9, "organization": "org-a", "is_active": True}],
        },
    }
    return FakeConnect(lists=lists, objects=objects, **extra)


def _opp():
    return OpportunitySync.objects.create(opportunity_id=1, name="Opp One", program_id=9, is_active=True)


def _run(fake, **kw):
    with get_source(fake.client()) as source:
        return sync.sync_opportunity(source, OpportunitySync.objects.get(opportunity_id=1), **kw)


def _kinds(username=ALICE):
    return sorted(WorkerEvent.objects.filter(worker__username=username).values_list("kind", flat=True))


def test_first_sync_builds_workers_memberships_history_and_state():
    _opp()
    report = _run(_fake())
    assert report["workers"] == 2
    alice = ConnectWorker.objects.get(username=ALICE)
    assert alice.phone == "+254700000001"
    m = WorkerMembership.objects.get(worker=alice, opportunity_id=1)
    assert m.program_id == 9 and m.visible
    # The phone and suspension reason never sit in a snapshot.
    assert "phone" not in m.snapshot and "suspension_reason" not in m.snapshot
    assert _kinds() == sorted(
        [
            ev.INVITED,
            ev.LEARN_STARTED,
            ev.MODULE_COMPLETED,
            ev.PAYMENT,
            ev.TASK_ASSIGNED,
            ev.STATE_CHANGED,
        ]
    )
    module = WorkerEvent.objects.get(worker=alice, kind=ev.MODULE_COMPLETED)
    assert module.payload["module_name"] == "Hand washing" and module.confidence == Confidence.EXACT
    assert WorkerState.objects.get(membership=m).stage == Stage.LEARNING
    opp = OpportunitySync.objects.get(opportunity_id=1)
    assert opp.last_synced_at and opp.coverage["payment"]["complete"] is True
    assert opp.coverage["visits_thin"]["complete"] is False  # no visit cache in this test


def test_a_second_identical_sync_writes_nothing():
    _opp()
    _run(_fake())
    before = WorkerEvent.objects.count()
    report = _run(_fake())
    assert report["events"] == 0 and WorkerEvent.objects.count() == before


def test_changes_between_pulls_become_events():
    _opp()
    _run(_fake())
    later = _fake(
        users=[
            _user(ALICE, suspended=True, suspension_date="2026-10-15T00:00:00Z", suspension_reason="fraud suspicion"),
            _user(BOB),
        ],
        tasks_=[_task(due_date="2026-10-25")],
        payments=[],
    )
    _run(later)
    alice = ConnectWorker.objects.get(username=ALICE)
    suspended = WorkerEvent.objects.get(worker=alice, kind=ev.SUSPENDED)
    assert suspended.confidence == Confidence.EXACT and "fraud" not in json.dumps(suspended.payload)
    due = WorkerEvent.objects.get(worker=alice, kind=ev.TASK_DUE_CHANGED)
    assert due.confidence == Confidence.OBSERVED and due.payload["from"] == "2026-10-20"
    removed = WorkerEvent.objects.get(worker=alice, kind=ev.PAYMENT, payload__removed=True)
    assert removed.confidence == Confidence.OBSERVED
    assert WorkerSnapshot.objects.get(worker=alice, kind="payment").gone_at is not None
    m = WorkerMembership.objects.get(worker=alice)
    assert m.suspension_reason == "fraud suspicion"  # kept, encrypted, staff only
    assert m.state.stage == Stage.SUSPENDED
    assert WorkerEvent.objects.filter(worker=alice, kind=ev.STATE_CHANGED, payload__stage_to="suspended").exists()

    _run(_fake(tasks_=[_task(status="completed", completed_at="2026-10-21T09:00:00Z")]))
    assert WorkerEvent.objects.get(worker=alice, kind=ev.UNSUSPENDED).confidence == Confidence.OBSERVED
    done = WorkerEvent.objects.get(worker=alice, kind=ev.TASK_COMPLETED)
    assert done.confidence == Confidence.EXACT


def test_a_one_worker_sync_leaves_everyone_else_alone():
    _opp()
    _run(_fake(payments=[_payment(), _payment(username=BOB, created_at="2026-10-06T10:00:00Z")]))
    _run(_fake(payments=[_payment()]), usernames={ALICE})
    bob_payment = WorkerSnapshot.objects.get(worker__username=BOB, kind="payment")
    assert bob_payment.gone_at is None


def test_visits_from_the_pulse_tail_become_visit_and_review_events():
    from connect_labs.pulse.models import PulseEvent

    _opp()
    now = timezone.now()
    PulseEvent.objects.create(
        connect_visit_id=77,
        opportunity_id=1,
        field_ts=now - timedelta(hours=2),
        sync_ts=now - timedelta(hours=1),
        status="approved",
        flagged=True,
        flag_type="duration",
        review_status="agree",
        worker_hash=ALICE,
    )
    _run(_fake())
    visit = WorkerEvent.objects.get(kind=ev.VISIT)
    assert visit.worker.username == ALICE and visit.payload["flagged"] is True
    assert WorkerEvent.objects.filter(kind=ev.VISIT_REVIEWED).count() == 1
    opp = OpportunitySync.objects.get(opportunity_id=1)
    assert opp.visits_watermark is not None
    assert WorkerState.objects.get(membership__worker__username=ALICE).facets["verification"]["flagged_visits"] == 1


def test_refresh_marks_what_the_identity_can_no_longer_read():
    OpportunitySync.objects.create(opportunity_id=99, name="Old")
    worker = ConnectWorker.objects.create(username=ALICE)
    WorkerMembership.objects.create(worker=worker, opportunity_id=99)
    with get_source(_fake().client()) as source:
        report = sync.refresh_opportunities(source)
    assert report["visible"] == 1 and report["no_longer_visible"] == 1
    assert OpportunitySync.objects.get(opportunity_id=1).visible
    assert not OpportunitySync.objects.get(opportunity_id=99).visible
    assert not WorkerMembership.objects.get(opportunity_id=99).visible


def test_a_failed_sync_is_recorded_and_backs_off():
    opp = _opp()
    fake = _fake()
    fake.missing.add(opp_path(1, "payment"))
    with pytest.raises(Exception):
        _run(fake)
    opp.refresh_from_db()
    assert opp.consecutive_failures == 1 and "404" in opp.last_error
    assert not ConnectWorker.objects.exists()  # nothing half-written
    assert opp not in tasks.due_opportunities(now=timezone.now())
    assert opp in tasks.due_opportunities(now=timezone.now() + timedelta(minutes=5))


def test_cadence_follows_activity():
    now = timezone.now()
    active = OpportunitySync.objects.create(opportunity_id=1, is_active=True, last_attempt_at=now, last_synced_at=now)
    idle = OpportunitySync.objects.create(opportunity_id=2, is_active=False, last_attempt_at=now, last_synced_at=now)
    new = OpportunitySync.objects.create(opportunity_id=3, is_active=True)
    soon = tasks.due_opportunities(now=now + timedelta(minutes=31))
    assert active in soon and new in soon and idle not in soon
    assert idle in tasks.due_opportunities(now=now + timedelta(hours=25))


def test_sync_worker_discovers_opportunities_and_links_nothing_without_ocs(settings):
    settings.OCS_API_KEY = ""
    _opp()
    with get_source(_fake().client()) as source:
        report = sync.sync_worker(BOB, source=source, discover=True)
    assert report["opportunities"] == [1] and not report["errors"]
    assert set(WorkerMembership.objects.values_list("worker__username", flat=True)) == {BOB}


def test_management_command_runs_one_worker(monkeypatch, capsys):
    _opp()
    fake = _fake()
    monkeypatch.setattr("connect_labs.workers.connect_facade.get_source", lambda **kw: get_source(fake.client()))
    call_command("connect_worker_sync", "--worker", ALICE)
    out = json.loads(capsys.readouterr().out)
    assert out["username"] == ALICE and out["opportunities"] == [1]


def _ocs_client(sessions):
    def handler(request):
        if request.url.path == "/api/participants":
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [{"identifier": ALICE, "data": [{"chatbot_id": "bot-1", "chatbot": "Coach"}]}],
                },
            )
        if request.url.path == "/api/sessions/":
            return httpx.Response(200, json={"next": None, "results": sessions})
        return httpx.Response(404)

    return httpx.Client(base_url="https://ocs.example", transport=httpx.MockTransport(handler))


def test_ocs_sessions_are_found_by_participant_and_linked_to_their_task():
    _opp()
    _run(_fake())
    alice = ConnectWorker.objects.get(username=ALICE)
    sessions = [
        {
            "id": "sess-1",
            "experiment": {"id": "bot-1", "name": "Coach"},
            "participant": {"identifier": ALICE},
            "created_at": "2026-10-11T10:00:00Z",
            "participant_data": {"connectTaskId": "11111111-1111-1111-1111-111111111111"},
        },
        {"id": "sess-2", "participant": {"identifier": "someone_else"}, "created_at": "2026-10-12T10:00:00Z"},
        {"id": "sess-3", "participant": {"identifier": ALICE}, "created_at": "2026-10-13T10:00:00Z"},
    ]
    refs = ocs_sessions_for(alice, client=_ocs_client(sessions))
    assert [r["session_id"] for r in refs] == ["sess-3", "sess-1"]
    assert link_ocs_tasks(alice, client=_ocs_client(sessions)) == 1
    event = WorkerEvent.objects.get(kind=ev.OCS_SESSION)
    assert event.source == "ocs" and event.ref["ocs_session_id"] == "sess-1" and event.opportunity_id == 1
    assert link_ocs_tasks(alice, client=_ocs_client(sessions)) == 0  # idempotent
