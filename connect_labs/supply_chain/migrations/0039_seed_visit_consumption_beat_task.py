"""Schedule the visit reader on celery beat, hourly.

The same pattern as 0012_seed_alert_beat_task: the worker runs beat with the
DatabaseScheduler, so the PeriodicTask row is what makes it fire. A run with
no active rule reads one table and stops; a real programme's rule is skipped
by name (synthetic programmes only, design 2026-09-28 §1).
"""

from django.db import migrations

TASK_NAME = "supply_chain_visit_consumption"
TASK_PATH = "connect_labs.supply_chain.tasks.ingest_visit_consumption"


def create_periodic_task(apps, schema_editor):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=1, period=IntervalSchedule.HOURS)
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={"task": TASK_PATH, "interval": schedule, "crontab": None},
    )


def delete_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0038_worker_visit"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
