from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("benchmarks", "0005_min_peers_floor_one"),
    ]

    operations = [
        migrations.AddField(
            model_name="benchmarkcohort",
            name="source_workflow_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
    ]
