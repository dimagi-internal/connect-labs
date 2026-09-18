"""GET on the schedule endpoint, so a report can show when its data was synced.

`WorkflowSchedule.last_run_at` was reachable only from the admin page, so render
code had nothing server-side to show. A report warmed every two hours could only
report when the VIEWER'S OWN browser last pulled, held in localStorage — which
on a device that had never triggered a pull understated freshness by hours
(measured 2026-09-18: a page claiming "18h 36m ago" while the cache behind it
had been refreshed at 04:21 that morning).

The GET is deliberately not scoped to the requesting user: a schedule belongs to
whoever created it, but the cache it warms is shared, so every reader should see
the same sync time.
"""

import json
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User


@pytest.fixture
def logged_in(client):
    user = User.objects.create(username="alice")
    client.force_login(user)
    return user


def _session(client):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()


@pytest.mark.django_db
def test_get_returns_the_schedules_last_run(client, logged_in):
    _session(client)
    ran_at = timezone.now() - timezone.timedelta(hours=2)
    WorkflowSchedule.objects.create(
        definition_id=13005,
        program_id=217,
        owner=logged_in,
        definition_name="Ward Progress Tracker",
        cadence="interval",
        interval_hours=2,
        hour=0,
        last_run_at=ran_at,
        last_status="ok",
    )
    with mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(None, 217)):
        resp = client.get(reverse("labs:workflow:api_schedule_upsert", args=[13005]))

    assert resp.status_code == 200
    sched = json.loads(resp.content)["schedule"]
    assert sched["cadence"] == "interval"
    assert sched["interval_hours"] == 2
    assert sched["last_status"] == "ok"
    assert sched["last_run_at"].startswith(ran_at.isoformat()[:16])


@pytest.mark.django_db
def test_get_returns_null_when_nothing_is_scheduled(client, logged_in):
    """A report with no schedule must render a page, not an error -- the label
    simply has nothing to show."""
    _session(client)
    with mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(None, 217)):
        resp = client.get(reverse("labs:workflow:api_schedule_upsert", args=[13005]))

    assert resp.status_code == 200
    assert json.loads(resp.content)["schedule"] is None


@pytest.mark.django_db
def test_get_reports_another_users_schedule(client, logged_in):
    """The point of the endpoint. The cache is shared, so a viewer who owns no
    schedule still needs the sync time of the one that warmed their data."""
    _session(client)
    owner = User.objects.create(username="akash")
    ran_at = timezone.now() - timezone.timedelta(minutes=30)
    WorkflowSchedule.objects.create(
        definition_id=13005,
        program_id=217,
        owner=owner,
        definition_name="Ward Progress Tracker",
        cadence="interval",
        interval_hours=2,
        hour=0,
        last_run_at=ran_at,
        last_status="ok",
    )
    with mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(None, 217)):
        resp = client.get(reverse("labs:workflow:api_schedule_upsert", args=[13005]))

    assert json.loads(resp.content)["schedule"]["last_run_at"] is not None


@pytest.mark.django_db
def test_get_prefers_a_schedule_that_has_actually_run(client, logged_in):
    """Two schedules on one workflow: the one that has run is the one that can
    answer the question, even if the other was created later."""
    _session(client)
    ran_at = timezone.now() - timezone.timedelta(hours=1)
    WorkflowSchedule.objects.create(
        definition_id=13005, program_id=217, owner=logged_in, definition_name="W",
        cadence="interval", interval_hours=2, hour=0, last_run_at=ran_at, last_status="ok",
    )
    WorkflowSchedule.objects.create(
        definition_id=13005, program_id=217, owner=User.objects.create(username="bob"),
        definition_name="W", cadence="daily", hour=6, last_run_at=None,
    )
    with mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(None, 217)):
        resp = client.get(reverse("labs:workflow:api_schedule_upsert", args=[13005]))

    assert json.loads(resp.content)["schedule"]["last_run_at"] is not None


@pytest.mark.django_db
def test_get_does_not_leak_a_schedule_from_another_scope(client, logged_in):
    """Scope is honoured: a schedule on the same definition id under a different
    program must not be reported."""
    _session(client)
    WorkflowSchedule.objects.create(
        definition_id=13005, program_id=999, owner=logged_in, definition_name="W",
        cadence="interval", interval_hours=2, hour=0, last_run_at=timezone.now(), last_status="ok",
    )
    with mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(None, 217)):
        resp = client.get(reverse("labs:workflow:api_schedule_upsert", args=[13005]))

    assert json.loads(resp.content)["schedule"] is None


@pytest.mark.django_db
def test_post_still_works(client, logged_in):
    """The endpoint gained GET; it must not have lost POST."""
    _session(client)
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(None, 217)),
        mock.patch("connect_labs.workflow.views.definition_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=13005, template_type="performance_review")
        definition.name = "Ward Progress Tracker"
        DA.return_value.get_definition.return_value = definition
        resp = client.post(
            reverse("labs:workflow:api_schedule_upsert", args=[13005]),
            data=json.dumps({"cadence": "interval", "interval_hours": 2, "hour": 0}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    assert resp.status_code == 200
    assert WorkflowSchedule.objects.filter(definition_id=13005, program_id=217).exists()
