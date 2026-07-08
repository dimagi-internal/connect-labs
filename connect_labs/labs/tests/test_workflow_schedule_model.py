from datetime import datetime, timezone

import pytest

from connect_labs.labs.models import WorkflowSchedule
from connect_labs.users.models import User
from connect_labs.workflow.schedules import WEEKLY


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
