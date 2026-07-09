import pytest
from django.test import override_settings
from django.urls import reverse

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.labs.tests.test_settings import LABS_SETTINGS
from connect_labs.users.models import User


@pytest.fixture
def dimagi_user(db):
    return User.objects.create_user(username="staff", email="staff@dimagi.com", password="pw")


@pytest.fixture
def external_user(db):
    return User.objects.create_user(username="ext", email="partner@external.com", password="pw")


def _make_schedule(owner, **overrides):
    defaults = dict(
        definition_id=42,
        opportunity_id=1237,
        owner=owner,
        definition_name="Weekly Review",
        cadence="weekly",
        hour=6,
        day_of_week=0,
    )
    defaults.update(overrides)
    return WorkflowSchedule.objects.create(**defaults)


@override_settings(**LABS_SETTINGS)
def test_schedule_list_shows_all_owners_schedules(client, dimagi_user, external_user):
    # A schedule owned by someone OTHER than the viewing admin must still appear.
    _make_schedule(external_user, definition_name="Someone Elses Review")
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:schedules"))
    assert resp.status_code == 200
    assert b"Someone Elses Review" in resp.content
    assert b"Scheduled Workflows" in resp.content


@override_settings(**LABS_SETTINGS)
def test_schedule_list_forbidden_for_external_user(client, external_user):
    client.force_login(external_user)
    resp = client.get(reverse("labs_admin:schedules"))
    assert resp.status_code == 403


@override_settings(**LABS_SETTINGS)
def test_admin_delete_removes_any_owners_schedule(client, dimagi_user, external_user):
    sched = _make_schedule(external_user)  # owned by external user, not the admin
    client.force_login(dimagi_user)
    resp = client.post(reverse("labs_admin:schedule_delete", args=[sched.id]))
    assert resp.status_code == 302  # redirects back to the list
    assert not WorkflowSchedule.objects.filter(pk=sched.pk).exists()


@override_settings(**LABS_SETTINGS)
def test_admin_toggle_flips_any_owners_schedule(client, dimagi_user, external_user):
    sched = _make_schedule(external_user, enabled=True)
    client.force_login(dimagi_user)
    resp = client.post(reverse("labs_admin:schedule_toggle", args=[sched.id]))
    assert resp.status_code == 302
    sched.refresh_from_db()
    assert sched.enabled is False
