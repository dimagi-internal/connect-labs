"""Add UNIQUE constraints to the cache tables.

Concurrent pipeline runs targeting the same (opportunity, config) used to
silently both succeed in writing their full row sets, producing 2x rows
in cache (incident on opp 765: 172120 rows for 86060 actual visits — the
delete-then-insert "guard" took no row locks when the table was empty,
so two concurrent bulk_creates both committed). UNIQUE constraints
serialize writers: one wins, the loser raises IntegrityError, which the
cache layer surfaces as CacheConcurrencyError so the pipeline can stop
with a clear message.

Pre-migration step: dedupe existing rows. The CREATE UNIQUE INDEX would
fail on any table with current dups, so we keep MIN(id) per key and
delete the rest. This is the *cache*; data loss here is fine — a fresh
pipeline run rebuilds it.
"""

from django.db import migrations, models


def dedupe_caches(apps, schema_editor):
    """Drop duplicate rows so the unique indexes can be created."""
    cursor = schema_editor.connection.cursor()
    # Each statement keeps the lowest-id row per unique key and deletes
    # the rest. Using a CTE so we don't materialize the whole table.
    statements = [
        # RawVisitCache: dedupe on (opportunity_id, visit_count, visit_id)
        """
        DELETE FROM labs_raw_visit_cache
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM labs_raw_visit_cache
            GROUP BY opportunity_id, visit_count, visit_id
        )
        """,
        # ComputedVisitCache: dedupe on (opportunity_id, config_hash, visit_id)
        """
        DELETE FROM labs_computed_visit_cache
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM labs_computed_visit_cache
            GROUP BY opportunity_id, config_hash, visit_id
        )
        """,
        # ComputedFLWCache: dedupe on (opportunity_id, config_hash, username)
        """
        DELETE FROM labs_computed_flw_cache
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM labs_computed_flw_cache
            GROUP BY opportunity_id, config_hash, username
        )
        """,
        # ComputedEntityCache: dedupe on (opportunity_id, config_hash, entity_id)
        """
        DELETE FROM labs_computed_entity_cache
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM labs_computed_entity_cache
            GROUP BY opportunity_id, config_hash, entity_id
        )
        """,
    ]
    for stmt in statements:
        cursor.execute(stmt)


def noop_reverse(apps, schema_editor):
    # Nothing to restore — duplicates were corrupt cache state. A
    # downstream pipeline run will rebuild whatever was needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0010_computedentitycache"),
    ]

    operations = [
        migrations.RunPython(dedupe_caches, noop_reverse),
        migrations.AddConstraint(
            model_name="rawvisitcache",
            constraint=models.UniqueConstraint(
                fields=("opportunity_id", "visit_count", "visit_id"),
                name="uniq_raw_visit_cache_opp_count_visit",
            ),
        ),
        migrations.AddConstraint(
            model_name="computedvisitcache",
            constraint=models.UniqueConstraint(
                fields=("opportunity_id", "config_hash", "visit_id"),
                name="uniq_computed_visit_cache_opp_config_visit",
            ),
        ),
        migrations.AddConstraint(
            model_name="computedflwcache",
            constraint=models.UniqueConstraint(
                fields=("opportunity_id", "config_hash", "username"),
                name="uniq_computed_flw_cache_opp_config_username",
            ),
        ),
        migrations.AddConstraint(
            model_name="computedentitycache",
            constraint=models.UniqueConstraint(
                fields=("opportunity_id", "config_hash", "entity_id"),
                name="uniq_computed_entity_cache_opp_config_entity",
            ),
        ),
    ]
