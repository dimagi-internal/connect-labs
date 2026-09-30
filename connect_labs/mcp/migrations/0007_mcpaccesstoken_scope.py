from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mcp", "0006_canopy_probe_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpaccesstoken",
            name="scope",
            field=models.CharField(
                choices=[("full", "Full access"), ("no-uservisit-data", "No user visit data (no writes)")],
                default="full",
                help_text="What the token may reach. See connect_labs/mcp/token_scopes.py.",
                max_length=20,
            ),
        ),
    ]
