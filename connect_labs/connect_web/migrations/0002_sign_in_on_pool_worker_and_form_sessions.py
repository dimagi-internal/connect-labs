"""The worker's PersonalID sign-in and HQ tokens move from the checkout (WorkerSession) to the
worker (PoolWorker), so a new checkout no longer runs a full recovery; and the form sessions labs
opened move out of the Django session into their own table.

Data: each open session's password, access token (and expiry) and HQ tokens are copied to its
pool worker (there is at most one open session per worker). Ended sessions hold nothing: a release
used to wipe them.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import connect_labs.utils.encrypted_fields

MOVED = ("personalid_password", "access_token", "access_token_expires_at", "hq_tokens")


def copy_sign_in_to_workers(apps, schema_editor):
    WorkerSession = apps.get_model("connect_web", "WorkerSession")
    PoolWorker = apps.get_model("connect_web", "PoolWorker")
    for session in WorkerSession.objects.filter(ended_at__isnull=True).order_by("started_at"):
        values = {name: getattr(session, name) for name in MOVED}
        if not (values["personalid_password"] or values["access_token"] or values["hq_tokens"]):
            continue
        worker = PoolWorker.objects.get(pk=session.pool_worker_id)
        for name, value in values.items():
            setattr(worker, name, value)
        worker.save(update_fields=list(MOVED))


class Migration(migrations.Migration):
    dependencies = [
        ("connect_web", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="poolworker",
            name="personalid_password",
            field=connect_labs.utils.encrypted_fields.EncryptedTextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="poolworker",
            name="access_token",
            field=connect_labs.utils.encrypted_fields.EncryptedTextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="poolworker",
            name="access_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="poolworker",
            name="hq_tokens",
            field=connect_labs.utils.encrypted_fields.EncryptedTextField(blank=True, default=""),
        ),
        migrations.RunPython(copy_sign_in_to_workers, migrations.RunPython.noop, hints={"run_on_secondary": False}),
        migrations.RemoveField(model_name="workersession", name="personalid_password"),
        migrations.RemoveField(model_name="workersession", name="access_token"),
        migrations.RemoveField(model_name="workersession", name="access_token_expires_at"),
        migrations.RemoveField(model_name="workersession", name="hq_tokens"),
        migrations.CreateModel(
            name="FormSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_id", models.CharField(max_length=64)),
                ("opportunity_id", models.CharField(max_length=64)),
                ("app_kind", models.CharField(max_length=16)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "worker_session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="form_sessions",
                        to="connect_web.workersession",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("worker_session", "session_id"), name="connect_web_one_form_session_row"
                    )
                ],
            },
        ),
    ]
