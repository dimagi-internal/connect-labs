"""Syncing as durable jobs: who they run as, that they chain, and that the schedule leaves a running one alone."""

import time
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.hqdata import sync as sync_module
from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.jobs import queue_sync, run_sync, sync_budget_seconds
from connect_labs.hqdata.models import HQForm, HQProject, HQSource, HQSyncRun
from connect_labs.hqdata.projects import Refused
from connect_labs.hqdata.tasks import sync_due_projects
from connect_labs.hqdata.tests.test_sync import T0, FakeBackend, form
from connect_labs.mcp.models import MCPJob
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


@pytest.fixture
def owner(db):
    return UserFactory()


@pytest.fixture
def project(owner):
    project = create_project(owner, "demo", hq_host=HOST, can_read_domain=lambda: True)
    HQSource.objects.create(project=project, kind="forms")
    HQProject.objects.filter(pk=project.pk).update(storage_mode="persistent")
    project.refresh_from_db()
    return project


class OrderedBackend:
    """HQ as a delta sees it: rows ordered by indexed_on, and only those at or after `since`."""

    def __init__(self, records):
        self.records = sorted(records, key=lambda r: r.indexed_on)

    def iter_records(self, source, since):
        for record in self.records:
            if since is None or record.indexed_on >= since:
                yield record


@pytest.fixture
def fake_hq():
    """HQ, as the sync sees it: whatever `records` holds."""
    holder = {"backend": FakeBackend([])}
    with mock.patch("connect_labs.hqdata.backends.factory.backend_for", side_effect=lambda p: holder["backend"]):
        yield holder


def test_a_page_queues_a_job_owned_by_the_person_who_asked(project, owner, fake_hq):
    other = UserFactory()
    from connect_labs.hqdata.models import HQProjectMember

    HQProjectMember.objects.create(project=project, user=other, role="member")
    reply = queue_sync(project, user=other, actor="page")
    job = MCPJob.objects.get(pk=reply["job_id"])
    assert (
        job.user == other
        and job.tool_name == "commcare_project_sync"
        and job.arguments == {"project_id": project.pk, "mode": None}
    )
    assert job.actor == "page" and job.restricted is False and job.state == MCPJob.SUCCEEDED  # tests run jobs inline


def test_the_schedule_and_a_continuation_run_as_the_person_whose_sign_in_syncs(project, owner, fake_hq):
    reply = queue_sync(project, actor="scheduler")
    assert MCPJob.objects.get(pk=reply["job_id"]).user == owner


def test_nobody_to_run_it_as_is_refused(project):
    HQProject.objects.filter(pk=project.pk).update(sync_owner=None)
    project.refresh_from_db()
    with pytest.raises(Refused, match="CommCare sign-in"):
        queue_sync(project)


def test_a_sync_already_queued_or_running_is_returned_not_doubled(project, owner):
    live = MCPJob.objects.create(
        id=uuid.uuid4(),
        user=owner,
        tool_name="commcare_project_sync",
        arguments={"project_id": project.pk, "mode": None},
        state=MCPJob.RUNNING,
    )
    reply = queue_sync(project)
    assert reply["already_running"] is True and reply["job_id"] == str(live.pk)
    assert MCPJob.objects.count() == 1
    other = create_project(owner, "other", hq_host=HOST, can_read_domain=lambda: True)
    HQSource.objects.create(project=other, kind="forms")
    with mock.patch("connect_labs.hqdata.jobs.sync_project", return_value={}):
        assert "already_running" not in queue_sync(other)  # another project's sync is not this one's


def test_too_many_jobs_for_one_person_is_a_refusal_with_the_reason(project, owner):
    for i in range(5):
        MCPJob.objects.create(
            id=uuid.uuid4(),
            user=owner,
            tool_name="commcare_project_sync",
            arguments={"project_id": 1000 + i},
            state=MCPJob.QUEUED,
        )
    with pytest.raises(Refused, match="limit"):
        queue_sync(project)


def test_a_job_reads_the_project_into_its_store(project, owner, fake_hq):
    fake_hq["backend"] = FakeBackend([form("f1"), form("f2")])
    reply = queue_sync(project)
    job = MCPJob.objects.get(pk=reply["job_id"])
    assert job.state == MCPJob.SUCCEEDED and job.result["complete"] is True and job.result["rows_read"] == 2
    assert HQForm.objects.filter(project_id=project.pk).count() == 2
    project.refresh_from_db()
    assert project.last_synced_at is not None


