from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0002_playbook_runs_and_event_cursor"),
    ]

    operations = [
        migrations.AlterField(
            model_name=model,
            name="transport",
            field=models.CharField(
                choices=[("personalid", "Personalid"), ("simulated", "Simulated")],
                default="personalid",
                max_length=20,
            ),
        )
        for model in ("address", "message", "transportevent")
    ]
