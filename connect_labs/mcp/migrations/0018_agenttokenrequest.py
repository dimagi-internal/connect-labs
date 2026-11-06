import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import connect_labs.mcp.models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp", "0017_ace_may_self_mint"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentTokenRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "ref",
                    models.CharField(
                        default=connect_labs.mcp.models._pending_ref, editable=False, max_length=64, unique=True
                    ),
                ),
                ("reason", models.CharField(max_length=200)),
                ("recipient_key", models.CharField(max_length=44)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Waiting for the person"),
                            ("approved", "Approved, not yet collected"),
                            ("declined", "Declined"),
                            ("claimed", "Collected by the agent"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("sealed_token", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "mcp_agent_token_request",
            },
        ),
    ]
