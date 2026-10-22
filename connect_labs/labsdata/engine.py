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
import json
import logging
import time
import uuid
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction

from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

from .scope import Opportunity
from .sql_validator import SQLValidationError
from .validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS, ExplorerValidator

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
    ("reason", "text", "Why the visit got its status (e.g. the rejection reason)"),
    ("location", "text", "GPS 'lat lon alt accuracy' recorded with the visit"),
    ("flag_reason", "jsonb", "Why Connect flagged the visit"),
    ("status_modified_date", "timestamptz", "When the status last changed"),
    ("review_created_on", "timestamptz", "When the program-manager review was made"),
    ("justification", "text", "The worker's justification for a flagged visit"),
    ("completed_work", "jsonb", "The completed work (payment unit) the visit belongs to"),
    ("completed_work_id", "integer", "Id of that completed work"),
    ("deliver_unit_id", "integer", "Id of the deliver unit"),
    ("images", "jsonb", "Images attached to the visit (ids and question names, not the pictures)"),
    ("user_visit_id", "text", "Connect UserVisit UUID"),
    ("form_json", "jsonb", "The full submitted CommCare form. Read fields with form_json #>> '{form,...}'"),
]

#: The cache columns behind ``visits``, in VISIT_COLUMNS order (the first three come
#: from the opportunity list, not the cache).
_CACHE_COLUMNS = [name for name, _t, _m in VISIT_COLUMNS if name not in {"opportunity_id", "opportunity_name", "llo"}]


#: How an agent should react to each kind of failure (Scout's typed outcomes,
#: ``apps/semantic/services/query_outcomes.py``): the category, whether the same
#: call can succeed if retried as-is, and what to do instead. Shipped with every
#: error so an agent corrects itself instead of guessing or looping.
ERROR_GUIDE: dict[str, tuple[str, bool, str]] = {
    "invalid_sql": ("sql", False, "fix_query"),
    "parse_error": ("sql", False, "fix_query"),
    "table_not_allowed": ("sql", False, "use_allowed_relations"),
    "schema_not_allowed": ("sql", False, "use_allowed_relations"),
    "dangerous_function": ("sql", False, "fix_query"),
    "multiple_statements": ("sql", False, "fix_query"),
    "not_select": ("sql", False, "fix_query"),
    "timeout": ("capacity", False, "narrow_or_aggregate"),
    "read_only": ("sql", False, "fix_query"),
    "query_failed": ("internal", True, "retry_once_then_stop"),
    "no_opportunities": ("scope", False, "choose_opportunities"),
    "unknown_visits": ("scope", False, "check_visit_ids"),
}


class QueryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        category, retryable, action = ERROR_GUIDE.get(self.code, ("sql", False, "fix_query"))
        return {
            "error": self.code,
            "message": self.message,
            "category": category,
            "retryable": retryable,
            "recovery_action": action,
        }


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
    columns = ", ".join(f"r.{c}" for c in _CACHE_COLUMNS)
    sql = f"""
explorer_opportunities (opportunity_id, opportunity_name, llo) AS (VALUES {values}),
explorer_generations AS (
    SELECT opportunity_id, MAX(visit_count) AS visit_count
    FROM public.labs_raw_visit_cache
    WHERE pipeline_id = %s AND visit_count > 0 AND opportunity_id = ANY(%s)
    GROUP BY opportunity_id
),
visits AS (
    SELECT r.opportunity_id, o.opportunity_name, o.llo, {columns}
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
        # Postgres's primary message only ("column x does not exist") -- it is about the
        # caller's own SQL. Never str(exc): that carries the LINE/context of the statement
        # labs wrapped around it.
        diag = getattr(cause, "diag", None)
        primary = (getattr(diag, "message_primary", None) or "").strip()
        return QueryError("invalid_sql", f"Postgres rejected the query: {primary or 'invalid SQL'}")
    logger.warning("explorer query failed", exc_info=exc)
    return QueryError("query_failed", "The query could not be run.")


def run_query(opps: list[Opportunity], sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> QueryResult:
    """Validate ``sql`` and run it over ``opps`` (already authorised by ``scope.resolve``).

    Fetches one row past the cap, as Scout does, so a result that fills the cap
    exactly is not reported as cut off and one that overflows always is.
    """
    cap = max(1, min(int(max_rows), HARD_MAX_ROWS))
    check = ExplorerValidator(schema="explorer", allowed_schemas=[], max_limit=cap + 1)
    try:
        statement = check.validate(sql)
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
    columns, rows = _execute(final_sql, params)
    truncated = len(rows) > cap
    return QueryResult(
        columns=columns,
        rows=rows[:cap],
        row_count=min(len(rows), cap),
        truncated=truncated,
        sql_executed=user_sql,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


def _execute(final_sql: str, params: list) -> tuple[list[str], list[list]]:
    """Run one statement locked down: read-only, time-boxed, no search path, rolled back."""
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL transaction_read_only = on")
                cursor.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
                cursor.execute(f"SET LOCAL search_path = {EMPTY_SEARCH_PATH}")
                cursor.execute(final_sql, params)
                description = cursor.description or []
                columns = [d[0] for d in description]
                # Django leaves json/jsonb as text (its JSONField decodes them itself);
                # here they are decoded so form_json arrives as the form, not a string.
                is_json = [getattr(d, "type_code", None) in _JSON_OIDS for d in description]
                rows = [
                    [_decode_json(v) if j else _jsonable(v) for v, j in zip(row, is_json)] for row in cursor.fetchall()
                ]
            # Nothing to keep, and rolling back is what undoes the SET LOCALs when this
            # runs inside an outer transaction.
            transaction.set_rollback(True)
    except DatabaseError as e:
        raise _classify(e) from e
    return columns, rows


#: Postgres type OIDs of json and jsonb.
_JSON_OIDS = frozenset({114, 3802})


def _decode_json(value):
    if isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


#: The most visits one row-level read returns, whole.
MAX_VISITS_PER_READ = 25


def fetch_visits(opps: list[Opportunity], visit_ids) -> list[dict]:
    """Whole visits -- every column of ``visits``, the full ``form_json`` -- by id.

    Through the same ``visits`` relation and lock as a query, so a visit of an
    opportunity outside ``opps`` is simply not found.
    """
    ids = [str(v).strip() for v in (visit_ids or []) if str(v).strip()]
    if not ids:
        raise QueryError("unknown_visits", "Give at least one visit_id.")
    if len(ids) > MAX_VISITS_PER_READ:
        raise QueryError("unknown_visits", f"At most {MAX_VISITS_PER_READ} visits per read.")
    cte_sql, params = visits_cte(opps)
    final_sql = f"WITH {cte_sql}\nSELECT * FROM visits WHERE visit_id = ANY(%s) ORDER BY opportunity_id, visit_id"
    columns, rows = _execute(final_sql, params + [ids])
    return [dict(zip(columns, row)) for row in rows]


#: An opportunity id no real or synthetic opportunity has, so ``visits`` over it is empty.
NO_DATA = Opportunity(id=-1, name="(no data)", llo="(no data)")


def validate_query(sql: str) -> dict:
    """Check ``sql`` completely -- validator, then Postgres -- WITHOUT touching data.

    It runs through the same path as a real query, over a ``visits`` relation that
    holds zero rows, so Postgres still resolves every column, type and function and
    reports what is wrong; the only thing that comes back is the result's column
    names. This is how an agent that may not see real data can still write SQL that
    will run.
    """
    result = run_query([NO_DATA], sql)
    return {"valid": True, "columns": result.columns, "sql_executed": result.sql_executed}
