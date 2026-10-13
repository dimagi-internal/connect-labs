"""Run the clone top-up daily (06:00 UTC), not weekly.

0047 scheduled it for Mondays. A clone's deliveries now come every fortnight from
the day before its seed's last one (demo/clone_supply.py), on any weekday, and a
short worker is sent only the fortnight they need: recorded up to six days late,
the delivery left them out in between. Each run still records only what has fallen
due, so running it daily changes nothing else.
"""

from django.db import migrations

TASK_NAME = "supply_chain_clone_top_up"
TASK_PATH = "connect_labs.supply_chain.tasks.top_up_clone_supply"


def _schedule(day_of_week):
    def apply(apps, schema_editor):
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0", hour="6", day_of_week=day_of_week, day_of_month="*", month_of_year="*", timezone="UTC"
        )
        PeriodicTask.objects.update_or_create(
            name=TASK_NAME,
            defaults={"task": TASK_PATH, "crontab": schedule, "interval": None},
        )

    return apply


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0053_drop_supply_workflow_view"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(_schedule("*"), _schedule("1"), hints={"run_on_secondary": False}),
    ]
