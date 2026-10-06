"""Schedule the clone top-up on celery beat, weekly (Mondays 06:00 UTC).

Same pattern as 0039_seed_visit_consumption_beat_task. Each run records the
invented deliveries that have fallen due on every clone seeded by
supply_seed_clone_supply; a run with none seeded reads one table and stops.
"""

from django.db import migrations

TASK_NAME = "supply_chain_clone_top_up"
TASK_PATH = "connect_labs.supply_chain.tasks.top_up_clone_supply"


def create_periodic_task(apps, schema_editor):
    from django_celery_beat.models import CrontabSchedule, PeriodicTask

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="6", day_of_week="1", day_of_month="*", month_of_year="*", timezone="UTC"
    )
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={"task": TASK_PATH, "crontab": schedule, "interval": None},
    )


def delete_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=TASK_NAME, task=TASK_PATH).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0046_award_open_at_decision"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, delete_periodic_task, hints={"run_on_secondary": False}),
    ]
