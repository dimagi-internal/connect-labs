# connect_labs/mcp/tools/labsdata.py
"""Labs data tools: read-only SQL, whole visits and workflow recipes over the opportunities you hold.

Call ``data_describe`` first: it returns what can be queried, a field index of the
forms (with the SQL that reads each field), the semantic registries that cover
these opportunities, and the rules for answering from this data. Then
``data_query`` runs read-only SQL over the ``visits`` relation, ``data_visits``
reads whole visits (every column, the full form JSON), and
``data_workflow_context`` explains how a workflow turns those visits into what it
shows. Service and safety model: ``connect_labs/labsdata/``.

The ``explorer_describe`` / ``explorer_validate`` / ``explorer_query`` names are
kept, calling the same handlers, because ``dimagi-internal/scout-queries`` calls
them; new callers use ``data_*``.

Visit readers: a restricted ("no user visit data") caller reaches them only for
opportunities whose data is generated (``token_scopes``, ``visit_access``), and
canopy acting as a visitor gets production rows only for an agent labs has cleared
(``labsdata/service.py:agent_sees_sensitive_rows``).
"""

from __future__ import annotations

from connect_labs.labs.access.scopes import Caller
from connect_labs.labsdata import service
from connect_labs.labsdata.engine import MAX_VISITS_PER_READ, QueryError
from connect_labs.labsdata.scope import ScopeError
from connect_labs.labsdata.validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS
from connect_labs.labsdata.workflow_context import WorkflowNotFound, workflow_context

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register
from ..ui import DATA_RESULT_URI, tool_meta

_OPPS = {
    "type": "array",
    "items": {"type": "integer"},
    "minItems": 1,
    "description": "Opportunities to read (labs_context lists the ones you hold). Up to 25.",
}


def _caller(user) -> Caller:
    return Caller(user=user, access_token=require_connect_token(user))


def _from_canopy() -> bool:
    """True when canopy is calling as a visitor (the delegated grant).

    That caller is an agent: it gets production rows only when labs has cleared it for
    sensitive data, and otherwise structure, validation and synthetic data, handing SQL
    to the page. A person's own MCP sign-in (Claude Desktop/Code) is not delegated.
    """
    from .workflow_run import _delegated_token

    return _delegated_token() is not None


def _scope_error(e: ScopeError) -> MCPToolError:
    code = "UPSTREAM_ERROR" if e.code == "scopes_unavailable" else "PERMISSION_DENIED"
    if e.code in {"bad_opportunity_ids", "no_opportunities", "too_many_opportunities", "bad_scope"}:
        code = "INVALID_SCHEMA"
    return MCPToolError(code, e.message, {"reason": e.code})


def _query_error(e: QueryError) -> MCPToolError:
    return MCPToolError("INVALID_SCHEMA", e.message, e.as_dict())


DESCRIBE = dict(
    description=(
        "START HERE to answer questions about live program data. For the given opportunities, returns: "
        "what is loaded (cached visits, freshness), the `visits` relation and its columns, an index of the "
        "submitted form fields (path, fill rate, type, the SQL that reads it, and the values of categorical "
        "fields), the semantic registries that cover these opportunities, and the rules for answering. "
        "Use `field_search` to find a field ('birth', 'delivery', 'weight'). Set `load_missing` to fetch "
        "opportunities that have nothing cached (can take a minute). An agent calling through canopy that "
        "Labs has not cleared for sensitive data gets structure only on PRODUCTION opportunities (no values, fill "
        "rates or counts)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": _OPPS,
            "field_search": {
                "type": "string",
                "description": "Only fields whose path or a value contains this text.",
            },
            "load_missing": {"type": "boolean", "default": False},
        },
        "required": ["opportunity_ids"],
        "additionalProperties": False,
    },
)


def describe(user, opportunity_ids, field_search=None, load_missing=False):
    try:
        return service.describe(
            _caller(user),
            opportunity_ids,
            field_search=field_search,
            load_missing=bool(load_missing),
            for_agent=_from_canopy(),
        )
    except ScopeError as e:
        raise _scope_error(e) from e


VALIDATE = dict(
    description=(
        "Check one SQL SELECT end to end -- the safety rules, then Postgres resolving every column, type and "
        "function -- by running it over a `visits` relation with ZERO rows. Touches no data. Returns "
        "{valid: true, columns} or {valid: false, error, message, category, retryable, recovery_action}."
    ),
    input_schema={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "One SELECT over `visits`."}},
        "required": ["sql"],
        "additionalProperties": False,
    },
)


def validate(user, sql):
    try:
        return service.validate(sql)
    except QueryError as e:
        return {"valid": False, **e.as_dict()}


