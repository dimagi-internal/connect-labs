from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workflow", "0007_drop_sandbox_exemption"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicRunShare",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=64, unique=True)),
                ("definition_id", models.IntegerField()),
                ("run_id", models.IntegerField()),
                ("opportunity_id", models.IntegerField(blank=True, null=True)),
                ("program_id", models.IntegerField(blank=True, null=True)),
                ("workflow_name", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "ended_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("expired", "Expired"),
                            ("ended", "Ended by a person"),
                            ("source_deleted", "The run or its workflow was deleted"),
                        ],
                        max_length=20,
                    ),
                ),
                ("last_viewed_at", models.DateTimeField(blank=True, null=True)),
                ("view_count", models.PositiveIntegerField(default=0)),
                ("payload", models.JSONField(blank=True, null=True)),
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
                (
                    "ended_by",
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
                "indexes": [
                    models.Index(fields=["run_id"], name="workflow_pu_run_id_d5cbe5_idx"),
                    models.Index(fields=["definition_id"], name="workflow_pu_definit_4c104f_idx"),
                ],
            },
        ),
    ]
