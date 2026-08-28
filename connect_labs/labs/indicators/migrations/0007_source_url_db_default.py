"""Give ``source_url`` a database-level default.

Django's AddField backfilled the existing rows but left no default on the
column, so a process still holding the pre-migration model class — its INSERT
omitting the column entirely — hit a NOT NULL violation and died. That is
exactly what happened to a six-hour WorldPop ingest: it ran fine while it was
updating rows and fell over the first time it inserted a new one.

Long-running ingests are normal here, so schema changes will sometimes land
under one. A column default makes that survivable rather than fatal.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("indicators", "0006_alter_indicatorvalue_source_alter_ingestrun_source")]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE labs_indicator_value ALTER COLUMN source_url SET DEFAULT '';",
            reverse_sql="ALTER TABLE labs_indicator_value ALTER COLUMN source_url DROP DEFAULT;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE labs_indicator_value ALTER COLUMN method SET DEFAULT '';",
            reverse_sql="ALTER TABLE labs_indicator_value ALTER COLUMN method DROP DEFAULT;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE labs_indicator_value ALTER COLUMN source_ref SET DEFAULT '';",
            reverse_sql="ALTER TABLE labs_indicator_value ALTER COLUMN source_ref DROP DEFAULT;",
        ),
    ]
