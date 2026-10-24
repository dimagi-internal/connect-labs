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
5. **Let Postgres contain it** (``labs/migrations/0039_labs_data_reader_role.py``).
   The statement runs as ``labs_data_reader``, a role that may read the caches and
   nothing else, and row-level security shows that role only the
   ``(opportunity_id, pipeline_id)`` pairs this query was granted -- grants written
   inside the same rolled-back transaction, so no other session ever sees them. A
   query that got past every check above still cannot read another table, or
   another opportunity's rows in the caches. If the role is missing, nothing runs.

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
from dataclasses import dataclass, field

from django.db import DatabaseError, connection, transaction
from django.utils import timezone

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
    "not_drillable": ("sql", False, "fix_query"),
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


#: What a result column holds, from its Postgres type: the card formats a cell by it
#: (an integer id is not "10,234", a timestamp is not ISO text) and an agent reads it.
COLUMN_TYPES = ("integer", "numeric", "date", "timestamp", "text", "bool", "json")

_TYPE_OF_OID = {
    **dict.fromkeys((20, 21, 23, 26), "integer"),  # int8, int2, int4, oid
    **dict.fromkeys((700, 701, 790, 1700), "numeric"),  # float4, float8, money, numeric
    1082: "date",
    **dict.fromkeys((1114, 1184), "timestamp"),  # timestamp, timestamptz
    16: "bool",
    **dict.fromkeys((114, 3802), "json"),
}
#: Postgres arrays arrive as lists, which the card shows as JSON.
_ARRAY_OIDS = frozenset({1000, 1005, 1007, 1016, 1009, 1015, 1021, 1022, 1231, 1182, 1115, 1185, 199, 3807, 2951})


def column_type(type_code) -> str:
    if type_code in _ARRAY_OIDS:
        return "json"
    return _TYPE_OF_OID.get(type_code, "text")


#: Where a result's row cap came from: the default, the caller's own `max_rows`, or the
#: hard cap a larger `max_rows` was held to. Only the default and the hard cap are a
#: result being cut off; a caller that asked for 50 rows got the 50 it asked for.
CAP_DEFAULT, CAP_REQUESTED, CAP_HARD = "default", "requested", "hard"


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    sql_executed: str
    elapsed_ms: int
    column_types: list[str] = field(default_factory=list)
    row_cap: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "columns": self.columns,
            "column_types": self.column_types,
            "row_cap": self.row_cap,
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


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


#: The most of a pipeline's fields that become columns.
MAX_PIPELINE_FIELDS = 300
#: How many stored rows are looked at to decide each field's column type.
FIELD_TYPE_SAMPLE = 500


def _current_rows(rel, alias: str, params: list, opp_ids: list[int], now) -> str:
    """Conditions picking ``rel``'s current rows: its pipeline, its configuration, unexpired."""
    params += [rel.pipeline_id, rel.config_hash, opp_ids, now]
    return (
        f"{alias}.pipeline_id = %s AND {alias}.config_hash = %s AND {alias}.opportunity_id = ANY(%s) "
        f"AND {alias}.expires_at > %s"
    )


def pipeline_fields(opps: list[Opportunity], relations) -> dict[str, list[tuple[str, str]]]:
    """Each relation's field columns and their types, from what its rows hold now.

    A pipeline's fields live in one JSON column; each becomes a column of the type
    its stored values have (number, boolean, text, or JSON when mixed with objects),
    so ``WHERE score > 3`` works without a cast. Read as the reader role, over the
    same grants as the query.
    """
    if not relations:
        return {}
    now = timezone.now()
    opp_ids = [o.id for o in opps]
    parts, params = [], []
    for rel in relations:
        params.append(rel.name)
        where = _current_rows(rel, "c", params, opp_ids, now)
        params.append(FIELD_TYPE_SAMPLE)
        parts.append(
            f"SELECT %s AS rel, f.key, jsonb_typeof(f.value) AS kind, COUNT(*) AS n "
            f"FROM (SELECT c.{rel.fields_column} AS j FROM public.{rel.table} c WHERE {where} LIMIT %s) s, "
            f"jsonb_each(s.j) f GROUP BY 1, 2, 3"
        )
    _columns, rows = _execute(" UNION ALL ".join(parts), params, pipeline_grants(opps, relations))
    kinds: dict[str, dict[str, set]] = {}
    for rel_name, key, kind, _n in rows:
        kinds.setdefault(rel_name, {}).setdefault(key, set()).add(kind)
    out = {}
    for rel in relations:
        seen = kinds.get(rel.name, {})
        reserved = {name for name, _t in rel.base_columns}
        order = [f for f in rel.field_names if f in seen] + sorted(k for k in seen if k not in rel.field_names)
        columns = []
        for key in order:
            if key in reserved:
                continue
            present = seen[key] - {"null"}
            if present <= {"number"} and present:
                kind = "numeric"
            elif present <= {"boolean"} and present:
                kind = "boolean"
            elif present & {"object", "array"}:
                kind = "jsonb"
            else:
                kind = "text"
            columns.append((key, kind))
        out[rel.name] = columns[:MAX_PIPELINE_FIELDS]
    return out


