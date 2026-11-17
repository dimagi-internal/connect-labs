"""What an operator can count on: access is audited, big reads fail clearly, sync failures are loud."""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from django.utils import timezone as dj_timezone

from connect_labs.audit_trail.models import Action, AuditEvent, Outcome
from connect_labs.hqdata.access import ProjectAccessDenied, create_project
from connect_labs.hqdata.alerts import stale_projects, sync_state_changed
from connect_labs.hqdata.models import HQForm, HQProject
from connect_labs.hqdata.pipelines import PipelineProblem, run_pipeline
from connect_labs.hqdata.tests.conftest import make_pipeline
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


@pytest.fixture
def alice():
    return UserFactory()


@pytest.fixture
def project(alice):
    return create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)


def add_form(project, form_id):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=form_id,
        xmlns="http://x/1",
        app_id="app1",
        username="ann",
        received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
        form_json={"id": form_id, "received_on": "2026-11-01T10:00:00Z", "form": {"meta": {"username": "ann"}}},
    )


@pytest.fixture
def pipeline(project, alice):
    return make_pipeline(
        project,
        created_by=alice,
        schema={
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}],
        },
    )


def events(project, action):
    return AuditEvent.objects.filter(resource_type="commcare_project", resource_id=str(project.pk), action=action)


def test_a_pipeline_read_is_on_the_audit_trail_without_the_data(alice, project, pipeline):
    add_form(project, "f1")
    run_pipeline(alice, project, pipeline)
    event = events(project, Action.READ).get()
    assert event.user_id == alice.pk and event.outcome == Outcome.SUCCESS
    assert event.metadata == {"kind": "pipeline"}  # an id and a kind: no domain, row or answer


def test_a_refused_read_is_recorded_as_a_denial(project, pipeline):
    stranger = UserFactory()
    with pytest.raises(ProjectAccessDenied):
        run_pipeline(stranger, project, pipeline)
    # require_project refuses before the pipeline gate; the pipeline gate itself records its own refusal.
    from connect_labs.hqdata.pipeline_source import check_project_access

    class Source:
        project_id = project.pk

    with pytest.raises(ProjectAccessDenied):
        check_project_access(Source, stranger)
    denial = events(project, Action.ACCESS_DENIED).get()
    assert denial.outcome == Outcome.FAILURE and denial.user_id == stranger.pk


def test_a_project_too_big_to_read_whole_says_so_before_loading_it(alice, project, pipeline, settings):
    settings.HQ_STORE_PIPELINE_MAX_ROWS = 2
    for i in range(3):
        add_form(project, f"f{i}")
    with pytest.raises(PipelineProblem, match=r"3 rows.*at most 2"):
        run_pipeline(alice, project, pipeline, force_refresh=True)


def test_a_failing_sync_alerts_once_not_on_every_retry(project, caplog):
    with caplog.at_level(logging.ERROR, logger="connect_labs.hqdata.alerts"):
        sync_state_changed(project.pk, HQProject.STATUS_ACTIVE, HQProject.STATUS_ERROR, "boom")
        sync_state_changed(project.pk, HQProject.STATUS_ERROR, HQProject.STATUS_ERROR, "boom")
        sync_state_changed(project.pk, HQProject.STATUS_ERROR, HQProject.STATUS_ACTIVE)
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert events(project, Action.UPDATE).filter(outcome=Outcome.FAILURE).count() == 1


def test_a_project_that_stopped_syncing_is_reported(project, caplog):
    HQProject.objects.filter(pk=project.pk).update(
        storage_mode=HQProject.STORAGE_PERSISTENT,
        sync_interval_minutes=60,
        last_synced_at=dj_timezone.now() - timedelta(hours=10),
    )
    with caplog.at_level(logging.ERROR, logger="connect_labs.hqdata.alerts"):
        assert [p.pk for p in stale_projects()] == [project.pk]
    assert "has not synced" in caplog.text


def test_fresh_paused_and_live_projects_are_not_stale(project):
    now = dj_timezone.now()
    HQProject.objects.filter(pk=project.pk).update(
        storage_mode=HQProject.STORAGE_PERSISTENT, last_synced_at=now - timedelta(minutes=30)
    )
    assert stale_projects(now) == []
    HQProject.objects.filter(pk=project.pk).update(last_synced_at=now - timedelta(days=2), status="paused")
    assert stale_projects(now) == []
    HQProject.objects.filter(pk=project.pk).update(status="active", storage_mode=HQProject.STORAGE_LIVE)
    assert stale_projects(now) == []
