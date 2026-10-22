from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0031_canopy_agent_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="canopyagentsettings",
            name="offered_agents",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
