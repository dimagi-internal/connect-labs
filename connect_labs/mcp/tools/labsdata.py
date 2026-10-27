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
from connect_labs.labsdata import display as result_display
from connect_labs.labsdata import guidance, page_holds, service, spaces
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
    "description": "Opportunities to read (labs_context lists the ones you hold). Up to 100.",
}

#: The selection data_describe and data_query take: opportunities and/or programs, or a workflow.
_SELECTION = {
    "opportunity_ids": {**_OPPS, "minItems": 0},
    "program_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Programs to read: every opportunity of each that the person holds, resolved now.",
    },
    "workflow": {
        "type": "object",
        "description": (
            "Instead of opportunities and programs: a workflow, with the scope it lives in -- "
            "{id, opportunity_id} or {id, program_id}. Reads its opportunities, and adds each of its "
            "pipelines (computed from Connect visits) as a relation p_<alias>: the stored rows the workflow "
            "draws, one per visit, worker or case. data_describe lists their columns."
        ),
        "properties": {
            "id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
        },
        "required": ["id"],
        "additionalProperties": False,
    },
}


#: Every data_* tool's ``space_id``: a data space on the Data page (its page state's
#: ``filters.space_id``). The space brings its own selection.
_SPACE = {
    "space_id": {
        "type": "integer",
        "description": (
            "A data space (the Data page's page state says `filters.space_id` when one is open). When given, "
            "the call reads exactly the space's sources -- opened as the person, refused as not found unless "
            "they hold every one -- and any opportunity_ids / program_ids / workflow you also pass must name "
            "the same sources, or the call is refused. On a space's page, always pass it."
        ),
    }
}


def _caller(user) -> Caller:
    return Caller(user=user, access_token=require_connect_token(user))


def _space(caller: Caller, space_id=None, opportunity_ids=None, program_ids=None, workflow=None):
    """The data space a call reads, through ``spaces.from_request`` (the one door): the saved
    space ``space_id`` names -- anything else the call names must be the space's own sources,
    so an agent held to a space cannot widen or swap what it reads -- or one generated from
    the selection. Its errors as tool errors."""
    try:
        opened = spaces.from_request(
            caller,
            space_id=space_id,
            opportunity_ids=opportunity_ids,
            program_ids=program_ids,
            workflow=workflow,
        )
        # Canopy on a page: only what that page shows, as labs recorded it (never the arguments).
        page_holds.hold_to_page(opened, caller=caller)
        return opened
    except page_holds.OutsidePage as e:
        raise _outside_page(e) from e
    except spaces.SpaceNotFound as e:
        raise MCPToolError("NOT_FOUND", e.message, {"reason": e.code}) from e
    except spaces.SpaceChanged as e:
        raise MCPToolError("INVALID_SCHEMA", e.message, {"reason": e.code}) from e
    except spaces.OutsideSpace as e:
        raise MCPToolError("PERMISSION_DENIED", e.message, {"reason": e.code, "space_id": e.space_id}) from e
    except ScopeError as e:
        raise _scope_error(e) from e


def _outside_page(e) -> MCPToolError:
    return MCPToolError("PERMISSION_DENIED", e.message, e.as_dict())


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
        "opportunities that have nothing cached (can take a minute) and, with a `workflow`, to rebuild its "
        "pipelines' stored output where it has expired (a pipeline relation with no rows says why in "
        "`why_no_rows`; a few opportunities per call -- call again while `pipelines_rebuilt.pending`). "
        "An agent calling through canopy that Labs has not cleared for sensitive data gets structure only on "
        "PRODUCTION opportunities (no values, fill rates or counts)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SELECTION,
            **_SPACE,
            "field_search": {
                "type": "string",
                "description": "Only fields whose path or a value contains this text.",
            },
            "load_missing": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
)


#: scout-queries' explorer_describe: the shape it was built against, opportunities only.
DESCRIBE_OPPORTUNITIES_ONLY = {
    **DESCRIBE,
    "input_schema": {
        **DESCRIBE["input_schema"],
        "properties": {
            "opportunity_ids": _OPPS,
            **{
                k: v
                for k, v in DESCRIBE["input_schema"]["properties"].items()
                if k not in _SELECTION and k not in _SPACE
            },
        },
        "required": ["opportunity_ids"],
    },
}


