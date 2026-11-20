"""Create and upgrade the tables labs keeps in a customer's database.

Labs owns exactly the objects named here, in the one schema the customer gave it. A version table
records how far the schema has got, so a later change is a step in ``_STEPS`` applied the next time
the store is used, with no migration for the customer to run and nothing to coordinate.
"""

from __future__ import annotations

import re

from connect_labs.hqdata.models import HQCase, HQForm

VERSION_TABLE = "labs_commcare_schema_version"
SCHEMA_VERSION = 4
SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class SchemaConflict(Exception):
    """The schema holds tables labs did not make that are in its way."""


def _step_1(connection) -> None:
    with connection.schema_editor() as editor:
        editor.create_model(HQForm)
        editor.create_model(HQCase)


def _step_2(connection) -> None:
    """The analysis caches, so a pipeline over this project stages and computes HERE, not in labs."""
    from connect_labs.labs.analysis.backends.sql.models import (
        ComputedEntityCache,
        ComputedFLWCache,
        ComputedVisitCache,
        RawVisitCache,
    )

    with connection.cursor() as cursor:
        for model in (RawVisitCache, ComputedVisitCache, ComputedFLWCache, ComputedEntityCache):
            if _exists(cursor, model._meta.db_table):
                raise SchemaConflict(
                    f"Schema already has a table named {model._meta.db_table} that labs did not create."
                )
    with connection.schema_editor() as editor:
        for model in (RawVisitCache, ComputedVisitCache, ComputedFLWCache, ComputedEntityCache):
            editor.create_model(model)


#: The id columns widened in step 3, as (table, column). HQ case ids reach 86 characters.
_WIDENED = [
    ("labs_commcare_form", "form_id"),
    ("labs_commcare_form", "app_id"),
    ("labs_commcare_form", "build_id"),
    ("labs_commcare_form", "user_id"),
    ("labs_commcare_case", "case_id"),
    ("labs_commcare_case", "owner_id"),
]


def _step_3(connection) -> None:
    """Room for the ids HQ really sends: a case id longer than 64 characters failed the whole sync."""
    with connection.cursor() as cursor:
        for table, column in _WIDENED:
            cursor.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE varchar(255)')


def _step_4(connection) -> None:
    """Change numbers on stored rows, and what a pipeline kept up to date in place has read (``hqdata`` 0012,
    ``labs`` 0043). Each statement is safe to repeat: a store created at step 1 or 2 by today's models already has
    the columns."""
    from connect_labs.labs.analysis.backends.sql.models import RawSlotState, StageState

    with connection.cursor() as cursor:
        for table, index in (
            ("labs_commcare_form", "hqform_project_change"),
            ("labs_commcare_case", "hqcase_project_change"),
        ):
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS change_seq bigint NULL")
            cursor.execute(f"ALTER TABLE {table} ALTER COLUMN change_seq SET DEFAULT txid_current()")
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {index} ON {table} (project_id, (COALESCE(change_seq, 0)), id)"
            )
        cursor.execute("ALTER TABLE labs_raw_visit_cache ADD COLUMN IF NOT EXISTS changed_at timestamptz NULL")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS raw_visit_slot_changed "
            "ON labs_raw_visit_cache (opportunity_id, pipeline_id, changed_at, visit_id)"
        )
        missing = [m for m in (RawSlotState, StageState) if not _exists(cursor, m._meta.db_table)]
    if missing:
        with connection.schema_editor() as editor:
            for model in missing:
                editor.create_model(model)


#: version -> the step that brings a store TO that version.
_STEPS = {1: _step_1, 2: _step_2, 3: _step_3, 4: _step_4}


def _exists(cursor, table: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", [table])
    return cursor.fetchone()[0] is not None


def ensure_schema(connection, schema: str) -> int:
    """Bring the customer's schema to ``SCHEMA_VERSION``. Returns the version it is at."""
    if not SCHEMA_NAME_RE.match(schema):
        raise ValueError(f"{schema!r} is not a usable schema name")
    with connection.cursor() as cursor:
        # CREATE SCHEMA IF NOT EXISTS checks the database CREATE privilege before it looks for
        # the schema, so a customer who made the schema and granted labs only that schema would
        # be refused. Look first.
        cursor.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", [schema])
        if cursor.fetchone() is None:
            cursor.execute(f'CREATE SCHEMA "{schema}"')
        if _exists(cursor, VERSION_TABLE):
            cursor.execute(f"SELECT COALESCE(MAX(version), 0) FROM {VERSION_TABLE}")
            current = cursor.fetchone()[0]
        else:
            if _exists(cursor, HQForm._meta.db_table) or _exists(cursor, HQCase._meta.db_table):
                raise SchemaConflict(
                    f"Schema {schema} already has a table named {HQForm._meta.db_table} or "
                    f"{HQCase._meta.db_table} that labs did not create."
                )
            cursor.execute(
                f"CREATE TABLE {VERSION_TABLE} (version integer NOT NULL, applied_at timestamptz DEFAULT now())"
            )
            current = 0
    for version in range(current + 1, SCHEMA_VERSION + 1):
        _STEPS[version](connection)
        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO {VERSION_TABLE} (version) VALUES (%s)", [version])
    return SCHEMA_VERSION
