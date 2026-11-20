# Every stored form and case records the transaction that last wrote it (``models.ChangeNumber``), so a reader
# can take what changed since it last read, in the order it was committed.
#
# Written by hand, in steps that never rewrite the tables. A column added WITH a volatile default fills every
# existing row in one long rewrite of a table holding millions of forms, so the column is added bare (rows from
# before it read NULL, which sorts first) and the default set after, which touches no row. The indexes are built
# CONCURRENTLY and IF NOT EXISTS for the reason given in 0011.

import django.db.models.functions.comparison
from django.db import migrations, models

import connect_labs.hqdata.models


def _column(table: str) -> list:
    return [
        migrations.RunSQL(
            sql=f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS change_seq bigint NULL",
            reverse_sql=f"ALTER TABLE {table} DROP COLUMN IF EXISTS change_seq",
        ),
        migrations.RunSQL(
            sql=f"ALTER TABLE {table} ALTER COLUMN change_seq SET DEFAULT txid_current()",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]


def _index(name: str, table: str):
    return migrations.RunSQL(
        sql=(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
            f"ON {table} (project_id, (COALESCE(change_seq, 0)), id)"
        ),
        reverse_sql=f"DROP INDEX CONCURRENTLY IF EXISTS {name}",
    )


def _state_index(name: str):
    return models.Index(
        models.F("project_id"),
        django.db.models.functions.comparison.Coalesce(models.F("change_seq"), models.Value(0)),
        models.F("id"),
        name=name,
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("hqdata", "0011_form_synced_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name=model,
                    name="change_seq",
                    field=models.BigIntegerField(
                        blank=True, db_default=connect_labs.hqdata.models.ChangeNumber(), null=True
                    ),
                )
                for model in ("hqform", "hqcase")
            ]
            + [
                migrations.AddIndex(model_name="hqform", index=_state_index("hqform_project_change")),
                migrations.AddIndex(model_name="hqcase", index=_state_index("hqcase_project_change")),
            ],
            database_operations=_column("labs_commcare_form")
            + _column("labs_commcare_case")
            + [
                _index("hqform_project_change", "labs_commcare_form"),
                _index("hqcase_project_change", "labs_commcare_case"),
            ],
        ),
    ]
