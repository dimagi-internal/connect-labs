"""Stop reconciling on a timer: a person runs it from labs ("Check delivery").

Removes the PeriodicTask that 0004 seeded. Nothing in communications runs on
beat any more; `service.reconcile` runs only when a person asks.
"""

from django.db import migrations

TASK_NAME = "communications_reconcile"


def remove_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("communications", "0004_seed_reconcile_beat_task"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(remove_periodic_task, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
