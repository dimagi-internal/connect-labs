"""Labs data's operations, shared by the MCP tools, the page and the labsdata agent.

``describe`` tells a person or an agent what they can query and how to answer
from it; ``query`` runs SQL; ``visits`` reads whole visits down to their form
JSON; ``workflow_context`` explains how a workflow turns those visits into what
it shows. Each reads over a DATA SPACE and nothing else (``spaces.OpenSpace``:
saved and opened, or generated for the call from a selection -- ``spaces.from_request``),
so the sources were resolved as the caller by ``spaces`` before anything here runs, and
no surface can pass ids that skipped the access check. Every read is written to the audit
trail with the opportunities it read (and, for SQL, a hash of the statement).

**Who sees production rows.** A person (the page, their own MCP sign-in) sees everything
they hold. An agent calling through canopy as a visitor (``for_agent``) sees production
rows -- labs' sensitive data -- only when labs has cleared it for sensitive data
(``labs/canopy_panel.py``:
``call_sensitive_data_clearance`` -- an agent declared to handle sensitive data, on
sensitive-data runners, vouched for
by canopy in this call's verified ``Canopy-Execution`` statement). Otherwise it gets
structure, validation and synthetic data, and hands SQL to the page, where the person
runs it.
"""

from __future__ import annotations

import hashlib
import logging
import re

from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.audit_trail.service import record
from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.synthetic.provenance import all_generated

from . import engine, fields, guidance, pipelines, scope, spaces
from .spaces import OpenSpace
from .validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "explorer_query"
VISITS_RESOURCE_TYPE = "labsdata_visits"


def _held(space) -> OpenSpace:
    """The space a read runs over -- refusing anything else, so ids that never went
    through ``spaces`` (generate / open) cannot reach the engine."""
    if not isinstance(space, OpenSpace):
        raise TypeError("labsdata reads take a data space (spaces.from_request / generate / open), not ids")
    return space


#: The most scopes (opportunities and programs) searched for workflows in one call.
MAX_WORKFLOW_SCOPES = 12


