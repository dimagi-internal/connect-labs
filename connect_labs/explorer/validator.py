"""The explorer's SQL rules: Scout's validator plus a strict relation allow-list.

Scout runs each tenant's queries under that tenant's own Postgres schema and a
read-only role, so its validator lets any unqualified table through and leaves
isolation to ``search_path`` and the role. Labs has neither: one database, one
role, and a visit cache shared by every opportunity. So here the ONLY relations a
query may name are the ones the engine injects (``RELATIONS``) and the query's
own CTEs. Anything schema- or catalog-qualified is refused outright.

The engine also runs with an empty ``search_path``, so a relation this check
somehow missed fails at execution instead of resolving to a real table.
"""

from __future__ import annotations

from sqlglot import exp

from .sql_validator import SQLValidationError, SQLValidator

#: The relations a query may read. Each is a CTE the engine builds, already
#: filtered to the caller's opportunities.
RELATIONS: frozenset[str] = frozenset({"visits"})

DEFAULT_MAX_ROWS = 500
HARD_MAX_ROWS = 5000


class ExplorerValidator(SQLValidator):
    """Scout's checks, with table access narrowed to ``RELATIONS``."""

    def _validate_table_access(self, statement: exp.Expression, sql: str) -> None:
        for table in statement.find_all(exp.Table):
            # A function in FROM (jsonb_array_elements(...), generate_series(...))
            # is not a relation; the function allow-list has already judged it.
            if not isinstance(table.this, exp.Identifier):
                continue
            if table.db or table.catalog:
                raise SQLValidationError(
                    f"'{table.sql(dialect='postgres')}' is qualified. Query the explorer's relations by "
                    f"bare name: {', '.join(sorted(RELATIONS))}.",
                    sql=sql,
                    error_type="schema_not_allowed",
                )
            if self._is_cte_reference(table):
                continue
            name = table.name if table.this.quoted else table.name.lower()
            if name not in RELATIONS:
                raise SQLValidationError(
                    f"Unknown table '{table.name}'. The explorer can read: {', '.join(sorted(RELATIONS))} "
                    "(plus CTEs you define).",
                    sql=sql,
                    error_type="table_not_allowed",
                )


def validator(max_rows: int = DEFAULT_MAX_ROWS) -> ExplorerValidator:
    return ExplorerValidator(schema="explorer", allowed_schemas=[], max_limit=max(1, min(max_rows, HARD_MAX_ROWS)))