def describe(
    user, opportunity_ids=None, field_search=None, load_missing=False, program_ids=None, workflow=None, space_id=None
):
    caller = _caller(user)
    opened = _space(caller, space_id, opportunity_ids, program_ids, workflow)
    try:
        out = service.describe(
            caller,
            opened,
            field_search=field_search,
            load_missing=bool(load_missing),
            for_agent=_from_canopy(),
        )
    except ScopeError as e:
        raise _scope_error(e) from e
    if opened.saved:
        out["space"] = {
            "id": opened.id,
            "name": opened.space.name,
            "description": opened.space.description,
            "program_ids": list(opened.space.program_ids),
            # What is kept in it, by name and id (page_openItem opens one; data_artifact
            # reads an artifact's code). Never a row.
            "items": _space_items(opened),
            "rules": guidance.SPACE_RULES,
        }
    if _from_canopy():
        # Only canopy's panel has the Data page's page actions (page_showQuery, ...).
        out["page_actions"] = guidance.PAGE_ACTIONS
    page = page_holds.current_page()
    if page is not None:
        # The page this agent is on, and that it reads nothing beyond it.
        out["page"] = {**page, "rules": guidance.PAGE_RULES}
    return out


def _space_items(opened) -> dict:
    """The latest version of each saved query and artifact in an open space: ids, names,
    versions and an artifact's query names. Definitions, never rows."""

    def latest(rows):
        seen = {}
        for r in rows.order_by("name", "-version"):
            seen.setdefault(r.name, r)
        return sorted(seen.values(), key=lambda r: r.name.lower())

    return {
        "queries": [{"id": q.pk, "name": q.name, "version": q.version} for q in latest(spaces.queries(opened))],
        "artifacts": [
            {"id": a.pk, "name": a.name, "version": a.version, "queries": [q.get("name") for q in a.queries or []]}
            for a in latest(spaces.artifacts(opened))
        ],
    }


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


#: data_validate (not scout-queries' explorer_validate): over a space's sources too.
VALIDATE_IN_SPACE = {
    **VALIDATE,
    "description": VALIDATE["description"]
    + " With `space_id`, checks it over the space's sources (a workflow space's pipelines are tables too).",
    "input_schema": {**VALIDATE["input_schema"], "properties": {**VALIDATE["input_schema"]["properties"], **_SPACE}},
}


def validate(user, sql, space_id=None):
    try:
        if space_id is not None:
            return service.validate_selected(sql, _space(_caller(user), space_id))
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
        "column_types (integer, numeric, date, timestamp, text, bool, json), rows, row_count, truncated (and "
        "row_cap: the limit and whether it was the default, your max_rows or the hard cap), "
        "the SQL actually executed, `notes` to read before answering and "
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


#: data_query only: what it can read besides opportunities.
QUERY_SELECTION = (
    " Instead of `opportunity_ids` you may pass `program_ids` (every opportunity of each program the person "
    "holds) or a `workflow` -- then its pipelines are relations too, `p_<alias>`: the stored rows the workflow "
    "draws, one per visit, worker or case, each pipeline field a typed column (data_describe lists them)."
)


#: data_query only: what it keeps (explorer_query, scout-queries' name, returns rows inline).
QUERY_KEPT = (
    " Through canopy the result is kept for a day at `result_uri` (a labs-result:// resource, also a "
    f"resource_link in the content): the agent gets the first {kept_results.INLINE_ROWS} rows as `sample_rows` "
    "(`sample_is_partial` when there are more) and reads the rest with site_read on that URI, a page at a time "
    "(?page=N&page_size=M, up to 1000) -- never by running the query again; the person's card shows every row. "
    "Other callers get every row in `rows`, and nothing is kept unless they pass keep_result. "
    "Pass `display` to say how the card shows the result: plain-text column labels, formats (counts number_0, "
    "rates percent_1 over a FRACTION, money currency_N, big numbers compact_N, dates date) and a chart (a value "
    "over time: line; categories: bar, which the card sorts largest first; one headline number: stat; at most "
    "five series, never a percent beside a count). The agent's facts carry `column_stats` per column (type, "
    "nulls, distinct, min/max/mean, and the common values of a categorical column)."
)


