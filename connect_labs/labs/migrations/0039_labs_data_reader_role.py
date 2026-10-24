"""A database role for the Data page's SQL, so Postgres itself contains it.

Until now a Data query ran as the app's own database role, which can read every
table: the SQL validator was the only thing between a query and, say,
``public.users_user``. This gives the query a role of its own and makes the
database enforce the boundary too:

* ``labs_data_reader`` (NOLOGIN) holds SELECT on the visit and pipeline caches and
  on ``labsdata_scope_grant``, and nothing else. A query naming any other table is
  "permission denied", whatever the validator thought of it.
* Row-level security on those caches lets that role see a row only when its
  ``(opportunity_id, pipeline_id)`` is in ``labsdata_scope_grant``. The engine
  writes the grants inside the query's own transaction and always rolls it back,
  so they are never committed and no other session ever sees them: each query
  sees exactly the opportunities (and pipelines) it was authorised for.
* Every other role keeps reading and writing the caches as before
  (``labsdata_everyone_else``), whether or not it owns the tables.

Roles are per cluster, not per database, so creating it tolerates one that exists
(test workers migrate in parallel against one server). On RDS the labs DB role
has CREATEROLE through ``rds_superuser``. The engine fails closed if the role is
missing: no Data query runs without it (``labsdata/engine.py``).
"""

from django.db import migrations

ROLE = "labs_data_reader"
CACHES = (
    "labs_raw_visit_cache",
    "labs_computed_visit_cache",
    "labs_computed_entity_cache",
    "labs_computed_flw_cache",
)

CREATE_ROLE = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
        CREATE ROLE {ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;
DO $$
BEGIN
    -- The app's role switches to it with SET LOCAL ROLE. A superuser needs no grant.
    IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        EXECUTE format('GRANT {ROLE} TO %I', current_user);
    END IF;
END
$$;
"""

GRANTS_TABLE = f"""
CREATE TABLE labsdata_scope_grant (
    opportunity_id integer NOT NULL,
    pipeline_id integer NOT NULL
);
GRANT USAGE ON SCHEMA public TO {ROLE};
GRANT SELECT ON labsdata_scope_grant TO {ROLE};
"""


def _protect(table: str) -> str:
    return f"""
GRANT SELECT ON {table} TO {ROLE};
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY labsdata_reader_scoped ON {table} FOR SELECT TO {ROLE}
    USING ((opportunity_id, pipeline_id) IN (SELECT g.opportunity_id, g.pipeline_id FROM public.labsdata_scope_grant g));
CREATE POLICY labsdata_everyone_else ON {table} FOR ALL TO PUBLIC
    USING (current_user <> '{ROLE}') WITH CHECK (current_user <> '{ROLE}');
"""


def _unprotect(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS labsdata_everyone_else ON {table};
DROP POLICY IF EXISTS labsdata_reader_scoped ON {table};
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
REVOKE SELECT ON {table} FROM {ROLE};
"""


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0038_pg_stat_statements"),
    ]

    operations = [
        migrations.RunSQL(CREATE_ROLE, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(
            GRANTS_TABLE,
            reverse_sql=f"REVOKE USAGE ON SCHEMA public FROM {ROLE}; DROP TABLE IF EXISTS labsdata_scope_grant;",
        ),
        *[migrations.RunSQL(_protect(t), reverse_sql=_unprotect(t)) for t in CACHES],
    ]
