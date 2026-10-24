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

from .. import kept_results
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


#: data_query only: what it keeps (explorer_query, scout-queries' name, returns rows inline).
QUERY_KEPT = (
    " Through canopy the result is kept for a day at `result_uri` (a labs-result:// resource, also a "
    f"resource_link in the content): the agent gets the first {kept_results.INLINE_ROWS} rows as `sample_rows` "
    "(`sample_is_partial` when there are more) and reads the rest with site_read on that URI, a page at a time "
    "(?page=N&page_size=M, up to 1000) -- never by running the query again; the person's card shows every row. "
    "Other callers get every row in `rows`, and nothing is kept unless they pass keep_result."
)


#: data_query's arguments: explorer_query's, and whether to keep the result for a client
#: that is not canopy.
QUERY_KEPT_SCHEMA = {
    **QUERY["input_schema"],
    "properties": {
        **QUERY["input_schema"]["properties"],
        "keep_result": {
            "type": "boolean",
            "default": False,
            "description": (
                "Also keep the result for a day at a labs-result:// link you will read back with "
                "resources/read. Only when you will; you get every row inline either way. (Through "
                "canopy the result is always kept.)"
            ),
        },
    },
}


def query(user, opportunity_ids, sql, max_rows=DEFAULT_MAX_ROWS, keep_result=False):
    agent = _from_canopy()
    try:
        out = service.query(_caller(user), opportunity_ids, sql, max_rows=int(max_rows), for_agent=agent, shaped=False)
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.SensitiveDataRefused as e:
        # Not an error: the SQL is valid (checked over zero rows) and the person can
        # run it. The agent -- and the View canopy draws for it -- get no rows. The View
        # reads even this from the kept result, so a sensitive conversation's card, which
        # gets only the link, still offers the Data page.
        withheld = {
            "withheld": True,
            "reason": e.code,
            "message": e.message,
            "sql_executed": sql,
            "open_on_page": service.open_on_page_url(opportunity_ids, sql),
        }
        if not (agent or keep_result):
            # Nobody will read it back (and only canopy's agent is ever refused rows).
            return withheld
        link = kept_results.save(
            user,
            kept_results.KIND_QUERY,
            withheld,
            access={"opportunity_ids": _ids(opportunity_ids), "sensitive": False},
            name="Query (rows stay in Labs)",
            description="Production rows withheld from the agent; open it on the Data page.",
        )
        return kept_results.with_link(withheld, link)
    except QueryError as e:
        raise _query_error(e) from e
    if not (agent or keep_result):
        # Nobody will read it back: the rows go inline, and labs keeps no copy of them.
        return out
    return _kept(user, out, agent)


def explorer_query(user, opportunity_ids, sql, max_rows=DEFAULT_MAX_ROWS):
    """scout-queries' ``explorer_query``: the result whole and inline, nothing kept.
    Its caller is Claude Code reading ``content[0].text``; it has no View and no
    ``site_read``, so it keeps exactly the shape it was built against."""
    try:
        return service.query(_caller(user), opportunity_ids, sql, max_rows=int(max_rows), for_agent=_from_canopy())
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.SensitiveDataRefused as e:
        return {
            "withheld": True,
            "reason": e.code,
            "message": e.message,
            "sql_executed": sql,
            "open_on_page": service.open_on_page_url(opportunity_ids, sql),
        }
    except QueryError as e:
        raise _query_error(e) from e


def _ids(opportunity_ids) -> list[int]:
    try:
        return sorted({int(i) for i in opportunity_ids or []})
    except (TypeError, ValueError):
        return []


def _kept(user, out: dict, agent: bool) -> dict:
    """The query's result kept as a ``labs-result://`` result, and what the caller gets.

    Kept only for a caller that reads it back (``query``): canopy, whose card pages and
    sorts through it and whose agent reads more with ``site_read``, or a client that passed
    ``keep_result``. Every other caller (Claude Code, ACE, scout-queries) gets every row
    inline and labs keeps no copy of production rows for it. The kept result holds every
    row the query returned (up to its row cap), unshaped. A canopy agent gets the facts --
    the row count, the columns, the first ``kept_results.INLINE_ROWS`` rows (long cells
    cut, as before), the SQL, the notes -- and the link; a client that asked gets the whole
    result with the link beside it."""
    ids = [o["id"] for o in out.get("opportunities") or []]
    names = ", ".join(str(o.get("name") or o["id"]) for o in out.get("opportunities") or [])
    link = kept_results.save(
        user,
        kept_results.KIND_QUERY,
        out,
        access={"opportunity_ids": ids, "sensitive": kept_results.sensitive(ids)},
        name=f"Query over {names}"[:200] if names else "Query result",
        description=f"{out['row_count']} rows, {len(out['columns'])} columns",
    )
    if not agent:
        return kept_results.with_link(out, link)
    rows = out["rows"]
    # Long cells cut and the row-cap note added exactly as an agent's result always was.
    shaped = service.shape_for_agent({**out, "rows": rows[: kept_results.INLINE_ROWS]})
    summary = kept_results.summarize_rows(out["columns"], rows, sample=kept_results.INLINE_ROWS)
    summary["sample_rows"] = [dict(zip(out["columns"], r)) for r in shaped["rows"]]
    facts = {
        **{k: v for k, v in out.items() if k != "rows"},
        "row_count": summary["row_count"],
        "sample_rows": summary["sample_rows"],
        # summarize_rows' `truncated` (the sample is not every row). `truncated` stays the
        # query's own: the row cap was hit.
        "sample_is_partial": summary["truncated"],
        "notes": shaped["notes"],
    }
    if summary["truncated"]:
        facts["notes"] = [
            *facts["notes"],
            f"These are the first {len(summary['sample_rows'])} of {summary['row_count']} rows. The person's card "
            "shows every row. Read more with site_read on `result_uri`, a page at a time (?page=2, "
            "?page_size= up to 1000) -- do not run the query again to see them.",
        ]
    return kept_results.with_link(facts, link)


for _name, _spec, _fn in (
    ("data_describe", DESCRIBE, describe),
    ("data_validate", VALIDATE, validate),
    (
        "data_query",
        {**QUERY, "description": QUERY["description"] + QUERY_KEPT, "input_schema": QUERY_KEPT_SCHEMA},
        query,
    ),
    # scout-queries' names for the same three (see the module docstring), without the
    # result View: they are called from Claude Code, not drawn in canopy.
    ("explorer_describe", DESCRIBE, describe),
    ("explorer_validate", VALIDATE, validate),
    ("explorer_query", {k: v for k, v in QUERY.items() if k != "meta"}, explorer_query),
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
