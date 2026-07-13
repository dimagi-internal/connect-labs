from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from connect_labs.labs.connect_tokens import ConnectTokenError
from connect_labs.users.models import User


def _make_definition(definition_id=6621):
    d = mock.Mock()
    d.id = definition_id
    return d


@pytest.mark.django_db
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.run_default")
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.WorkflowDataAccess")
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.get_valid_access_token")
def test_backfill_calls_run_default_once_per_week_with_non_overlapping_windows(
    mock_get_token, MockWDA, mock_run_default
):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = _make_definition()
    MockWDA.return_value = fetch_instance

    mock_run_default.return_value = {"opportunities": {}, "period_start": "x", "period_end": "y"}

    out = StringIO()
    call_command(
        "backfill_flw_weekly_audit_report",
        "--definition",
        "6621",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--weeks",
        "3",
        stdout=out,
    )

    assert mock_run_default.call_count == 3
    windows = [call.kwargs["window"] for call in mock_run_default.call_args_list]

    # Every window is exactly 7 days, Monday 00:00 UTC to the following Monday 00:00 UTC.
    for start, end in windows:
        assert start.weekday() == 0
        assert start.tzinfo is not None
        assert (end - start) == timedelta(days=7)

    # The 3 windows are consecutive, non-overlapping weeks with no gaps.
    sorted_windows = sorted(windows, key=lambda w: w[0])
    for (s1, e1), (s2, _e2) in zip(sorted_windows, sorted_windows[1:]):
        assert e1 == s2

    assert "Backfilled 3 week(s)" in out.getvalue()


@pytest.mark.django_db
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.run_default")
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.WorkflowDataAccess")
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.get_valid_access_token")
def test_backfill_replace_existing_deletes_only_matching_period_runs(mock_get_token, MockWDA, mock_run_default):
    """--replace-existing deletes any pre-existing run(s) for the exact same
    (definition, period_start) before creating the new one -- e.g. after a
    fix to flw_audit_compute.py, so a re-run replaces stale runs computed
    under the old logic instead of piling up duplicates alongside them."""
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_run_default.return_value = {"opportunities": {}, "period_start": "x", "period_end": "y"}

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = _make_definition()
    MockWDA.return_value = fetch_instance

    out = StringIO()
    call_command(
        "backfill_flw_weekly_audit_report",
        "--definition",
        "6621",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--weeks",
        "2",
        stdout=out,
    )
    # Without --replace-existing, list_runs/delete_run are never touched.
    fetch_instance.list_runs.assert_not_called()
    fetch_instance.delete_run.assert_not_called()

    windows = [call.kwargs["window"] for call in mock_run_default.call_args_list]
    stale_period = windows[0][0].date().isoformat()  # the first week's own window_start

    stale_run = mock.Mock(id=555, opportunity_id=1973, data={"period_start": stale_period})
    other_run = mock.Mock(id=556, opportunity_id=1976, data={"period_start": "1999-01-01"})  # different week
    fetch_instance.list_runs.return_value = [stale_run, other_run]

    mock_run_default.reset_mock()
    out2 = StringIO()
    call_command(
        "backfill_flw_weekly_audit_report",
        "--definition",
        "6621",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--weeks",
        "2",
        "--replace-existing",
        stdout=out2,
    )

    # Only the run matching this exact period_start was deleted.
    fetch_instance.delete_run.assert_any_call(555, delete_linked=True)
    assert mock.call(556, delete_linked=True) not in fetch_instance.delete_run.call_args_list
    assert "Deleted stale run 555" in out2.getvalue()


@pytest.mark.django_db
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.WorkflowDataAccess")
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.get_valid_access_token")
def test_backfill_raises_when_definition_not_found(mock_get_token, MockWDA):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = None
    MockWDA.return_value = fetch_instance

    with pytest.raises(CommandError, match="not found"):
        call_command(
            "backfill_flw_weekly_audit_report",
            "--definition",
            "9999",
            "--program",
            "176",
            "--owner-email",
            "wvink@dimagi.com",
        )


@pytest.mark.django_db
def test_backfill_raises_when_owner_email_unknown():
    with pytest.raises(CommandError, match="No user with email"):
        call_command(
            "backfill_flw_weekly_audit_report",
            "--definition",
            "6621",
            "--program",
            "176",
            "--owner-email",
            "nobody@example.com",
        )


@pytest.mark.django_db
@mock.patch("connect_labs.workflow.management.commands.backfill_flw_weekly_audit_report.get_valid_access_token")
def test_backfill_raises_when_token_unavailable(mock_get_token):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.side_effect = ConnectTokenError("no token stored")

    with pytest.raises(CommandError, match="no token stored"):
        call_command(
            "backfill_flw_weekly_audit_report",
            "--definition",
            "6621",
            "--program",
            "176",
            "--owner-email",
            "wvink@dimagi.com",
        )
