"""Let ``labs_data_reader`` be a login of its own, so a Data query has no app role to return to.

0039 made Data SQL run as ``labs_data_reader`` by ``SET LOCAL ROLE`` from the app's
own login. That contains a statement only as long as the statement cannot switch
back (``RESET ROLE``, ``set_config('role', ...)``), which only the validator
prevented. Connecting as ``labs_data_reader`` itself removes that: the session has
no other role to become. Its grants then cannot be rows written inside the query's
own transaction (a separate connection never sees them), so this changes how a
grant is found:

* ``labsdata_scope_grant`` rows carry a ``token`` (64 random hex characters, one per
  statement) and an ``expires_at``. The app writes and commits them on its own
  connection, runs the statement, and deletes them.
* The reader no longer reads that table at all. ``labsdata_granted()``, a SECURITY
  DEFINER function, returns the pairs whose token is the session's
  ``labsdata.token`` setting, and the row-level policies on the caches read that.
  The reader learns its token from its connection's startup options, which no
  other session can see; it cannot list the table to find another statement's.
* Per-database defaults for the role (read-only, empty search_path, timeouts),
  which a login session starts with. The engine sets them again on every
  transaction; these are a floor.

``SET LOCAL ROLE`` keeps working the same way (grants inserted with a token, the
token set for the transaction, all rolled back): it is what runs where no reader
password is configured (development, tests). Turning the login on -- ``LOGIN`` and
its password -- is not done here, because the password is a secret: the engine
does it with ``labsdata/reader_login.py`` when ``LABSDATA_READER_PASSWORD`` is set.
"""

from django.db import migrations

ROLE = "labs_data_reader"
CACHES = (
    "labs_raw_visit_cache",
    "labs_computed_visit_cache",
    "labs_computed_entity_cache",
    "labs_computed_flw_cache",
)

TOKENS = f"""
ALTER TABLE labsdata_scope_grant
    ADD COLUMN token text NOT NULL CHECK (length(token) >= 32),
    ADD COLUMN expires_at timestamptz NOT NULL;
CREATE INDEX labsdata_scope_grant_token ON labsdata_scope_grant (token);
REVOKE SELECT ON labsdata_scope_grant FROM {ROLE};

CREATE FUNCTION public.labsdata_granted()
    RETURNS TABLE (opportunity_id integer, pipeline_id integer)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT g.opportunity_id, g.pipeline_id
    FROM public.labsdata_scope_grant g
    WHERE g.token = current_setting('labsdata.token', true) AND g.expires_at > now()
$$;
REVOKE ALL ON FUNCTION public.labsdata_granted() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.labsdata_granted() TO {ROLE};
"""

UNTOKENS = f"""
DROP FUNCTION IF EXISTS public.labsdata_granted();
GRANT SELECT ON labsdata_scope_grant TO {ROLE};
DROP INDEX IF EXISTS labsdata_scope_grant_token;
ALTER TABLE labsdata_scope_grant DROP COLUMN IF EXISTS token, DROP COLUMN IF EXISTS expires_at;
"""

# IN DATABASE: these live per (role, database), so test databases migrating in
# parallel never update the same catalog row. Defence in depth only, hence a
# warning rather than a failed migration where the app's role may not set them.
ROLE_DEFAULTS = f"""
DO $$
DECLARE
    setting text;
BEGIN
    FOREACH setting IN ARRAY ARRAY[
        'default_transaction_read_only = on',
        'search_path = labs_explorer_no_tables',
        'statement_timeout = ''20s''',
        'idle_in_transaction_session_timeout = ''30s'''
    ] LOOP
        EXECUTE format('ALTER ROLE {ROLE} IN DATABASE %I SET ', current_database()) || setting;
    END LOOP;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE WARNING 'labs_data_reader: could not set its database defaults (%)', SQLERRM;
END
$$;
"""

UNSET_ROLE_DEFAULTS = f"""
DO $$
BEGIN
    EXECUTE format('ALTER ROLE {ROLE} IN DATABASE %I RESET ALL', current_database());
EXCEPTION WHEN insufficient_privilege THEN
    NULL;
END
$$;
"""


def _policy(table: str, source: str) -> str:
    return f"""
DROP POLICY labsdata_reader_scoped ON {table};
CREATE POLICY labsdata_reader_scoped ON {table} FOR SELECT TO {ROLE}
    USING ((opportunity_id, pipeline_id) IN (SELECT g.opportunity_id, g.pipeline_id FROM {source} g));
"""


class Migration(migrations.Migration):
    dependencies = [
        ("labs", "0039_labs_data_reader_role"),
    ]

    operations = [
        migrations.RunSQL(TOKENS, reverse_sql=UNTOKENS),
        *[
            migrations.RunSQL(
                _policy(t, "public.labsdata_granted()"), reverse_sql=_policy(t, "public.labsdata_scope_grant")
            )
            for t in CACHES
        ],
        migrations.RunSQL(ROLE_DEFAULTS, reverse_sql=UNSET_ROLE_DEFAULTS),
    ]
