from django.db import migrations


def create_periodic_task(apps, schema_editor):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=15,
        period=IntervalSchedule.MINUTES,
    )
    PeriodicTask.objects.update_or_create(
        name="run_due_workflow_schedules",
        defaults={
            "task": "connect_labs.workflow.tasks.run_due_workflow_schedules",
            "interval": schedule,
            "crontab": None,
        },
    )


def delete_periodic_task(apps, schema_editor):
    from django_celery_beat.models import PeriodicTask

    PeriodicTask.objects.filter(
        name="run_due_workflow_schedules",
        task="connect_labs.workflow.tasks.run_due_workflow_schedules",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0015_workflowschedule"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_periodic_task,
            delete_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
