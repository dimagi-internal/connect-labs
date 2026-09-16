from datetime import datetime, timezone

import pytest

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User
from connect_labs.workflow.schedules import INTERVAL, WEEKLY


@pytest.mark.django_db
def test_recompute_next_run_sets_future_datetime():
    user = User.objects.create(username="alice")
    sched = WorkflowSchedule.objects.create(
        definition_id=42,
        opportunity_id=1237,
        owner=user,
        definition_name="Weekly Review",
        cadence=WEEKLY,
        hour=6,
        day_of_week=0,  # Monday
    )
    now = datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc)  # Wed
    sched.recompute_next_run(now)
    assert sched.next_run_at == datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)


@pytest.mark.django_db
def test_unique_per_definition_scope_owner():
    user = User.objects.create(username="bob")
    WorkflowSchedule.objects.create(
        definition_id=7, opportunity_id=99, owner=user, definition_name="A", cadence="daily", hour=6
    )
    with pytest.raises(Exception):
        WorkflowSchedule.objects.create(
            definition_id=7, opportunity_id=99, owner=user, definition_name="A", cadence="daily", hour=8
        )


@pytest.mark.django_db
def test_recompute_next_run_honours_the_interval_cadence():
    """interval_hours has to reach compute_next_run through the model, not just
    the pure helper -- the ticker recomputes via this path, so a dropped kwarg
    here would silently fall through to the ValueError branch at fire time."""
    user = User.objects.create(username="carol")
    sched = WorkflowSchedule.objects.create(
        definition_id=13005,
        program_id=217,
        owner=user,
        definition_name="Ward Progress Tracker",
        cadence=INTERVAL,
        hour=0,
        interval_hours=6,
    )
    now = datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc)
    sched.recompute_next_run(now)
    # 00/06/12/18 grid -> next slot after 09:30 is 12:00 the same day
    assert sched.next_run_at == datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


@pytest.mark.django_db
def test_interval_cadence_without_interval_hours_is_rejected_at_compute_time():
    """The column is nullable so the calendar cadences can leave it unset; an
    interval schedule saved without it must fail loudly rather than schedule
    itself at some arbitrary default."""
    user = User.objects.create(username="dave")
    sched = WorkflowSchedule.objects.create(
        definition_id=13005,
        program_id=217,
        owner=user,
        definition_name="Ward Progress Tracker",
        cadence=INTERVAL,
        hour=0,
    )
    with pytest.raises(ValueError, match="interval_hours must be one of"):
        sched.recompute_next_run(datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc))
