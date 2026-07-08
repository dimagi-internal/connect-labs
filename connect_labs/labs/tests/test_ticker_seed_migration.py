import pytest
from django_celery_beat.models import PeriodicTask


@pytest.mark.django_db
def test_ticker_periodic_task_seeded():
    task = PeriodicTask.objects.get(name="run_due_workflow_schedules")
    assert task.task == "connect_labs.workflow.tasks.run_due_workflow_schedules"
    assert task.interval.every == 15
    assert task.interval.period == "minutes"
