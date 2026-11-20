# Built CONCURRENTLY and IF NOT EXISTS: the forms table holds millions of rows and is written by every sync, so a plain
# CREATE INDEX would block syncs, and the build outlasts the deploy's migration wait. If a deploy gives up waiting while
# the build carries on, the next deploy's migrate must not fail on the index already being there.

from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("hqdata", "0010_campaign_records"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="hqform",
                    index=models.Index(fields=["project_id", "synced_at"], name="hqform_project_synced"),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS hqform_project_synced "
                        "ON labs_commcare_form (project_id, synced_at)"
                    ),
                    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS hqform_project_synced",
                ),
            ],
        ),
    ]
