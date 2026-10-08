# connect_labs/mcp/tools/explorer.py
"""SQL explorer tools: ad hoc and agent-written SQL over the opportunities you hold.

Call ``explorer_describe`` first: it returns what can be queried, a field index
of the forms (with the SQL that reads each field), the semantic registries that
cover these opportunities, and the rules for answering from this data. Then
``explorer_query`` runs read-only SQL over the ``visits`` relation.

Both are visit-data readers: a restricted ("no user visit data") caller reaches
them only for opportunities whose data is generated (``token_scopes``,
``visit_access``). Safety model: ``connect_labs/explorer/engine.py``.
"""

from __future__ import annotations

from connect_labs.explorer import service
from connect_labs.explorer.engine import QueryError
from connect_labs.explorer.scope import ScopeError
from connect_labs.explorer.validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS
from connect_labs.labs.access.scopes import Caller

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

_OPPS = {
    "type": "array",
    "items": {"type": "integer"},
    "minItems": 1,
    "description": "Opportunities to explore (labs_context lists the ones you hold). Up to 25.",
}


def _caller(user) -> Caller:
    return Caller(user=user, access_token=require_connect_token(user))


def _from_canopy() -> bool:
    """True when canopy is calling as a visitor (the delegated grant).

    That caller is an agent, and real rows must not travel back through canopy:
    it gets structure, validation and synthetic data, and hands SQL to the page.
    A person's own MCP sign-in (Claude Desktop/Code) is not delegated.
    """
    from .workflow_run import _delegated_token

    return _delegated_token() is not None


def _scope_error(e: ScopeError) -> MCPToolError:
    code = "UPSTREAM_ERROR" if e.code == "scopes_unavailable" else "PERMISSION_DENIED"
    if e.code in {"bad_opportunity_ids", "no_opportunities", "too_many_opportunities"}:
        code = "INVALID_SCHEMA"
    return MCPToolError(code, e.message, {"reason": e.code})


@register(
    name="explorer_describe",
    description=(
        "START HERE to answer questions about live program data. For the given opportunities, returns: "
        "what is loaded (cached visits, freshness), the `visits` relation and its columns, an index of the "
        "submitted form fields (path, fill rate, type, the SQL that reads it, and the values of categorical "
        "fields), the semantic registries that cover these opportunities, and the rules for answering. "
        "Use `field_search` to find a field ('birth', 'delivery', 'weight'). Set `load_missing` to fetch "
        "opportunities that have nothing cached (can take a minute). Called by an agent through canopy "
        "on REAL opportunities it returns structure only (no values, fill rates or counts)."
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
def explorer_describe(user, opportunity_ids, field_search=None, load_missing=False):
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


@register(
    name="explorer_validate",
    description=(
        "Check one SQL SELECT for the explorer end to end -- the safety rules, then Postgres resolving every "
        "column, type and function -- by running it over a `visits` relation with ZERO rows. Touches no data. "
        "Returns {valid: true, columns} or an error saying what is wrong. Use it before handing SQL to the page."
    ),
    input_schema={
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "One SELECT over `visits`."}},
        "required": ["sql"],
        "additionalProperties": False,
    },
)
def explorer_validate(user, sql):
    try:
        return service.validate(sql)
    except QueryError as e:
        return {"valid": False, "error": e.code, "message": e.message}


@register(
    name="explorer_query",
    description=(
        "Run one read-only PostgreSQL SELECT over `visits` (one row per visit, filtered to the given "
        "opportunities; columns from explorer_describe; form fields via form_json #>> '{form,...}'). "
        "WITH / JOIN / UNION / window and jsonb functions work; no other tables. Results are capped "
        f"(default {DEFAULT_MAX_ROWS}, max {HARD_MAX_ROWS} rows) and time out after 20s, so aggregate. Returns "
        "columns, rows, row_count, truncated and the SQL actually executed. Every query is audit-logged. "
        "Called by an agent through canopy it runs only on SYNTHETIC opportunities: real rows never go back "
        "through canopy -- validate with explorer_validate and hand the SQL to the page instead."
    ),
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
def explorer_query(user, opportunity_ids, sql, max_rows=DEFAULT_MAX_ROWS):
    try:
        return service.query(_caller(user), opportunity_ids, sql, max_rows=int(max_rows), for_agent=_from_canopy())
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.RealDataRefused as e:
        raise MCPToolError("PERMISSION_DENIED", e.message, {"reason": e.code}) from e
    except QueryError as e:
        raise MCPToolError("INVALID_SCHEMA", e.message, {"reason": e.code}) from e
