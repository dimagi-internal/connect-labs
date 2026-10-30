import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """Verifying that whoever opens an update link can read its recipient's PersonalID chat."""

    dependencies = [
        ("supply_chain", "0059_update_link_sent"),
    ]

    operations = [
        migrations.AddField(
            model_name="updatelink",
            name="require_verification",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name="updatelink",
            name="verify_bot",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.AddField(
            model_name="updatelinksubmission",
            name="verified",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.CreateModel(
            name="LinkVerification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="verifications",
                        to="supply_chain.updatelink",
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="LinkDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("secret_hash", models.CharField(max_length=64, unique=True)),
                ("connect_username", models.CharField(max_length=150)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="devices",
                        to="supply_chain.updatelink",
                    ),
                ),
            ],
        ),
    ]