#: data_query's arguments: explorer_query's, and whether to keep the result for a client
#: that is not canopy.
QUERY_KEPT_SCHEMA = {
    **QUERY["input_schema"],
    "required": ["sql"],
    "properties": {
        **QUERY["input_schema"]["properties"],
        **_SELECTION,
        **_SPACE,
        "sql": {
            "type": "string",
            "description": "One SELECT. The relations are `visits` and, with a workflow, its pipelines `p_<alias>`.",
        },
        "display": result_display.SCHEMA,
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


def query(
    user,
    opportunity_ids=None,
    sql="",
    max_rows=None,
    keep_result=False,
    program_ids=None,
    workflow=None,
    display=None,
    space_id=None,
):
    agent = _from_canopy()
    caller = _caller(user)
    space = _space(caller, space_id, opportunity_ids, program_ids, workflow)
    in_space = {"space_id": space.id} if space.saved else {}
    try:
        # Checked before the query runs (all but which columns exist), so a bad display
        # costs no query.
        shown = result_display.validate(display)
    except result_display.DisplayError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "bad_display"}) from e
    try:
        out = service.query(caller, space, sql, max_rows=_max_rows(max_rows), for_agent=agent, shaped=False)
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
            "sql_formatted": service.format_sql(sql),
            "open_on_page": service.open_on_page_url(space, sql, max_rows=max_rows),
            **in_space,
        }
        if not (agent or keep_result):
            # Nobody will read it back (and only canopy's agent is ever refused rows).
            return withheld
        link = kept_results.save(
            user,
            kept_results.KIND_QUERY,
            withheld,
            access={"opportunity_ids": list(space.selection.ids), "sensitive": False},
            name="Query (rows stay in Labs)",
            description="Production rows withheld from the agent; open it on the Data page.",
        )
        return kept_results.with_link(withheld, link)
    except QueryError as e:
        raise _query_error(e) from e
    try:
        shown = result_display.check_columns(shown, out["columns"])
    except result_display.DisplayError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "bad_display"}) from e
    if shown:
        # In the result itself, so the card reads it from the kept result: a sensitive
        # conversation may keep no tool input.
        out["display"] = shown
    if not (agent or keep_result):
        # Nobody will read it back: the rows go inline, and labs keeps no copy of them.
        return out
    return _kept(user, out, agent)


def explorer_query(user, opportunity_ids, sql, max_rows=None):
    """scout-queries' ``explorer_query``: the result whole and inline, nothing kept.
    Its caller is Claude Code reading ``content[0].text``; it has no View and no
    ``site_read``, so it keeps exactly the shape it was built against."""
    caller = _caller(user)
    space = _space(caller, opportunity_ids=opportunity_ids)
    try:
        return service.query(caller, space, sql, max_rows=_max_rows(max_rows), for_agent=_from_canopy())
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.SensitiveDataRefused as e:
        return {
            "withheld": True,
            "reason": e.code,
            "message": e.message,
            "sql_executed": sql,
            "open_on_page": service.open_on_page_url(space, sql, max_rows=max_rows),
        }
    except QueryError as e:
        raise _query_error(e) from e


def _max_rows(max_rows) -> int | None:
    """None when the caller left it out: the default cap, which (unlike a cap the caller
    chose) is a result being cut off (``engine.run_query``'s ``row_cap``)."""
    return None if max_rows is None else int(max_rows)


def _opps(out: dict) -> list:
    """The opportunities a query result read, as ``service.agent_sees_sensitive_rows`` takes them."""
    from connect_labs.labsdata.scope import Opportunity

    return [
        Opportunity(id=o["id"], name=o.get("name") or "", llo=o.get("llo") or "")
        for o in out.get("opportunities") or []
    ]


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
        **{k: v for k, v in out.items() if k not in ("rows", "sql_formatted")},
        "row_count": summary["row_count"],
        "sample_rows": summary["sample_rows"],
        # Every row reached this agent's sample under the same rule (it ran: service.query
        # refuses an agent labs has not cleared for sensitive rows), so the statistics
        # follow it too; reveal stays explicit so the rule is in one place to read.
        "column_stats": kept_results.column_stats(
            out["columns"], rows, out.get("column_types"), reveal=service.agent_sees_sensitive_rows(agent, _opps(out))
        ),
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
    # The card beside the reply shows the person every row: the reply is for what the
    # rows mean, not a second copy of them.
    facts["notes"] = [
        *facts["notes"],
        "The person sees every row of this result -- and its SQL, on the card's SQL tab -- in a card beside your "
        "reply. Point at the card and give the answer and its takeaway in two to four sentences -- quoting the "
        "exact figures the question turns on, never a range -- with one line of provenance (what was counted, "
        "the filters, the opportunities). Do NOT re-print the rows as a markdown table and do NOT paste the SQL: "
        "both are already on the card.",
    ]
    return kept_results.with_link(facts, link)


