# The standard MCP sign-in (connect_labs/mcp/oauth.py): marks an OAuth
# application as an MCP client registered through dynamic client registration.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mcp", "0002_mcpauditlog"),
        migrations.swappable_dependency(settings.OAUTH2_PROVIDER_APPLICATION_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MCPOAuthClient",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "application",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_client",
                        to=settings.OAUTH2_PROVIDER_APPLICATION_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "mcp_oauth_client",
            },
        ),
    ]
