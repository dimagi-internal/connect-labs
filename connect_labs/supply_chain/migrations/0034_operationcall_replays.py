from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("supply_chain", "0033_history_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationcall",
            name="replay_count",
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="operationcall",
            name="last_replayed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
