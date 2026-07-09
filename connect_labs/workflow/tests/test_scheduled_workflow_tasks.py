from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from connect_labs.labs.connect_tokens import ConnectReLoginRequired, ConnectTokenError
from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User


def _make_schedule(**overrides):
    user = User.objects.create(username=overrides.pop("username", "alice"))
    defaults = dict(
        definition_id=42,
        opportunity_id=1237,
        owner=user,
        definition_name="Weekly Review",
        cadence="daily",
        hour=6,
        enabled=True,
    )
    defaults.update(overrides)
    return WorkflowSchedule.objects.create(**defaults)


@pytest.mark.django_db
def test_run_scheduled_workflow_success_records_ok():
    sched = _make_schedule()
    with (
        mock.patch("connect_labs.workflow.tasks.get_valid_access_token", return_value="tok"),
        mock.patch("connect_labs.workflow.tasks.WorkflowDataAccess") as DA,
        mock.patch(
            "connect_labs.workflow.tasks.run_default_for_definition", return_value={"ran": True}
        ) as run_default,
    ):
        DA.return_value.get_definition.return_value = mock.Mock(id=42)
        from connect_labs.workflow.tasks import run_scheduled_workflow

        run_scheduled_workflow(sched.id)

    sched.refresh_from_db()
    assert sched.last_status == WorkflowSchedule.STATUS_OK
    assert sched.last_run_at is not None
    run_default.assert_called_once()
    DA.assert_called_once_with(access_token="tok", opportunity_id=1237)


@pytest.mark.django_db
def test_run_scheduled_workflow_program_scoped_constructs_dao_with_program_id():
    sched = _make_schedule(username="pete", opportunity_id=None, program_id=99)
    with (
        mock.patch("connect_labs.workflow.tasks.get_valid_access_token", return_value="tok"),
        mock.patch("connect_labs.workflow.tasks.WorkflowDataAccess") as DA,
        mock.patch("connect_labs.workflow.tasks.run_default_for_definition", return_value={"ran": True}),
    ):
        DA.return_value.get_definition.return_value = mock.Mock(id=42)
        from connect_labs.workflow.tasks import run_scheduled_workflow

        run_scheduled_workflow(sched.id)

    sched.refresh_from_db()
    assert sched.last_status == WorkflowSchedule.STATUS_OK
    DA.assert_called_once_with(access_token="tok", program_id=99)


@pytest.mark.django_db
def test_run_scheduled_workflow_auth_expired_disables():
    sched = _make_schedule(username="bob")
    with mock.patch(
        "connect_labs.workflow.tasks.get_valid_access_token",
        side_effect=ConnectReLoginRequired("dead"),
    ):
        from connect_labs.workflow.tasks import run_scheduled_workflow

        run_scheduled_workflow(sched.id)

    sched.refresh_from_db()
    assert sched.last_status == WorkflowSchedule.STATUS_AUTH_EXPIRED
    assert sched.enabled is False


@pytest.mark.django_db
def test_run_scheduled_workflow_generic_error_stays_enabled():
    sched = _make_schedule(username="carol")
    with (
        mock.patch("connect_labs.workflow.tasks.get_valid_access_token", return_value="tok"),
        mock.patch("connect_labs.workflow.tasks.WorkflowDataAccess") as DA,
        mock.patch("connect_labs.workflow.tasks.run_default_for_definition", side_effect=RuntimeError("boom")),
    ):
        DA.return_value.get_definition.return_value = mock.Mock(id=42)
        from connect_labs.workflow.tasks import run_scheduled_workflow

        run_scheduled_workflow(sched.id)

    sched.refresh_from_db()
    assert sched.last_status == WorkflowSchedule.STATUS_FAILED
    assert sched.enabled is True


@pytest.mark.django_db
def test_ticker_dispatches_only_due_enabled_and_advances():
    now = datetime.now(tz=timezone.utc)
    due = _make_schedule(username="d1")
    WorkflowSchedule.objects.filter(pk=due.pk).update(next_run_at=now - timedelta(minutes=5))
    not_due = _make_schedule(username="d2")
    WorkflowSchedule.objects.filter(pk=not_due.pk).update(next_run_at=now + timedelta(hours=5))
    disabled = _make_schedule(username="d3", enabled=False)
    WorkflowSchedule.objects.filter(pk=disabled.pk).update(next_run_at=now - timedelta(minutes=5))

    with mock.patch("connect_labs.workflow.tasks.run_scheduled_workflow.delay") as delay:
        from connect_labs.workflow.tasks import run_due_workflow_schedules

        run_due_workflow_schedules()

    dispatched_ids = {c.args[0] for c in delay.call_args_list}
    assert dispatched_ids == {due.pk}
    due.refresh_from_db()
    assert due.next_run_at > now  # advanced


@pytest.mark.django_db
def test_ticker_claim_prevents_double_dispatch():
    now = datetime.now(tz=timezone.utc)
    sched = _make_schedule(username="dbl")
    WorkflowSchedule.objects.filter(pk=sched.pk).update(next_run_at=now - timedelta(minutes=5))

    with mock.patch("connect_labs.workflow.tasks.run_scheduled_workflow.delay") as delay:
        from connect_labs.workflow.tasks import run_due_workflow_schedules

        # Simulate a crashed/replayed tick: run the ticker twice back-to-back. The
        # first call claims the row (advances next_run_at into the future), so the
        # second call sees nothing due and dispatches nothing.
        run_due_workflow_schedules()
        run_due_workflow_schedules()

    assert delay.call_count == 1


@pytest.mark.django_db
def test_run_scheduled_workflow_no_token_disables():
    sched = _make_schedule(username="notok")
    with mock.patch(
        "connect_labs.workflow.tasks.get_valid_access_token",
        side_effect=ConnectTokenError("no token"),
    ):
        from connect_labs.workflow.tasks import run_scheduled_workflow

        run_scheduled_workflow(sched.id)

    sched.refresh_from_db()
    assert sched.last_status == WorkflowSchedule.STATUS_AUTH_EXPIRED
    assert sched.enabled is False
