"""Drop the beat entry that swept kept results under their old name.

The sweep is now ``sweep-kept-results`` (``connect_labs.mcp.tasks.sweep_kept_results``).
The DatabaseScheduler adds entries from ``CELERY_BEAT_SCHEDULE`` but never removes one, so
the old row would keep enqueuing a task name the worker no longer has, which crashes its
consumer (config/celery_app.py).
"""

from django.db import migrations

OLD_NAME = "sweep-labs-result-artifacts"


def drop(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=OLD_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("mcp", "0014_kept_result"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(drop, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
