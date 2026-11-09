"""Schedule communications' reconcile on celery beat, every ten minutes.

Superseded by 0005, which removes it: reconciling now runs only when a person
clicks "Check delivery".

Same pattern as `supply_chain/migrations/0012_seed_alert_beat_task.py`: the
worker runs beat with the DatabaseScheduler, so this PeriodicTask row is what
makes it fire. It sends no mail: it settles records and retries follow-ups.
"""

from django.db import migrations

TASK_NAME = "communications_reconcile"
TASK_PATH = "connect_labs.communications.tasks.reconcile_communications"


def create_periodic_task(apps, schema_editor):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=10, period=IntervalSchedule.MINUTES)
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={"task": TASK_PATH, "interval": schedule, "crontab": None},
    )


def delete_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("communications", "0003_followups_and_unknown_delivery"),
        # The live PeriodicTask model is used above: its tables must exist first.
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