def workflows(caller: Caller, space: OpenSpace) -> dict:
    """Workflows readable, as the caller, in a space's programs and opportunities.

    Each comes with the scope it was found in -- the scope it is then read in, so a
    workflow is only ever opened where the caller's own token can reach it. Pages
    (workflows with no runs) are left out: they have no pipelines to query.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.page_mode import is_page

    selection = _held(space).selection
    opps, programs = selection.opportunities, selection.programs
    scopes_ = [{"program_id": p["id"]} for p in programs] + [{"opportunity_id": o.id} for o in opps]
    token = _token(caller)
    found, seen, unreadable = [], set(), []
    for owner in scopes_[:MAX_WORKFLOW_SCOPES]:
        access = WorkflowDataAccess(access_token=token, **owner)
        try:
            records = access.list_definitions()
        except Exception:  # noqa: BLE001 -- one scope Connect will not list must not cost the rest
            logger.info("labsdata: could not list workflows in %s", owner, exc_info=True)
            unreadable.append(owner)
            continue
        finally:
            access.close()
        for r in records:
            if r.id in seen or is_page(r):
                continue
            seen.add(r.id)
            found.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "scope": owner,
                    "opportunities": len(r.opportunity_ids or []) or 1,
                    "pipelines": len([s for s in r.pipeline_sources or [] if isinstance(s, dict)]),
                }
            )
    return {
        "workflows": sorted(found, key=lambda w: (w["name"] or "").lower()),
        "scopes_searched": scopes_[:MAX_WORKFLOW_SCOPES],
        "scopes_not_searched": scopes_[MAX_WORKFLOW_SCOPES:],
        "scopes_unreadable": unreadable,
    }


def _token(caller: Caller) -> str | None:
    if caller.access_token:
        return caller.access_token
    if caller.request is not None:
        oauth = getattr(caller.request, "session", {}).get("labs_oauth") or {}
        if oauth.get("access_token"):
            return oauth["access_token"]
    if caller.user is not None:
        from connect_labs.labs.connect_tokens import get_valid_access_token

        try:
            return get_valid_access_token(caller.user)
        except Exception:  # noqa: BLE001
            return None
    return None


def warm(caller: Caller, opps: list[scope.Opportunity]) -> list[dict]:
    """Load any opportunity that has nothing cached, as the caller. Reports, never raises."""
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    token = _token(caller)
    report = []
    pending = [s for s in fields.cache_status(opps) if not s["cached_visits"]]
    if not pending:
        return report
    if not token:
        return [{"opportunity_id": s["opportunity_id"], "error": "no Connect token to load with"} for s in pending]
    analysis = AnalysisPipeline(access_token=token)
    for s in pending:
        oid = s["opportunity_id"]
        try:
            rows = analysis.backend.fetch_raw_visits(
                opportunity_id=oid,
                access_token=token,
                expected_visit_count=analysis.expected_visits_for(oid),
                skip_form_json=True,
                user=caller.user,
            )
            report.append({"opportunity_id": oid, "loaded_visits": len(rows or [])})
        except Exception as e:  # noqa: BLE001 -- one opportunity must not cost the rest
            logger.warning("explorer warm failed for opp %s", oid, exc_info=True)
            report.append({"opportunity_id": oid, "error": str(e)[:200]})
    return report


def related_registries(caller: Caller, opps: list[scope.Opportunity]) -> list[dict]:
    """Semantic registries whose deployment names one of these opportunities."""
    token = _token(caller)
    if not token:
        return []
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    ids = {str(o.id) for o in opps}
    out = []
    try:
        access = SemanticRegistryDataAccess(access_token=token)
        try:
            for r in access.list_registries(include_shared=True):
                llo_map = (r.deployment or {}).get("llo_map") or {}
                covered = sorted(int(k) for k in llo_map if k in ids)
                if covered:
                    out.append({"registry_id": r.id, "name": r.name, "covers_opportunities": covered})
        finally:
            access.close()
    except Exception:  # noqa: BLE001 -- the registry list is a pointer, not the answer
        logger.info("explorer: registry lookup failed", exc_info=True)
    return out


class SensitiveDataRefused(Exception):
    """An agent asked for production rows (sensitive data) it is not cleared for. It may write
    SQL; the person runs it."""

    code = "sensitive_data_stays_in_labs"
    message = (
        "Production visit data is sensitive: it reaches an agent only when Labs has cleared it for sensitive "
        "data, and this one is not. "
        "Validate the SQL with data_validate, try it on a synthetic opportunity, then show it to the person: "
        "on the Data page with page_showQuery (it runs there, as them), elsewhere as SQL to run at /labs/data/."
    )


def agent_sees_sensitive_rows(for_agent: bool, opps) -> bool:
    """Whether this call may return production rows: always for a person; for an agent only when
    cleared for sensitive data."""
    if not for_agent or all_generated([o.id for o in opps]):
        return True
    from connect_labs.labs import canopy_panel

    return canopy_panel.call_sensitive_data_clearance().granted


#: What an agent receives from one call, at most. A visit's form_json alone can run to
#: tens of kilobytes; past these the result is cut (and says so) rather than flooding
#: the model's context. A person on the page gets the result whole.
AGENT_CELL_CHARS = 4000
AGENT_RESULT_CHARS = 200_000


def shape_for_agent(result: dict) -> dict:
    """Cap long cells and the whole result, with notes written for the model (Scout's rule)."""
    import json

    rows, cut_cells, size = [], 0, 0
    for row in result.get("rows") or []:
        shaped = []
        for cell in row:
            text = (
                cell
                if isinstance(cell, str)
                else json.dumps(cell, default=str) if isinstance(cell, dict | list) else None
            )
            if text is not None and len(text) > AGENT_CELL_CHARS:
                cell = text[:AGENT_CELL_CHARS] + f"... [cut: {len(text)} chars]"
                cut_cells += 1
            shaped.append(cell)
        size += len(json.dumps(shaped, default=str))
        if size > AGENT_RESULT_CHARS:
            break
        rows.append(shaped)
    notes = list(result.get("notes") or [])
    if result.get("truncated"):
        notes.append(
            "These rows hit the row cap: do not present them as complete. To answer from all of the data, "
            "aggregate further (GROUP BY, COUNT, percentiles) or filter, then query again. If the person "
            f"needs every row, give them `open_on_page`: the Data page runs it for them (up to {HARD_MAX_ROWS} "
            "rows, raise max rows there) and downloads it as CSV; the rows never pass through you."
        )
    if len(rows) < len(result.get("rows") or []):
        notes.append(
            f"Only the first {len(rows)} of {result['row_count']} rows fit in your context. Select fewer "
            "columns (form_json is large) or aggregate."
        )
    if cut_cells:
        notes.append(
            f"{cut_cells} long cell(s) were cut at {AGENT_CELL_CHARS} characters. Read whole visits, form JSON "
            "and all, with data_visits; or select the specific fields with form_json #>> '{form,...}'."
        )
    return {**result, "rows": rows, "rows_returned": len(rows), "notes": notes}


#: Field-index keys computed from real answers. An agent gets structure only.
_DATA_DERIVED_FIELD_KEYS = ("values", "filled_pct", "distinct_in_sample")


def _structure_only(field: dict) -> dict:
    return {k: v for k, v in field.items() if k not in _DATA_DERIVED_FIELD_KEYS}


def describe(
    caller: Caller,
    space: OpenSpace,
    *,
    field_search: str | None = None,
    load_missing: bool = False,
    include_registries: bool = True,
    for_agent: bool = False,
    include_fields: bool = True,
) -> dict:
    """What can be queried, and how to answer from it.

    ``for_agent`` (a canopy call): STRUCTURE only -- form paths, types, the SQL that
    reads each, the registries -- with no answer values, fill rates or row counts,
    unless every opportunity is synthetic or labs has cleared the agent for sensitive data.
    The person on the page gets everything.
    """
    selection = _held(space).selection
    opps = selection.opportunities
    withheld = not agent_sees_sensitive_rows(for_agent, opps)
    loaded = warm(caller, opps) if load_missing and not withheld else []
    rebuilt = _refill_pipelines(caller, selection) if load_missing and not withheld else None
    described = (
        fields.search_fields(fields.describe_fields(opps), field_search)
        if include_fields
        else {"fields": [], "sampled_visits": None}
    )
    status = fields.cache_status(opps)
    if withheld:
        described["fields"] = [_structure_only(f) for f in described["fields"]]
        status = [
            {"opportunity_id": s["opportunity_id"], "opportunity_name": s["opportunity_name"], "llo": s["llo"]}
            for s in status
        ]
    return {
        **selection.as_dict(),
        "opportunities": status,
        "sensitive_data_visible_to_you": not withheld,
        "loaded": loaded,
        **({"pipelines_rebuilt": rebuilt} if rebuilt else {}),
        "relations": {
            "visits": {
                "grain": "one row per Connect visit",
                "columns": [{"name": n, "type": t, "means": m} for n, t, m in engine.VISIT_COLUMNS],
            },
            **_pipeline_relations(selection, withheld),
        },
        "fields": described["fields"],
        "sampled_visits": None if withheld else described["sampled_visits"],
        "field_search": field_search,
        "registries": related_registries(caller, opps) if include_registries else [],
        "answering_rules": guidance.AGENT_RULES if withheld else guidance.ANSWERING_RULES,
        "verifying_a_workflow": guidance.VERIFYING_A_WORKFLOW,
        "sql_notes": guidance.SQL_NOTES,
        "max_rows": {"default": DEFAULT_MAX_ROWS, "max": HARD_MAX_ROWS},
    }


def _pipeline_relations(selection, withheld: bool) -> dict:
    """Each pipeline relation: its grain, its columns (names and types, read from what is
    stored), and -- for a person -- how many current rows each opportunity has."""
    from .pipelines import STAGES

    if not selection.relations:
        return {}
    columns = engine.pipeline_fields(selection.opportunities, selection.relations)
    status = {} if withheld else engine.pipeline_status(selection.opportunities, selection.relations)
    out = {}
    for rel in selection.relations:
        entry = {
            "grain": STAGES[rel.stage]["grain"],
            "pipeline": {"id": rel.pipeline_id, "alias": rel.alias, "name": rel.pipeline_name},
            "columns": [{"name": n, "type": t} for n, t in rel.base_columns]
            + [{"name": n, "type": t, "pipeline_field": True} for n, t in columns.get(rel.name, [])],
        }
        if not withheld:
            counts = status.get(rel.name, {})
            entry["rows_by_opportunity"] = counts
            entry["stale_opportunities"] = sorted(o for o, n in counts.items() if not n)
        out[rel.name] = entry
    if not withheld:
        # Never a silent 0: say why each one has no rows, and how to rebuild them.
        stale = {name: e["stale_opportunities"] for name, e in out.items() if e.get("stale_opportunities")}
        reasons = pipelines.stale_reasons(stale, selection.relations)
        for name, by_opp in reasons.items():
            out[name]["why_no_rows"] = {str(oid): r for oid, r in by_opp.items()}
            states = sorted({r["state"] for r in by_opp.values()})
            out[name]["note"] = (
                f"No current rows on {len(by_opp)} of {len(out[name]['rows_by_opportunity'])} opportunities "
                f"({', '.join(states)}): queries over {name} leave them out. {pipelines.REBUILD}"
            )
    return out


def _refill_pipelines(caller: Caller, selection) -> dict | None:
    """With ``load_missing`` on a workflow: rebuild the pipelines' stored output on every
    opportunity of ``selection`` (the space's ``Selection``) that has none current (a few per
    call; ``pipelines.refill``)."""
    if not selection.relations:
        return None
    status = engine.pipeline_status(selection.opportunities, selection.relations)
    stale = sorted({oid for counts in status.values() for oid, n in counts.items() if not n})
    if not stale:
        return None
    return pipelines.refill(caller, selection, stale)


def validate_selected(sql: str, space: OpenSpace) -> dict:
    """``validate`` over a space: a workflow's pipelines are tables too."""
    selection = _held(space).selection
    return engine.validate_query(sql, relations=selection.relations, field_opps=selection.opportunities)


def validate(sql: str) -> dict:
    """Check SQL end to end over zero rows. Touches no data, so it needs no scope.

    Over ``visits`` only; a query naming a workflow's pipelines is checked by running
    it with that workflow chosen."""
    return engine.validate_query(sql)


def query(
    caller: Caller,
    space: OpenSpace,
    sql: str,
    max_rows: int | None = None,
    *,
    for_agent: bool = False,
    shaped: bool = True,
) -> dict:
    """Run SQL. ``for_agent`` (a canopy call) gets production rows only when cleared
    (``agent_sees_sensitive_rows``), and its result is shaped for a model's context --
    unless ``shaped`` is False: the caller keeps the whole result and shapes what it
    hands the model itself (the MCP tool, which keeps the rows as a result to read back)."""
    selection = _held(space).selection
    opps = selection.opportunities
    if not agent_sees_sensitive_rows(for_agent, opps):
        # Still tell the agent whether its SQL would run: that is what it needs to hand
        # the query to the person, and it reads no data.
        engine.validate_query(sql, relations=selection.relations, field_opps=opps)
        raise SensitiveDataRefused()
    # Never the SQL itself: the audit log carries no PHI content (docs/AUDIT_LOGGING.md),
    # and a query can hold a name or an answer as a literal. The hash still ties an
    # event to a query the caller can produce.
    text = sql or ""
    meta = {
        "opportunity_ids": [o.id for o in opps],
        "sql_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sql_length": len(text),
        **({"workflow_id": selection.workflow["id"]} if selection.workflow else {}),
        **({"program_ids": [p["id"] for p in selection.programs]} if selection.programs else {}),
        **({"space_id": space.id} if space.saved else {}),
    }
    try:
        result = engine.run_query(opps, sql, max_rows=max_rows, relations=selection.relations)
    except engine.QueryError as e:
        record(
            Action.LIST,
            resource_type=RESOURCE_TYPE,
            opportunity_id=opps[0].id,
            outcome=Outcome.FAILURE,
            metadata={**meta, "error": e.code},
            user=caller.user,
        )
        raise
    for o in opps:
        record(
            Action.LIST,
            resource_type=RESOURCE_TYPE,
            opportunity_id=o.id,
            record_count=result.row_count,
            metadata=meta,
            user=caller.user,
        )
    freshness = fields.cached_at(opps)
    out = {
        **result.as_dict(),
        **selection.as_dict(),
        # When each opportunity's visits were loaded from Connect: how fresh the answer is.
        "opportunities": [{"id": o.id, "name": o.name, "llo": o.llo, "cached_at": freshness.get(o.id)} for o in opps],
        # The SQL laid out to read (the card's SQL tab); `sql_executed` stays as it ran.
        "sql_formatted": format_sql(result.sql_executed),
        "drill": drillable(sql),
        "open_on_page": open_on_page_url(space, sql, max_rows=max_rows),
        **({"space_id": space.id} if space.saved else {}),
    }
    return shape_for_agent(out) if (for_agent and shaped) else out


def drillable(sql: str) -> dict:
    """Which result columns can be clicked through to the rows behind them, or why none can."""
    from . import drill

    try:
        kinds = drill.shape(sql).kinds
        return {"columns": [kind is not None for kind in kinds], "kinds": kinds}
    except drill.NotDrillable as e:
        return {"columns": [], "reason": str(e)}


def drill_into(caller: Caller, space: OpenSpace, sql: str, row: list, column: int) -> dict:
    """The SQL selecting the rows behind one cell of ``sql``'s result, and how many there are.

    The SQL is written from the query alone (``drill.py``); the count is a query of its
    own, scoped, checked and audit-logged like any other. For a cell that counts rows
    (``COUNT(*)``, ``COUNT(*) FILTER``, ``SUM(CASE WHEN .. THEN 1)``) the count should
    equal the cell, and ``matches`` says whether it does.
    """
    from . import drill

    _held(space)
    try:
        found = drill.drill(sql, list(row or []), int(column))
    except drill.NotDrillable as e:
        raise engine.QueryError("not_drillable", str(e)) from e
    counted = query(caller, space, f"SELECT COUNT(*) AS rows_behind FROM (\n{found.sql}\n) AS behind", max_rows=1)
    rows_behind = counted["rows"][0][0] if counted["rows"] else 0
    cell = row[int(column)]
    matches = None
    if found.kind == drill.COUNTING and isinstance(cell, int | float) and not isinstance(cell, bool):
        matches = int(cell) == rows_behind
    return {
        "sql": found.sql,
        "kind": found.kind,
        "description": found.description,
        "rows_behind": rows_behind,
        "matches": matches,
    }


def format_sql(sql: str) -> str:
    """``sql`` laid out to read (sqlglot's pretty printer, Postgres dialect), or as it is
    when it cannot be parsed. For showing only: what runs is always ``sql`` itself."""
    import sqlglot

    text = sql or ""
    if not text.strip():
        return text
    try:
        out = sqlglot.transpile(text, read="postgres", write="postgres", pretty=True)
    except Exception:  # noqa: BLE001 -- a layout failure must never fail the query
        return text
    return out[0] if len(out) == 1 and out[0].strip() else text


def open_on_page_url(space: OpenSpace, sql: str = "", *, max_rows: int | None = None) -> str:
    """The Data page on this space, this SQL in the editor and its row cap set: everything
    it takes to run the same query again (the page runs it only when the link asks,
    ``&run=1``, and the person is signed in: ``views.DataView``). A saved space opens on
    its own page (``/labs/data/space/<id>/``) -- a 404 to anyone who may not open it; a
    generated one on the selection it was made from, which the page generates again."""
    from urllib.parse import urlencode

    from django.conf import settings
    from django.urls import reverse

    params = {}
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    if _held(space).saved:
        path = reverse("labsdata:space", args=[int(space.id)])
    else:
        path = reverse("labsdata:index")
        asked = space.requested
        workflow = asked.get("workflow")
        if workflow:
            owner = workflow.get("opportunity_id") or workflow.get("program_id")
            kind = "opp" if workflow.get("opportunity_id") else "program"
            params["workflow"] = f"{int(workflow['id'])}:{kind}:{int(owner)}"
        if asked.get("program_ids"):
            params["programs"] = ",".join(str(int(p)) for p in asked["program_ids"])
        if asked.get("opportunity_ids"):
            params["opps"] = ",".join(str(int(o)) for o in asked["opportunity_ids"])
    if sql:
        params["sql"] = sql
    if max_rows is not None:
        params["max_rows"] = str(max(1, min(int(max_rows), HARD_MAX_ROWS)))
    return f"{base}{path}?{urlencode(params)}" if params else f"{base}{path}"


def visits(caller: Caller, space: OpenSpace, visit_ids, *, for_agent: bool = False) -> dict:
    """Whole visits by id -- every column and the full form_json -- from a space's opportunities."""
    opps = _held(space).selection.opportunities
    if not agent_sees_sensitive_rows(for_agent, opps):
        raise SensitiveDataRefused()
    found = engine.fetch_visits(opps, visit_ids)
    missing = sorted({str(v) for v in visit_ids} - {str(r["visit_id"]) for r in found})
    by_opp: dict[int, int] = {}
    for r in found:
        by_opp[r["opportunity_id"]] = by_opp.get(r["opportunity_id"], 0) + 1
    for oid, count in by_opp.items():
        record(
            Action.READ,
            resource_type=VISITS_RESOURCE_TYPE,
            opportunity_id=oid,
            record_count=count,
            metadata={"visits": count},
            user=caller.user,
        )
    return {
        "visits": found,
        "not_found": missing,
        "notes": (
            [
                "Not found means not in these opportunities' cached visits: check the visit_id and the "
                "opportunity, or whether the cache is fresh (data_describe shows cached_at)."
            ]
            if missing
            else []
        ),
    }


# --- artifacts (models.SpaceArtifact): render code and the named queries it reads ---------
# Drawn only in the workflow sandbox's frame. The Data page (the host) runs the queries
# here, as the person opening the artifact, and hands the frame their rows as the render's
# `pipelines` -- the frame asks for nothing else, and nothing is kept.

#: The most named queries one artifact reads.
ARTIFACT_MAX_QUERIES = 8
#: A query's name: how render code reads its rows (``pipelines.<name>.rows``).
_QUERY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
#: A query is ``{name, sql, max_rows}``. It may also name sources, but only the space's own
#: (``spaces.held_to``): an artifact reads its space and nothing else.
_QUERY_KEYS = frozenset({"name", "sql", "max_rows"})
_SOURCE_KEYS = ("space_id", "opportunity_ids", "program_ids", "workflow")


class ArtifactError(Exception):
    """An artifact that cannot be kept or drawn as given: said as itself (a shape, never data)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def artifact_code(code) -> str:
    """The artifact's JSX, checked as a workflow's render_code is when it is written."""
    from connect_labs.workflow.render_code_lint import render_code_problem

    problem = render_code_problem(code)
    if problem:
        raise ArtifactError("bad_code", problem.replace("render_code", "The artifact's code"))
    return code


def _artifact_query(space: OpenSpace, q) -> dict:
    """One named query's shape, and that it names no source but the space's own."""
    if not isinstance(q, dict):
        raise ArtifactError("bad_queries", "Each query is an object: {name, sql, max_rows}.")
    unknown = set(q) - _QUERY_KEYS - set(_SOURCE_KEYS)
    if unknown:
        raise ArtifactError("bad_queries", f"A query is {{name, sql, max_rows}}; it cannot carry {sorted(unknown)}.")
    # The same refusal a call naming a saved space and another selection gets.
    spaces.held_to(space, **{k: q.get(k) for k in _SOURCE_KEYS})
    name, sql = q.get("name"), q.get("sql")
    if not isinstance(name, str) or not _QUERY_NAME.match(name):
        raise ArtifactError(
            "bad_queries",
            "A query's name is how the code reads it (pipelines.<name>): letters, digits and _, "
            "not starting with a digit, at most 64.",
        )
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise ArtifactError("bad_queries", f"Query {name} needs one SELECT (at most 20,000 characters).")
    try:
        max_rows = max(1, min(int(q.get("max_rows") or DEFAULT_MAX_ROWS), HARD_MAX_ROWS))
    except (TypeError, ValueError):
        max_rows = DEFAULT_MAX_ROWS
    return {"name": name, "sql": sql, "max_rows": max_rows}


def _artifact_shape(space: OpenSpace, queries) -> list[dict]:
    _held(space)
    if not isinstance(queries, list) or not queries:
        raise ArtifactError("bad_queries", "An artifact reads at least one named query: [{name, sql, max_rows}].")
    if len(queries) > ARTIFACT_MAX_QUERIES:
        raise ArtifactError("bad_queries", f"An artifact reads at most {ARTIFACT_MAX_QUERIES} named queries.")
    out = [_artifact_query(space, q) for q in queries]
    names = [q["name"] for q in out]
    if len(set(names)) != len(names):
        raise ArtifactError("bad_queries", "Each query needs its own name.")
    return out


def artifact_queries(space: OpenSpace, queries) -> list[dict]:
    """An artifact's named queries, each checked against the space exactly as a run over it
    would be -- any source it names must be the space's own (``outside_space``), and its SQL
    is validated over the space's relations (``validate_selected``: the validator, then
    Postgres resolving every column, over zero rows) -- as they are kept:
    ``[{name, sql, max_rows}]``. ``engine.QueryError`` names the query that failed."""
    checked = _artifact_shape(space, queries)
    for q in checked:
        try:
            validate_selected(q["sql"], space)
        except engine.QueryError as e:
            raise engine.QueryError(e.code, f"Query {q['name']}: {e.message}") from e
    return checked


def run_artifact(caller: Caller, space: OpenSpace, queries) -> dict:
    """Run an artifact's named queries over the space, as the caller (the person opening it),
    for the host to hand the frame: ``{name: {columns, column_types, rows, row_count,
    truncated, row_cap}}``. Each is checked again first, as at its save (a source outside the
    space refuses the whole artifact), then run through ``query`` -- scoped to the space,
    validated, audited. A query whose SQL fails carries ``error`` and the others still run.
    Nothing is kept."""
    out = {}
    for q in _artifact_shape(space, queries):
        try:
            result = query(caller, space, q["sql"], q["max_rows"])
        except engine.QueryError as e:
            out[q["name"]] = {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "error": {"code": e.code, "message": e.message},
            }
            continue
        out[q["name"]] = {
            k: result.get(k) for k in ("columns", "column_types", "rows", "row_count", "truncated", "row_cap")
        }
    return out
