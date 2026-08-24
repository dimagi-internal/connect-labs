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
    assert run_default.call_args.kwargs["cadence"] == "daily"
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


@pytest.mark.django_db
def test_run_scheduled_workflow_forwards_cchq_token_when_available():
    """A schedule whose owner has authorized CommCare HQ gets that token forwarded
    to run_default_for_definition, so templates reading cchq_forms/cchq_cases
    pipelines can run headlessly (this is the whole point of the CCHQ token work —
    see get_valid_cchq_access_token)."""
    sched = _make_schedule(username="hascchq")
    with (
        mock.patch("connect_labs.workflow.tasks.get_valid_access_token", return_value="tok"),
        mock.patch("connect_labs.workflow.tasks.get_valid_cchq_access_token", return_value="cchq-tok"),
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
    run_default.assert_called_once_with(
        mock.ANY, access_token="tok", request=None, cchq_access_token="cchq-tok", cadence="daily"
    )


@pytest.mark.django_db
def test_run_scheduled_workflow_missing_cchq_token_does_not_block_run():
    """A schedule whose owner never authorized CommCare HQ (or whose CCHQ token
    died) must still run normally for templates that don't need CCHQ data —
    only cchq_access_token becomes None, nothing else changes."""
    from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError

    sched = _make_schedule(username="nocchq")
    with (
        mock.patch("connect_labs.workflow.tasks.get_valid_access_token", return_value="tok"),
        mock.patch("connect_labs.workflow.tasks.get_valid_cchq_access_token", side_effect=CCHQTokenError("no cchq")),
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
    run_default.assert_called_once_with(
        mock.ANY, access_token="tok", request=None, cchq_access_token=None, cadence="daily"
    )


# ── A hook that RETURNS failure must not be recorded as OK ────────────────────
#
# Creator templates audit several opportunities per run and record a per-opportunity
# failure rather than abandoning the rest, so they report trouble by returning it at
# least as often as by raising. The return value used to be discarded and the status set
# to OK unconditionally, so a schedule could fail the same way every night and still look
# healthy in the admin.


def _run_with_result(sched, result):
    with (
        mock.patch("connect_labs.workflow.tasks.get_valid_access_token", return_value="tok"),
        mock.patch("connect_labs.workflow.tasks.WorkflowDataAccess") as DA,
        mock.patch("connect_labs.workflow.tasks.run_default_for_definition", return_value=result),
    ):
        DA.return_value.get_definition.return_value = mock.Mock(id=42)
        from connect_labs.workflow.tasks import run_scheduled_workflow

        run_scheduled_workflow(sched.id)
    sched.refresh_from_db()
    return sched


@pytest.mark.django_db
def test_a_returned_failure_is_recorded_as_failed():
    sched = _run_with_result(
        _make_schedule(),
        {"status": "failed", "errors": ["1236: HTTP 500 from record creation"], "sessions_created": 0},
    )

    assert sched.last_status == WorkflowSchedule.STATUS_FAILED
    assert "HTTP 500" in sched.last_error


@pytest.mark.django_db
def test_a_top_level_error_key_is_recorded_too():
    """run_default reports "nothing to audit" that way rather than in `errors`."""
    sched = _run_with_result(
        _make_schedule(), {"status": "failed", "error": "config.schedule_defaults.opportunity_ids is empty"}
    )

    assert sched.last_status == WorkflowSchedule.STATUS_FAILED
    assert "opportunity_ids is empty" in sched.last_error


@pytest.mark.django_db
def test_a_partial_run_stays_ok_but_still_surfaces_the_failures():
    """ "Created audits for 4 of 5 opportunities" is a success worth acting on, and
    last_error is the only field the admin shows it through."""
    sched = _run_with_result(
        _make_schedule(), {"status": "ready", "sessions_created": 4, "errors": ["1790: unreachable"]}
    )

    assert sched.last_status == WorkflowSchedule.STATUS_OK
    assert "1790: unreachable" in sched.last_error


@pytest.mark.django_db
def test_a_clean_run_records_no_error():
    sched = _run_with_result(_make_schedule(), {"status": "ready", "sessions_created": 5, "errors": []})

    assert sched.last_status == WorkflowSchedule.STATUS_OK
    assert sched.last_error == ""


@pytest.mark.django_db
def test_an_empty_window_is_not_a_failure():
    """A quiet period audits nothing and that is correct."""
    sched = _run_with_result(_make_schedule(), {"status": "empty", "sessions_created": 0, "errors": []})

    assert sched.last_status == WorkflowSchedule.STATUS_OK
    assert sched.last_error == ""


@pytest.mark.django_db
def test_a_dry_run_is_not_a_failure():
    sched = _run_with_result(_make_schedule(), {"status": "dry_run", "sessions_created": 0, "planned": [{}]})

    assert sched.last_status == WorkflowSchedule.STATUS_OK


@pytest.mark.django_db
def test_a_hook_returning_something_other_than_a_dict_is_still_ok():
    """Older hooks return None; that is not a failure signal."""
    sched = _run_with_result(_make_schedule(), None)

    assert sched.last_status == WorkflowSchedule.STATUS_OK
    assert sched.last_error == ""


@pytest.mark.django_db
def test_a_long_error_list_is_truncated_to_the_column_width():
    sched = _run_with_result(_make_schedule(), {"status": "failed", "errors": ["x" * 500 for _ in range(20)]})

    assert sched.last_status == WorkflowSchedule.STATUS_FAILED
    assert len(sched.last_error) <= 2000