def _field_expr(column: str, key: str, kind: str, params: list) -> str:
    params += [key] * (2 if kind in {"numeric", "boolean"} else 1)
    if kind == "numeric":
        return f"CASE WHEN jsonb_typeof(c.{column} -> %s) = 'number' THEN (c.{column} ->> %s)::numeric END"
    if kind == "boolean":
        return f"CASE WHEN jsonb_typeof(c.{column} -> %s) = 'boolean' THEN (c.{column} ->> %s)::boolean END"
    if kind == "jsonb":
        return f"c.{column} -> %s"
    return f"c.{column} ->> %s"


def pipeline_cte(rel, index: int, fields: list[tuple[str, str]], opps: list[Opportunity], now) -> tuple[str, list]:
    """``rel`` as a relation: its current rows for ``opps``, one generation per opportunity
    (as ``visits``), with the opportunity's name and LLO and each field as a column."""
    params: list = []
    gen = f"explorer_pipeline_{index}_generations"
    gen_where = _current_rows(rel, "c", params, [o.id for o in opps], now)
    selects = ["c.opportunity_id", "o.opportunity_name", "o.llo"]
    selects += [f"c.{name}" for name, _t in rel.stage_columns]
    selects += [f"{_field_expr(rel.fields_column, key, kind, params)} AS {_quote(key)}" for key, kind in fields]
    body_where = _current_rows(rel, "c", params, [o.id for o in opps], now)
    sql = f"""
{gen} AS (
    SELECT c.opportunity_id, MAX(c.visit_count) AS visit_count
    FROM public.{rel.table} c WHERE {gen_where}
    GROUP BY c.opportunity_id
),
{rel.name} AS (
    SELECT {", ".join(selects)}
    FROM public.{rel.table} c
    JOIN {gen} g ON g.opportunity_id = c.opportunity_id AND g.visit_count = c.visit_count
    JOIN explorer_opportunities o ON o.opportunity_id = c.opportunity_id
    WHERE {body_where}
)"""
    return sql, params


def pipeline_status(opps: list[Opportunity], relations) -> dict[str, dict[int, int]]:
    """How many current rows each pipeline relation holds per opportunity (0: none stored
    for its current configuration -- the workflow has not been run since it changed)."""
    if not relations:
        return {}
    now = timezone.now()
    parts, params = [], []
    for rel in relations:
        params.append(rel.name)
        where = _current_rows(rel, "c", params, [o.id for o in opps], now)
        # One generation per opportunity, as the relation serves (``pipeline_cte``).
        parts.append(
            f"SELECT rel, opportunity_id, COUNT(*) FROM (SELECT %s AS rel, c.opportunity_id, c.visit_count, "
            f"MAX(c.visit_count) OVER (PARTITION BY c.opportunity_id) AS served FROM public.{rel.table} c "
            f"WHERE {where}) s WHERE visit_count = served GROUP BY 1, 2"
        )
    _columns, rows = _execute(" UNION ALL ".join(parts), params, pipeline_grants(opps, relations))
    out = {rel.name: {o.id: 0 for o in opps} for rel in relations}
    for rel_name, opp_id, count in rows:
        out[rel_name][opp_id] = count
    return out


def pipeline_grants(opps: list[Opportunity], relations) -> list[tuple[int, int]]:
    """The cache rows the pipeline relations may read: each pipeline, on each opportunity."""
    return [(o.id, rel.pipeline_id) for rel in relations for o in opps]


