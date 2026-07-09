import json
from unittest import mock

import pytest
from django.urls import reverse

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User


@pytest.fixture
def logged_in(client):
    user = User.objects.create(username="alice")
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_upsert_creates_schedule_for_schedulable_workflow(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()

    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        # ``mock.Mock(name=...)`` is reserved — it names the mock, not a ``.name``
        # attribute — so set it after construction (see test_delete_backup.py).
        definition = mock.Mock(id=42, template_type="program_audit_creator")
        definition.name = "Weekly Review"
        DA.return_value.get_definition.return_value = definition
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(
            url,
            data=json.dumps({"cadence": "weekly", "hour": 6, "day_of_week": 0}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    assert resp.status_code == 200
    sched = WorkflowSchedule.objects.get(definition_id=42, owner=logged_in)
    assert sched.cadence == "weekly"
    assert sched.next_run_at is not None
    assert sched.definition_name == "Weekly Review"


@pytest.mark.django_db
def test_upsert_rejects_non_schedulable(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=False),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="performance_review")
        definition.name = "Not schedulable"
        DA.return_value.get_definition.return_value = definition
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(
            url,
            data=json.dumps({"cadence": "daily", "hour": 6}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_upsert_non_numeric_day_of_week_is_400(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess") as DA,
    ):
        definition = mock.Mock(id=42, template_type="program_audit_creator")
        definition.name = "Weekly Review"
        DA.return_value.get_definition.return_value = definition
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(
            url,
            data=json.dumps({"cadence": "weekly", "hour": 6, "day_of_week": "nope"}),
            content_type="application/json",
        )
    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_upsert_non_object_json_body_is_400(client, logged_in):
    session = client.session
    session["labs_oauth"] = {"access_token": "tok"}
    session.save()
    with (
        mock.patch("connect_labs.workflow.views._resolve_schedule_scope", return_value=(1237, None)),
        mock.patch("connect_labs.workflow.views.template_supports_default_run", return_value=True),
        mock.patch("connect_labs.workflow.views.WorkflowDataAccess"),
    ):
        url = reverse("labs:workflow:api_schedule_upsert", args=[42])
        resp = client.post(url, data=json.dumps([]), content_type="application/json")
    assert resp.status_code == 400
    assert not WorkflowSchedule.objects.filter(definition_id=42).exists()


@pytest.mark.django_db
def test_delete_removes_only_owners_schedule(client, logged_in):
    sched = WorkflowSchedule.objects.create(
        definition_id=42, opportunity_id=1237, owner=logged_in, definition_name="A", cadence="daily", hour=6
    )
    url = reverse("labs:workflow:api_schedule_delete", args=[sched.id])
    resp = client.post(url)
    assert resp.status_code == 200
    assert not WorkflowSchedule.objects.filter(pk=sched.pk).exists()


@pytest.mark.django_db
def test_toggle_flips_enabled(client, logged_in):
    sched = WorkflowSchedule.objects.create(
        definition_id=42,
        opportunity_id=1237,
        owner=logged_in,
        definition_name="A",
        cadence="daily",
        hour=6,
        enabled=True,
    )
    url = reverse("labs:workflow:api_schedule_toggle", args=[sched.id])
    resp = client.post(url)
    assert resp.status_code == 200
    sched.refresh_from_db()
    assert sched.enabled is False
