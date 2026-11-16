"""Create and upgrade the tables labs keeps in a customer's database.

Labs owns exactly the objects named here, in the one schema the customer gave it. A version table
records how far the schema has got, so a later change is a step in ``_STEPS`` applied the next time
the store is used, with no migration for the customer to run and nothing to coordinate.
"""

from __future__ import annotations

import re

from connect_labs.hqdata.models import HQCase, HQForm

VERSION_TABLE = "labs_commcare_schema_version"
SCHEMA_VERSION = 2
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


#: version -> the step that brings a store TO that version.
_STEPS = {1: _step_1, 2: _step_2}


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
