from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workflow", "0003_drop_legacy_template_workflows"),
    ]

    operations = [
        migrations.CreateModel(
            name="CoachChart",
            fields=[
                ("id", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("run_id", models.IntegerField()),
                ("opportunity_id", models.IntegerField(blank=True, null=True)),
                ("program_id", models.IntegerField(blank=True, null=True)),
                ("worker_key", models.CharField(max_length=200)),
                ("request", models.JSONField()),
                ("chart", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["run_id", "worker_key"], name="workflow_co_run_id_a463e8_idx")],
            },
        ),
    ]