QUERY = dict(
    description=(
        "Run one read-only PostgreSQL SELECT over `visits` (one row per visit, filtered to the given "
        "opportunities; every column of the visit incl. form_json, the full submitted form; columns from "
        "data_describe; form fields via form_json #>> '{form,...}'). WITH / JOIN / UNION / window and jsonb "
        "functions work; no other tables. Results are capped (default "
        f"{DEFAULT_MAX_ROWS}, max {HARD_MAX_ROWS} rows) and time out after 20s, so aggregate. Returns columns, "
        "rows, row_count, truncated, the SQL actually executed, `notes` to read before answering and "
        "`open_on_page` (the query on the Data page). Errors carry category / retryable / recovery_action. Every "
        "query is audit-logged. An agent calling through canopy that Labs has not cleared for sensitive data gets "
        "production rows withheld: `withheld: true`, the SQL checked, and `open_on_page` for the person to run it."
    ),
    meta=tool_meta(DATA_RESULT_URI, "model", "app"),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": _OPPS,
            "sql": {"type": "string", "description": "One SELECT. The only relation is `visits`."},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": HARD_MAX_ROWS, "default": DEFAULT_MAX_ROWS},
        },
        "required": ["opportunity_ids", "sql"],
        "additionalProperties": False,
    },
)


def query(user, opportunity_ids, sql, max_rows=DEFAULT_MAX_ROWS):
    try:
        return service.query(_caller(user), opportunity_ids, sql, max_rows=int(max_rows), for_agent=_from_canopy())
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.SensitiveDataRefused as e:
        # Not an error: the SQL is valid (checked over zero rows) and the person can
        # run it. The agent -- and the View canopy draws for it -- get no rows.
        return {
            "withheld": True,
            "reason": e.code,
            "message": e.message,
            "sql_executed": sql,
            "open_on_page": service.open_on_page_url(opportunity_ids, sql),
        }
    except QueryError as e:
        raise _query_error(e) from e


for _name, _spec, _fn in (
    ("data_describe", DESCRIBE, describe),
    ("data_validate", VALIDATE, validate),
    ("data_query", QUERY, query),
    # scout-queries' names for the same three (see the module docstring), without the
    # result View: they are called from Claude Code, not drawn in canopy.
    ("explorer_describe", DESCRIBE, describe),
    ("explorer_validate", VALIDATE, validate),
    ("explorer_query", {k: v for k, v in QUERY.items() if k != "meta"}, query),
):
    register(name=_name, **_spec)(_fn)


@register(
    name="data_visits",
    description=(
        "Read whole visits by id: every column of `visits` and the full submitted form (form_json), from the "
        f"given opportunities' cached visits. Up to {MAX_VISITS_PER_READ} visits per call. Use it to check a "
        "number at row level -- the visits behind it -- or to see exactly what a worker submitted. Returns "
        "{visits, not_found, notes}. Audit-logged. An agent calling through canopy that Labs has not cleared "
        "for sensitive data reads only SYNTHETIC opportunities."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": _OPPS,
            "visit_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_VISITS_PER_READ,
                "description": "Connect visit ids (the `visit_id` column).",
            },
        },
        "required": ["opportunity_ids", "visit_ids"],
        "additionalProperties": False,
    },
)
def data_visits(user, opportunity_ids, visit_ids):
    try:
        return service.visits(_caller(user), opportunity_ids, visit_ids, for_agent=_from_canopy())
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.SensitiveDataRefused as e:
        raise MCPToolError("PERMISSION_DENIED", e.message, {"reason": e.code}) from e
    except QueryError as e:
        raise _query_error(e) from e


@register(
    name="data_workflow_context",
    description=(
        "How a workflow turns visits into what it shows: its opportunities, its semantic-registry binding, and "
        "for each pipeline the fields it reads (candidate form paths, aggregation, transform, filter), its "
        "grouping key and row filters, plus `visits_sql` -- a ready, validated query over `visits` that "
        "extracts the pipeline's fields per visit with the pipeline's own expressions. Use it to check that a "
        "workflow is right: recompute its numbers from `visits` and read the visits that disagree with "
        "data_visits. Definitions only, no visit rows. Give the workflow's opportunity_id OR program_id."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer", "description": "The workflow (definition) id."},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
        },
        "required": ["definition_id"],
        "additionalProperties": False,
    },
)
def data_workflow_context(user, definition_id, opportunity_id=None, program_id=None):
    try:
        return workflow_context(
            _caller(user), int(definition_id), opportunity_id=opportunity_id, program_id=program_id
        )
    except ScopeError as e:
        raise _scope_error(e) from e
    except WorkflowNotFound as e:
        raise MCPToolError("NOT_FOUND", str(e), {"reason": e.code}) from e
