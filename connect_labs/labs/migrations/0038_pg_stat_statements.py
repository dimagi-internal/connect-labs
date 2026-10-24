"""Create the `pg_stat_statements` extension, so the database can say what it spends time on.

RDS already preloads the library (`shared_preload_libraries` on
labs-jj-postgres), so it has been counting all along; the view that exposes
those counts only exists once the extension is created in the database. On
2026-10-23 a DB at ~70% baseline CPU had to be attributed by sampling
`pg_stat_activity` for 150s, because this view was missing. With it, the same
question is one query:

    SELECT calls, round(total_exec_time) AS ms, query
    FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;

Needs `rds_superuser`, which the labs DB role has. Locally and in CI the
extension creates fine without the preload; only reading the view needs it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0037_canopy_agent_ticks_sensitive_data"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE EXTENSION IF NOT EXISTS pg_stat_statements",
            reverse_sql="DROP EXTENSION IF EXISTS pg_stat_statements",
        ),
    ]
