# What a raw slot kept up to date in place, and each stage computed from it, have read so far
# (``analysis.backends.sql.incremental``). The raw cache's new column is added bare, which rewrites
# nothing, and its index is built CONCURRENTLY and IF NOT EXISTS: the table is read by every pipeline.

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("labs", "0042_encrypt_oauth_tokens"),
    ]

    operations = [
        migrations.CreateModel(
            name="RawSlotState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("opportunity_id", models.IntegerField()),
                ("slot_id", models.IntegerField()),
                ("source_cursor", models.JSONField(blank=True, default=dict)),
                ("changed_through", models.DateTimeField(blank=True, null=True)),
                ("generation", models.IntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "labs_raw_slot_state"},
        ),
        migrations.CreateModel(
            name="StageState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("opportunity_id", models.IntegerField()),
                ("pipeline_id", models.IntegerField(blank=True, null=True)),
                ("config_hash", models.CharField(max_length=32)),
                ("stage", models.CharField(max_length=16)),
                ("slot_id", models.IntegerField(blank=True, null=True)),
                ("changed_through", models.DateTimeField(blank=True, null=True)),
                ("cursor", models.JSONField(blank=True, default=dict)),
                ("generation", models.IntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "labs_stage_state"},
        ),
        migrations.AddConstraint(
            model_name="rawslotstate",
            constraint=models.UniqueConstraint(fields=("opportunity_id", "slot_id"), name="uniq_raw_slot_state"),
        ),
        migrations.AddConstraint(
            model_name="stagestate",
            constraint=models.UniqueConstraint(
                fields=("opportunity_id", "pipeline_id", "config_hash", "stage"), name="uniq_stage_state"
            ),
        ),
        migrations.AddField(
            model_name="rawvisitcache",
            name="changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="rawvisitcache",
                    index=models.Index(
                        fields=["opportunity_id", "pipeline_id", "changed_at", "visit_id"],
                        name="raw_visit_slot_changed",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS raw_visit_slot_changed "
                        "ON labs_raw_visit_cache (opportunity_id, pipeline_id, changed_at, visit_id)"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS raw_visit_slot_changed",
                ),
            ],
        ),
    ]
