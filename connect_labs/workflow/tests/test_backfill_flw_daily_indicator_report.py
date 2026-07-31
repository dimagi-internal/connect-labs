from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from connect_labs.labs.connect_tokens import ConnectTokenError
from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError
from connect_labs.users.models import User

COMMAND = "backfill_flw_daily_indicator_report"
MODPATH = "connect_labs.workflow.management.commands.backfill_flw_daily_indicator_report"


def _make_definition(definition_id=8061):
    d = mock.Mock()
    d.id = definition_id
    return d


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.run_default")
@mock.patch(f"{MODPATH}.WorkflowDataAccess")
@mock.patch(f"{MODPATH}.get_valid_cchq_access_token")
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_calls_run_default_once_per_day_with_non_overlapping_windows(
    mock_get_token, mock_get_cchq_token, MockWDA, mock_run_default
):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_get_cchq_token.return_value = "cchq-tok"

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = _make_definition()
    MockWDA.return_value = fetch_instance

    mock_run_default.return_value = {"opportunities": {}, "date": "x"}

    out = StringIO()
    call_command(
        COMMAND,
        "--definition",
        "8061",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--days",
        "14",
        stdout=out,
    )

    assert mock_run_default.call_count == 14
    for call in mock_run_default.call_args_list:
        assert call.kwargs["cchq_access_token"] == "cchq-tok"

    windows = [call.kwargs["window"] for call in mock_run_default.call_args_list]

    # Every window is exactly 1 day, with a tz-aware start.
    for start, end in windows:
        assert start.tzinfo is not None
        assert (end - start) == timedelta(days=1)

    # The 14 windows are consecutive, non-overlapping days with no gaps or duplicates.
    sorted_windows = sorted(windows, key=lambda w: w[0])
    assert len({w[0] for w in sorted_windows}) == 14
    for (s1, e1), (s2, _e2) in zip(sorted_windows, sorted_windows[1:]):
        assert e1 == s2

    assert "Backfilled 14 day(s)" in out.getvalue()


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.run_default")
@mock.patch(f"{MODPATH}.WorkflowDataAccess")
@mock.patch(f"{MODPATH}.get_valid_cchq_access_token")
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_proceeds_without_cchq_token_when_owner_never_authorized(
    mock_get_token, mock_get_cchq_token, MockWDA, mock_run_default
):
    """A missing/unauthorized CommCare HQ token must not abort the backfill --
    run_default itself degrades gracefully (indicator #2 becomes None)."""
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_get_cchq_token.side_effect = CCHQTokenError("no CommCare HQ OAuth token stored")

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = _make_definition()
    MockWDA.return_value = fetch_instance
    mock_run_default.return_value = {"opportunities": {}, "date": "x"}

    out = StringIO()
    call_command(
        COMMAND,
        "--definition",
        "8061",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--days",
        "2",
        stdout=out,
    )

    assert mock_run_default.call_count == 2
    for call in mock_run_default.call_args_list:
        assert call.kwargs["cchq_access_token"] is None
    assert "proceeding without one" in out.getvalue()


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.run_default")
@mock.patch(f"{MODPATH}.WorkflowDataAccess")
@mock.patch(f"{MODPATH}.get_valid_cchq_access_token")
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_replace_existing_deletes_only_matching_period_runs(
    mock_get_token, mock_get_cchq_token, MockWDA, mock_run_default
):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_get_cchq_token.return_value = "cchq-tok"
    mock_run_default.return_value = {"opportunities": {}, "date": "x"}

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = _make_definition()
    MockWDA.return_value = fetch_instance

    out = StringIO()
    call_command(
        COMMAND,
        "--definition",
        "8061",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--days",
        "2",
        stdout=out,
    )
    # Without --replace-existing, list_runs/delete_run are never touched.
    fetch_instance.list_runs.assert_not_called()
    fetch_instance.delete_run.assert_not_called()

    windows = [call.kwargs["window"] for call in mock_run_default.call_args_list]
    stale_period = windows[0][0].date().isoformat()  # the first day's own window_start

    stale_run = mock.Mock(id=555, opportunity_id=1973, data={"period_start": stale_period})
    other_run = mock.Mock(id=556, opportunity_id=1976, data={"period_start": "1999-01-01"})  # different day
    fetch_instance.list_runs.return_value = [stale_run, other_run]

    mock_run_default.reset_mock()
    out2 = StringIO()
    call_command(
        COMMAND,
        "--definition",
        "8061",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--days",
        "2",
        "--replace-existing",
        stdout=out2,
    )

    # Only the run matching this exact period_start was deleted.
    fetch_instance.delete_run.assert_any_call(555, delete_linked=True)
    assert mock.call(556, delete_linked=True) not in fetch_instance.delete_run.call_args_list
    assert "Deleted stale run 555" in out2.getvalue()


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.run_default")
@mock.patch(f"{MODPATH}.WorkflowDataAccess")
@mock.patch(f"{MODPATH}.get_valid_cchq_access_token")
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_end_date_anchors_the_window_instead_of_yesterday(
    mock_get_token, mock_get_cchq_token, MockWDA, mock_run_default
):
    """--end-date lets a later backfill reach further back without re-touching
    the more-recent days an earlier (no --end-date) backfill already covered."""
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_get_cchq_token.return_value = "cchq-tok"

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = _make_definition()
    MockWDA.return_value = fetch_instance
    mock_run_default.return_value = {"opportunities": {}, "date": "x"}

    out = StringIO()
    call_command(
        COMMAND,
        "--definition",
        "8061",
        "--program",
        "176",
        "--owner-email",
        "wvink@dimagi.com",
        "--days",
        "40",
        "--end-date",
        "2026-07-16",
        stdout=out,
    )

    assert mock_run_default.call_count == 40
    windows = [call.kwargs["window"] for call in mock_run_default.call_args_list]
    # run_default derives its period_start from the END of the window (see
    # test_run_default_splits_by_opportunity_and_completes_each_run), so that's
    # what determines the WAT calendar day each backfilled run is tagged with.
    period_ends = sorted(w[1].date().isoformat() for w in windows)
    assert period_ends[0] == "2026-06-07"
    assert period_ends[-1] == "2026-07-16"


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.get_valid_cchq_access_token")
@mock.patch(f"{MODPATH}.WorkflowDataAccess")
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_raises_on_malformed_end_date(mock_get_token, MockWDA, mock_get_cchq_token):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_get_cchq_token.return_value = "cchq-tok"
    MockWDA.return_value.get_definition.return_value = _make_definition()

    with pytest.raises(CommandError, match="--end-date must be YYYY-MM-DD"):
        call_command(
            COMMAND,
            "--definition",
            "8061",
            "--program",
            "176",
            "--owner-email",
            "wvink@dimagi.com",
            "--end-date",
            "not-a-date",
        )


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.get_valid_cchq_access_token")
@mock.patch(f"{MODPATH}.WorkflowDataAccess")
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_raises_when_definition_not_found(mock_get_token, MockWDA, mock_get_cchq_token):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.return_value = "connect-tok"
    mock_get_cchq_token.return_value = "cchq-tok"

    fetch_instance = mock.Mock()
    fetch_instance.get_definition.return_value = None
    MockWDA.return_value = fetch_instance

    with pytest.raises(CommandError, match="not found"):
        call_command(
            COMMAND,
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
            COMMAND,
            "--definition",
            "8061",
            "--program",
            "176",
            "--owner-email",
            "nobody@example.com",
        )


@pytest.mark.django_db
@mock.patch(f"{MODPATH}.get_valid_access_token")
def test_backfill_raises_when_connect_token_unavailable(mock_get_token):
    User.objects.create(username="wouter", email="wvink@dimagi.com")
    mock_get_token.side_effect = ConnectTokenError("no token stored")

    with pytest.raises(CommandError, match="no token stored"):
        call_command(
            COMMAND,
            "--definition",
            "8061",
            "--program",
            "176",
            "--owner-email",
            "wvink@dimagi.com",
        )