for _name, _spec, _fn in (
    ("data_describe", DESCRIBE, describe),
    ("data_validate", VALIDATE_IN_SPACE, validate),
    (
        "data_query",
        {
            **QUERY,
            "description": QUERY["description"] + QUERY_SELECTION + QUERY_KEPT,
            "input_schema": QUERY_KEPT_SCHEMA,
        },
        query,
    ),
    # scout-queries' names for the same three (see the module docstring), without the
    # result View: they are called from Claude Code, not drawn in canopy.
    ("explorer_describe", DESCRIBE_OPPORTUNITIES_ONLY, describe),
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
        "for sensitive data reads only SYNTHETIC opportunities. In a data space pass `space_id` instead of "
        "opportunity_ids."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": _OPPS,
            **_SPACE,
            "visit_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_VISITS_PER_READ,
                "description": "Connect visit ids (the `visit_id` column).",
            },
        },
        "required": ["visit_ids"],
        "additionalProperties": False,
    },
)
def data_visits(user, visit_ids, opportunity_ids=None, space_id=None):
    caller = _caller(user)
    space = _space(caller, space_id, opportunity_ids)
    try:
        return service.visits(caller, space, visit_ids, for_agent=_from_canopy())
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
        "data_visits. Definitions only, no visit rows. Give the workflow's opportunity_id OR program_id. In a "
        "data space with a workflow, pass `space_id` alone: it reads the space's workflow."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "definition_id": {"type": "integer", "description": "The workflow (definition) id."},
            "opportunity_id": {"type": "integer"},
            "program_id": {"type": "integer"},
            **_SPACE,
        },
        "additionalProperties": False,
    },
)
def data_workflow_context(user, definition_id=None, opportunity_id=None, program_id=None, space_id=None):
    caller = _caller(user)
    if space_id is not None:
        opened = _space(caller, space_id)
        w = opened.space.workflow or {}
        if not w:
            raise MCPToolError(
                "NOT_FOUND", f"Data space {opened.id} has no workflow.", {"reason": "no_workflow_in_space"}
            )
        if definition_id is not None and _ids([definition_id]) != _ids([w.get("id")]):
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"This call is held to data space {opened.id}, whose workflow is {w.get('id')}.",
                {"reason": "outside_space", "space_id": opened.id},
            )
        definition_id, opportunity_id, program_id = w["id"], w.get("opportunity_id"), w.get("program_id")
    if definition_id is None:
        raise MCPToolError("INVALID_SCHEMA", "Give definition_id (or space_id).", {"reason": "definition_required"})
    try:
        # Canopy on a page: only that page's workflow.
        page_holds.hold_workflow(definition_id)
    except page_holds.OutsidePage as e:
        raise _outside_page(e) from e
    try:
        return workflow_context(caller, int(definition_id), opportunity_id=opportunity_id, program_id=program_id)
    except ScopeError as e:
        raise _scope_error(e) from e
    except WorkflowNotFound as e:
        raise MCPToolError("NOT_FOUND", str(e), {"reason": e.code}) from e


