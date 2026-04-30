"""Pipeline-discriminate RawVisitCache to fix #116.

Multiple pipelines for the same opportunity used to share this table.
Each pipeline's `store_raw_visits` did a wholesale DELETE+INSERT for the
opp, so the last writer's rows survived and the earlier pipelines' raw
data was lost.

Adds a nullable `pipeline_id` column and replaces the existing unique
constraint to include it. Existing rows are wiped because they have no
pipeline_id and would create constraint conflicts the moment any new
pipeline writes (where pipeline_id IS NULL would conflict with legacy
rows). Cache rebuilds cleanly on the next pipeline run — same approach
as migration 0011.
"""

from django.db import migrations, models


def wipe_legacy_rows(apps, schema_editor):
    """Drop pre-pipeline-id rows so the new constraint can be created."""
    cursor = schema_editor.connection.cursor()
    cursor.execute("DELETE FROM labs_raw_visit_cache")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0011_cache_unique_constraints"),
        ("labs", "0012_haversine_function"),
    ]

    operations = [
        # Step 1: wipe legacy rows (cache; safe to lose, rebuilds on next run)
        migrations.RunPython(wipe_legacy_rows, noop_reverse),
        # Step 2: drop the old unique constraint that didn't include pipeline_id
        migrations.RemoveConstraint(
            model_name="rawvisitcache",
            name="uniq_raw_visit_cache_opp_count_visit",
        ),
        # Step 3: add the pipeline_id column
        migrations.AddField(
            model_name="rawvisitcache",
            name="pipeline_id",
            field=models.IntegerField(blank=True, db_index=True, null=True),
        ),
        # Step 4: add the new unique constraint that includes pipeline_id
        migrations.AddConstraint(
            model_name="rawvisitcache",
            constraint=models.UniqueConstraint(
                fields=("opportunity_id", "pipeline_id", "visit_count", "visit_id"),
                name="uniq_raw_visit_cache_opp_pipe_count_visit",
            ),
        ),
    ]
