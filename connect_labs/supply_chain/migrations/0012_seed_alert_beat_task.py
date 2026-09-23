"""Schedule the supply alert run on celery beat.

The worker runs `celery ... worker --beat` with the DatabaseScheduler, so the
`PeriodicTask` row is what actually makes this fire -- the same pattern as
`labs/migrations/0016_seed_workflow_schedule_ticker.py`.

Every five minutes, so an "immediate" subscription hears within five minutes
of a check becoming true or a movement being recorded. A run with no active
subscriptions touches one table and stops.

This is an unattended send path, which docs/OUTBOUND_EMAIL.md asks to be
audited before mail is enabled anywhere. It mails only addresses a programme
member typed into a subscription, and only while that subscription is active.
"""

from django.db import migrations

TASK_NAME = "supply_chain_send_alerts"
TASK_PATH = "connect_labs.supply_chain.tasks.send_supply_alerts"


def create_periodic_task(apps, schema_editor):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(every=5, period=IntervalSchedule.MINUTES)
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={"task": TASK_PATH, "interval": schedule, "crontab": None},
    )


def delete_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0011_alerts_and_update_links"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
