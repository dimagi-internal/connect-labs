"""Run a caller's SQL over the opportunities they hold. The safety model, in order:

1. **Scope** (``scope.resolve``): every requested opportunity is one the caller
   holds, or the call is refused. This runs before anything touches the cache.
2. **Validate** (``validator.ExplorerValidator``, Scout's sqlglot rules): one
   SELECT; no DDL/DML; analytics functions only; no system catalogs; the only
   relation it may name is ``visits`` (plus its own CTEs); LIMIT injected/capped.
3. **Scope the data, not the query.** ``visits`` is a CTE labs builds over
   ``labs_raw_visit_cache`` with the opportunity filter already applied, so even a
   query that validates cannot reach a row outside the caller's set: it never has
   a name for one.
4. **Lock the session** for the one statement, inside a savepoint that is always
   rolled back: read-only, a statement timeout, and an empty ``search_path`` --
   so a relation the validator somehow let through fails to resolve instead of
   reading a real table. The CTE names its table schema-qualified, which is the
   only way anything resolves.

What the user writes is placed in a subquery after labs' WITH, so their own WITH,
UNION, ORDER BY and LIMIT keep working unchanged.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import time
import uuid
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction

from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

from .scope import Opportunity
from .sql_validator import SQLValidationError
from .validator import DEFAULT_MAX_ROWS, validator

logger = logging.getLogger(__name__)

STATEMENT_TIMEOUT_MS = 20_000
# Never created: an empty search path that is still a legal setting.
EMPTY_SEARCH_PATH = "labs_explorer_no_tables"

#: The columns of ``visits``, with what each means. This is also what the agent reads.
VISIT_COLUMNS: list[tuple[str, str, str]] = [
    ("opportunity_id", "integer", "Connect opportunity id"),
    ("opportunity_name", "text", "Opportunity name"),
    ("llo", "text", "The organization (LLO) that runs the opportunity"),
    ("visit_id", "text", "Connect visit id"),
    ("visit_date", "date", "Date of the visit"),
    ("username", "text", "The frontline worker's (FLW) username"),
    ("status", "text", "Visit review status: approved, rejected, pending, over_limit, ..."),
    ("deliver_unit", "text", "The deliver unit (the Connect form) the visit counts toward"),
    ("entity_id", "text", "The beneficiary / case the visit is about"),
    ("entity_name", "text", "Display name of that entity"),
    ("flagged", "boolean", "Connect flagged the visit"),
    ("review_status", "text", "Program-manager review status"),
    ("date_created", "timestamptz", "When the visit was submitted"),
    ("form_json", "jsonb", "The full submitted CommCare form. Read fields with form_json #>> '{form,...}'"),
]


class QueryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    sql_executed: str
    elapsed_ms: int

    def as_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "sql_executed": self.sql_executed,
            "elapsed_ms": self.elapsed_ms,
        }


def visits_cte(opps: list[Opportunity]) -> tuple[str, list]:
    """The ``visits`` relation over ``opps`` only, and its parameters.

    One generation per opportunity: concurrent cache rebuilds can each leave a
    finalized ``visit_count`` label behind, and serving both would count every
    visit twice (see ``SQLCacheManager._one_generation``). Unfinalized writers
    carry negative sentinels, hence ``> 0``.
    """
    if not opps:
        raise QueryError("no_opportunities", "pick at least one opportunity")
    values = ", ".join(["(%s::integer, %s::text, %s::text)"] * len(opps))
    params: list = []
    for o in opps:
        params += [o.id, o.name, o.llo]
    ids = [o.id for o in opps]
    sql = f"""
explorer_opportunities (opportunity_id, opportunity_name, llo) AS (VALUES {values}),
explorer_generations AS (
    SELECT opportunity_id, MAX(visit_count) AS visit_count
    FROM public.labs_raw_visit_cache
    WHERE pipeline_id = %s AND visit_count > 0 AND opportunity_id = ANY(%s)
    GROUP BY opportunity_id
),
visits AS (
    SELECT r.opportunity_id, o.opportunity_name, o.llo, r.visit_id, r.visit_date, r.username, r.status,
           r.deliver_unit, r.entity_id, r.entity_name, r.flagged, r.review_status, r.date_created, r.form_json
    FROM public.labs_raw_visit_cache r
    JOIN explorer_generations g ON g.opportunity_id = r.opportunity_id AND g.visit_count = r.visit_count
    JOIN explorer_opportunities o ON o.opportunity_id = r.opportunity_id
    WHERE r.pipeline_id = %s
)"""
    params += [USER_VISITS_RAW_SLOT, ids, USER_VISITS_RAW_SLOT]
    return sql, params


def _jsonable(value):
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID | memoryview | bytes):
        return str(value)
    return value


def _classify(exc: DatabaseError) -> QueryError:
    cause = exc.__cause__ or exc
    code = getattr(cause, "pgcode", None) or ""
    if code == "57014":
        return QueryError(
            "timeout",
            f"The query ran past {STATEMENT_TIMEOUT_MS // 1000}s. Filter by date or opportunity, or aggregate.",
        )
    if code == "25006":
        return QueryError("read_only", "The explorer is read-only.")
    if code[:2] in {"42", "22", "21"} or code == "0A000":
        detail = str(cause).strip().splitlines()[0] if str(cause).strip() else type(cause).__name__
        return QueryError("invalid_sql", f"Postgres rejected the query: {detail}")
    logger.warning("explorer query failed", exc_info=exc)
    return QueryError("query_failed", "The query could not be run.")


def run_query(opps: list[Opportunity], sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
    """Validate ``sql`` and run it over ``opps`` (already authorised by ``scope.resolve``)."""
    check = validator(max_rows)
    try:
        statement = check.validate(sql)
        requested_limit = check.limit_value(statement)
        user_sql = check.inject_limit(statement).sql(dialect=check.dialect)
    except SQLValidationError as e:
        raise QueryError(e.error_type, e.message) from e
    except Exception as e:  # noqa: BLE001 -- sqlglot can parse what it cannot render
        raise QueryError("invalid_sql", "Could not parse that as PostgreSQL. Simplify the query and retry.") from e

    cte_sql, params = visits_cte(opps)
    # The user's SQL carries no parameters; double its % so the driver does not read
    # LIKE '%term%' as a placeholder.
    final_sql = f"WITH {cte_sql}\nSELECT * FROM (\n{user_sql.replace('%', '%%')}\n) AS explorer_result"

    started = time.monotonic()
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL transaction_read_only = on")
                cursor.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
                cursor.execute(f"SET LOCAL search_path = {EMPTY_SEARCH_PATH}")
                cursor.execute(final_sql, params)
                columns = [d[0] for d in cursor.description or []]
                rows = [[_jsonable(v) for v in row] for row in cursor.fetchall()]
            # Nothing to keep, and rolling back is what undoes the SET LOCALs when this
            # runs inside an outer transaction.
            transaction.set_rollback(True)
    except DatabaseError as e:
        raise _classify(e) from e

    truncated = len(rows) >= check.max_limit or (requested_limit is not None and requested_limit > check.max_limit)
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        sql_executed=user_sql,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