def run_query(
    opps: list[Opportunity], sql: str, max_rows: int | None = None, relations=(), *, field_opps=None
) -> QueryResult:
    """Validate ``sql`` and run it over ``opps`` (already authorised by ``scope.resolve``),
    with ``relations`` -- a workflow's pipelines (``pipelines.workflow_selection``) -- beside
    ``visits``.

    Fetches one row past the cap, as Scout does, so a result that fills the cap
    exactly is not reported as cut off and one that overflows always is. ``max_rows``
    None is the default cap; ``row_cap`` says which cap applied.
    """
    relations = list(relations or [])
    if max_rows is None:
        cap, source = DEFAULT_MAX_ROWS, CAP_DEFAULT
    elif int(max_rows) > HARD_MAX_ROWS:
        cap, source = HARD_MAX_ROWS, CAP_HARD
    else:
        cap, source = max(1, int(max_rows)), CAP_REQUESTED
    check = ExplorerValidator(
        schema="explorer",
        allowed_schemas=[],
        max_limit=cap + 1,
        relations=frozenset({"visits", *(r.name for r in relations)}),
    )
    try:
        statement = check.validate(sql)
        user_sql = check.inject_limit(statement).sql(dialect=check.dialect)
    except SQLValidationError as e:
        raise QueryError(e.error_type, e.message) from e
    except Exception as e:  # noqa: BLE001 -- sqlglot can parse what it cannot render
        raise QueryError("invalid_sql", "Could not parse that as PostgreSQL. Simplify the query and retry.") from e

    started = time.monotonic()
    cte_sql, params = visits_cte(opps)
    if relations:
        # ``field_opps``: whose stored rows decide the pipeline columns, when that is not
        # ``opps`` -- validation runs over no data but must see the real columns.
        fields = pipeline_fields(field_opps or opps, relations)
        now = timezone.now()
        for i, rel in enumerate(relations):
            rel_sql, rel_params = pipeline_cte(rel, i, fields.get(rel.name, []), opps, now)
            cte_sql += "," + rel_sql
            params += rel_params
    # The user's SQL carries no parameters; double its % so the driver does not read
    # LIKE '%term%' as a placeholder.
    final_sql = f"WITH {cte_sql}\nSELECT * FROM (\n{user_sql.replace('%', '%%')}\n) AS explorer_result"

    columns, types, rows = _execute_typed(final_sql, params, visit_grants(opps) + pipeline_grants(opps, relations))
    truncated = len(rows) > cap
    return QueryResult(
        columns=columns,
        column_types=types,
        row_cap={"limit": cap, "source": source},
        rows=rows[:cap],
        row_count=min(len(rows), cap),
        truncated=truncated,
        sql_executed=user_sql,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


#: The role every Data statement runs as (created by labs migration 0039).
READER_ROLE = "labs_data_reader"


def visit_grants(opps: list[Opportunity]) -> list[tuple[int, int]]:
    """The cache rows ``visits`` over ``opps`` may read: each opportunity's raw visit slot."""
    return [(o.id, USER_VISITS_RAW_SLOT) for o in opps]


def _lock(cursor, grants: list[tuple[int, int]]) -> None:
    """Grant exactly these cache rows, then become the reader role: from here on the
    database, not only the validator, decides what the statement can read."""
    if grants:
        values = ", ".join(["(%s, %s)"] * len(grants))
        cursor.execute(
            f"INSERT INTO public.labsdata_scope_grant (opportunity_id, pipeline_id) VALUES {values}",
            [v for pair in grants for v in pair],
        )
    cursor.execute(f"SET LOCAL ROLE {READER_ROLE}")
    cursor.execute("SET LOCAL transaction_read_only = on")
    cursor.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
    cursor.execute(f"SET LOCAL search_path = {EMPTY_SEARCH_PATH}")


def _execute(final_sql: str, params: list, grants: list[tuple[int, int]]) -> tuple[list[str], list[list]]:
    """Run one statement locked down: as the reader role, over only ``grants``,
    read-only, time-boxed, no search path, rolled back."""
    columns, _types, rows = _execute_typed(final_sql, params, grants)
    return columns, rows


def _execute_typed(
    final_sql: str, params: list, grants: list[tuple[int, int]]
) -> tuple[list[str], list[str], list[list]]:
    """``_execute``, and each column's type (``COLUMN_TYPES``) from the cursor."""
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                try:
                    _lock(cursor, grants)
                except DatabaseError as e:
                    # Fail closed: without the role and its grants there is no boundary.
                    logger.error("labsdata: could not lock the session; refusing to run", exc_info=e)
                    raise QueryError("query_failed", "The Data page is not available right now.") from e
                cursor.execute(final_sql, params)
                description = cursor.description or []
                columns = [d[0] for d in description]
                types = [column_type(getattr(d, "type_code", None)) for d in description]
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
    return columns, types, rows


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
    columns, rows = _execute(final_sql, params + [ids], visit_grants(opps))
    return [dict(zip(columns, row)) for row in rows]


#: An opportunity id no real or synthetic opportunity has, so ``visits`` over it is empty.
NO_DATA = Opportunity(id=-1, name="(no data)", llo="(no data)")


def validate_query(sql: str, relations=(), field_opps=None) -> dict:
    """Check ``sql`` completely -- validator, then Postgres -- WITHOUT touching data.

    It runs through the same path as a real query, over a ``visits`` relation that
    holds zero rows, so Postgres still resolves every column, type and function and
    reports what is wrong; the only thing that comes back is the result's column
    names. This is how an agent that may not see sensitive data can still write SQL that
    will run. With a workflow's ``relations``, their columns come from ``field_opps``'
    stored rows (names and types only); the rows queried are still none.
    """
    result = run_query([NO_DATA], sql, relations=relations, field_opps=field_opps)
    return {"valid": True, "columns": result.columns, "sql_executed": result.sql_executed}
