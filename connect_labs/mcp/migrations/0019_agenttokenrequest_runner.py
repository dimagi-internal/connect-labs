from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp", "0018_agenttokenrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="agenttokenrequest",
            name="runner",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="agenttokenrequest",
            name="runner_key_kind",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="agenttokenrequest",
            name="runner_agent",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="agenttokenrequest",
            name="runner_serves_agent",
            field=models.BooleanField(default=False),
        ),
    ]