@register(
    name="data_artifact",
    description=(
        "Read one artifact kept in a data space: its JSX code and its named queries ({name, sql, max_rows}), and "
        "its versions. An artifact is a small labs page -- render code drawn in the workflow sandbox on the Data "
        "page, reading its queries' rows as `pipelines.<name>.rows`, exactly as a workflow's render code reads "
        "its pipelines. Definitions only: never rows (the page runs the queries for the person, as them, and "
        "only the page draws them). data_describe with `space_id` lists a space's artifacts under "
        "`space.items.artifacts`. To change one, draft a new version with page_showArtifactDraft and propose it "
        "with page_proposeSave."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "space_id": {**_SPACE["space_id"], "description": "The data space the artifact is kept in."},
            "artifact_id": {"type": "integer", "description": "The artifact (or one version's) id."},
        },
        "required": ["space_id", "artifact_id"],
        "additionalProperties": False,
    },
)
def data_artifact(user, space_id, artifact_id):
    opened = _space(_caller(user), space_id)
    artifact = spaces.artifact_item(opened, artifact_id)
    if artifact is None:
        raise MCPToolError(
            "NOT_FOUND", f"Data space {opened.id} has no artifact {artifact_id}.", {"reason": "no_such_artifact"}
        )
    versions = spaces.artifacts(opened).filter(name=artifact.name).order_by("-version")
    return {
        **artifact.as_dict(),
        "versions": [
            {"id": v.pk, "version": v.version, "saved_by": getattr(v.saved_by, "username", "") or ""} for v in versions
        ],
        "space_id": opened.id,
    }


# --- a data space's dictionary and de-identified exports (labsdata/dictionary.py, exports.py) ---


def _space_tool_error(e) -> MCPToolError:
    return MCPToolError("INVALID_SCHEMA", e.message, {"reason": e.code})


@register(
    name="data_dictionary",
    description=(
        "A data space's data dictionary: every field its visits carry (each form path, per deliver unit, with "
        "the app builds it was seen in), what kind of value it holds (identifier, free text, gps, case id, date, "
        "number, categorical...), what it means, and what a de-identified export does with it (keep, drop, round, "
        "pseudonymise, month, shift, coarsen, scrub). A field is exported only once a person reviewed it: "
        "`effective` says what an export does now. Also open flags from the last scan (new fields, new spellings, "
        "fields a newer app build stopped sending). Structure only: never a value. Settings are changed by the "
        "space's managers on its Dictionary page, not here."
    ),
    input_schema={
        "type": "object",
        "properties": {"space_id": {**_SPACE["space_id"], "description": "The saved data space."}},
        "required": ["space_id"],
        "additionalProperties": False,
    },
)
def data_dictionary(user, space_id):
    from connect_labs.labsdata import dictionary

    opened = _space(_caller(user), space_id)
    try:
        out = dictionary.describe(opened)
    except dictionary.DictionaryError as e:
        raise _space_tool_error(e) from e
    out.pop("changes", None)
    return out


@register(
    name="data_exports",
    description=(
        "A data space's de-identified exports: who asked, the date window, rows, status (queued, building, "
        "blocked, ready for sign-off, signed off, rejected, expired) and every check's result (rows against "
        "visits, the leak scan, value ranges, duplicates, visits with no case, small groups). Counts and "
        "column names only, never a value. Downloading and signing off are a person's, on the space's "
        "Dictionary page."
    ),
    input_schema={
        "type": "object",
        "properties": {"space_id": {**_SPACE["space_id"], "description": "The saved data space."}},
        "required": ["space_id"],
        "additionalProperties": False,
    },
)
def data_exports(user, space_id):
    from connect_labs.labsdata import exports

    opened = _space(_caller(user), space_id)
    return {"space_id": opened.id, "exports": [exports.describe(e, opened) for e in exports.for_space(opened)[:50]]}


@register(
    name="data_export_request",
    description=(
        "Ask for a de-identified export of a data space, optionally for a date window (YYYY-MM-DD). It applies "
        "the space's dictionary to every visit in the window, refuses any field the dictionary does not know, "
        "and checks its own output; then a manager of the space other than you signs it off on the Dictionary "
        "page before anyone can download it. Returns the export's id and status; poll with data_exports."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "space_id": {**_SPACE["space_id"], "description": "The saved data space."},
            "date_from": {"type": "string", "description": "First visit date, YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Last visit date, YYYY-MM-DD."},
        },
        "required": ["space_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def data_export_request(user, space_id, date_from=None, date_to=None):
    from connect_labs.labsdata import dictionary, exports

    opened = _space(_caller(user), space_id)
    try:
        export = exports.request(opened, user=user, caller=_caller(user), date_from=date_from, date_to=date_to)
    except (dictionary.DictionaryError, exports.ExportError) as e:
        raise _space_tool_error(e) from e
    return {"export": exports.describe(export, opened)}