def test_a_job_that_runs_out_of_time_stores_what_it_read_and_hands_on_to_a_continuation(
    project, owner, fake_hq, monkeypatch
):
    monkeypatch.setattr(sync_module, "BATCH_SIZE", 1)
    monkeypatch.setattr("connect_labs.hqdata.jobs.sync_budget_seconds", lambda: 0)
    fake_hq["backend"] = OrderedBackend([form(f"f{i}", at=T0 + timedelta(minutes=i * 30)) for i in range(3)])
    reply = queue_sync(project)
    first = MCPJob.objects.get(pk=reply["job_id"])
    assert first.state == MCPJob.SUCCEEDED and first.result["complete"] is False
    assert first.result["continues_as"]
    chain = list(MCPJob.objects.order_by("created_at"))
    assert len(chain) >= 2 and all(j.state == MCPJob.SUCCEEDED for j in chain) and all(j.user == owner for j in chain)
    assert HQForm.objects.filter(project_id=project.pk).count() == 3  # nothing lost between jobs
    assert chain[-1].result["complete"] is True and chain[-1].result["continues_as"] is None
    source = project.sources.get()
    assert source.backfill_complete
    project.refresh_from_db()
    assert project.last_synced_at is not None


def test_a_job_that_fails_does_not_queue_a_continuation(project, owner, fake_hq):
    fake_hq["backend"] = FakeBackend([form("f1"), form("f2")], fail_after=1)
    reply = queue_sync(project)
    job = MCPJob.objects.get(pk=reply["job_id"])
    assert job.result["errors"] and job.result["complete"] is False and job.result["continues_as"] is None
    assert MCPJob.objects.count() == 1


def test_the_job_budget_stays_under_the_workers_limit(settings):
    settings.MCP_JOB_SOFT_TIME_LIMIT = 45 * 60
    assert sync_budget_seconds() == 35 * 60
    settings.MCP_JOB_SOFT_TIME_LIMIT = 8 * 60
    assert sync_budget_seconds() == 5 * 60


# -- the schedule ---------------------------------------------------------------------------


def test_the_schedule_queues_a_due_project_once_and_not_while_one_is_running(project, owner, fake_hq):
    fake_hq["backend"] = FakeBackend([form("f1")])
    first = sync_due_projects()
    assert first["queued"] == 1 and MCPJob.objects.count() == 1
    HQProject.objects.filter(pk=project.pk).update(last_synced_at=None)
    MCPJob.objects.update(state=MCPJob.RUNNING)
    assert sync_due_projects()["queued"] == 0 and MCPJob.objects.count() == 1


def test_the_schedule_leaves_a_project_that_is_not_due_paused_or_unsigned_alone(project, owner):
    with mock.patch("connect_labs.hqdata.tasks.queue_sync") as queue:
        HQProject.objects.filter(pk=project.pk).update(last_synced_at=timezone.now())
        sync_due_projects()
        HQProject.objects.filter(pk=project.pk).update(last_synced_at=None, status="paused")
        sync_due_projects()
        HQProject.objects.filter(pk=project.pk).update(status="needs_reauth")
        sync_due_projects()
        HQProject.objects.filter(pk=project.pk).update(status="active", storage_mode="live")
        sync_due_projects()
    assert not queue.called


def test_a_project_that_just_failed_is_not_retried_on_every_tick(project, owner):
    HQProject.objects.filter(pk=project.pk).update(status="error")
    source = project.sources.get()
    HQSyncRun.objects.create(
        source=source, mode="delta", status="error", started_at=timezone.now() - timedelta(minutes=3)
    )
    with mock.patch("connect_labs.hqdata.tasks.queue_sync") as queue:
        assert sync_due_projects()["skipped"] == 1 and not queue.called
        HQSyncRun.objects.update(started_at=timezone.now() - timedelta(minutes=30))
        sync_due_projects()
    assert queue.called


def test_a_refusal_to_queue_is_logged_and_does_not_stop_the_other_projects(project, owner, caplog):
    other = create_project(owner, "other", hq_host=HOST, can_read_domain=lambda: True)
    HQProject.objects.filter(pk=other.pk).update(storage_mode="persistent")
    calls = []

    def queue(p, **kwargs):
        calls.append(p.pk)
        if p.pk == project.pk:
            raise Refused("nobody")
        return {"job_id": "j"}

    with mock.patch("connect_labs.hqdata.tasks.queue_sync", side_effect=queue):
        result = sync_due_projects()
    assert sorted(calls) == sorted([project.pk, other.pk]) and result["queued"] == 1 and result["skipped"] == 1


def test_run_sync_reports_what_it_did(project, owner):
    with mock.patch("connect_labs.hqdata.jobs.sync_project", return_value={}) as sync:
        out = run_sync(project, owner, "delta", progress=None)
    assert out == {
        "project_id": project.pk,
        "complete": True,
        "rows_read": 0,
        "rows_removed": 0,
        "continues_as": None,
        "errors": [],
    }
    assert sync.call_args.kwargs["mode"] == "delta" and sync.call_args.kwargs["deadline"] > time.monotonic()
