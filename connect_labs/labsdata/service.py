"""Labs data's operations, shared by the MCP tools, the page and the labsdata agent.

``describe`` tells a person or an agent what they can query and how to answer
from it; ``query`` runs SQL; ``visits`` reads whole visits down to their form
JSON; ``workflow_context`` explains how a workflow turns those visits into what
it shows. Each starts at ``scope.resolve``, so no surface can forget the access
check, and every read is written to the audit trail with the opportunities it
read (and, for SQL, a hash of the statement).

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
from dataclasses import dataclass, field

from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.audit_trail.service import record
from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.synthetic.provenance import all_generated

from . import engine, fields, guidance, scope
from .validator import DEFAULT_MAX_ROWS, HARD_MAX_ROWS

logger = logging.getLogger(__name__)

RESOURCE_TYPE = "explorer_query"
VISITS_RESOURCE_TYPE = "labsdata_visits"


@dataclass
class Selection:
    """What one call reads: opportunities (each held), the programs they came from, and
    -- when a workflow was chosen -- the workflow and its pipelines' relations."""

    opportunities: list
    programs: list[dict] = field(default_factory=list)
    workflow: dict | None = None
    relations: list = field(default_factory=list)
    not_offered: list[dict] = field(default_factory=list)

    @property
    def ids(self) -> list[int]:
        return [o.id for o in self.opportunities]

    def as_dict(self) -> dict:
        return {
            "opportunities": [{"id": o.id, "name": o.name, "llo": o.llo} for o in self.opportunities],
            "programs": self.programs,
            "workflow": self.workflow,
            "pipelines": [
                {
                    "relation": r.name,
                    "alias": r.alias,
                    "pipeline_id": r.pipeline_id,
                    "name": r.pipeline_name,
                    "stage": r.stage,
                }
                for r in self.relations
            ],
            "pipelines_not_offered": self.not_offered,
        }


def select(caller: Caller, opportunity_ids=None, *, program_ids=None, workflow: dict | None = None) -> Selection:
    """Resolve what a call reads, refusing anything the caller does not hold.

    Either programs and/or opportunities (a program is the opportunities of it the
    caller holds), or one workflow -- whose opportunities are the ones it reads,
    every one held, plus its pipelines as relations. Not both: a workflow's pipeline
    relations cover only its own opportunities, and ``visits`` beside them over a
    different set would answer a different question without saying so.
    """
    if workflow:
        if opportunity_ids or program_ids:
            raise scope.ScopeError(
                "bad_scope", "choose a workflow, or programs and opportunities -- a workflow brings its own"
            )
        from .pipelines import workflow_selection

        try:
            chosen = workflow_selection(
                caller,
                int(workflow["id"]),
                opportunity_id=workflow.get("opportunity_id"),
                program_id=workflow.get("program_id"),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise scope.ScopeError("bad_scope", "workflow needs an id and its opportunity_id or program_id") from e
        return Selection(
            opportunities=chosen.opportunities,
            workflow=chosen.workflow,
            relations=chosen.relations,
            not_offered=chosen.not_offered,
        )
    opps, programs = scope.expand(caller, opportunity_ids or [], program_ids or [])
    return Selection(opportunities=opps, programs=programs)


#: The most scopes (opportunities and programs) searched for workflows in one call.
MAX_WORKFLOW_SCOPES = 12


def workflows(caller: Caller, *, opportunity_ids=None, program_ids=None) -> dict:
    """Workflows readable, as the caller, in the programs and opportunities chosen.

    Each comes with the scope it was found in -- the scope it is then read in, so a
    workflow is only ever opened where the caller's own token can reach it. Pages
    (workflows with no runs) are left out: they have no pipelines to query.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.page_mode import is_page

    opps, programs = scope.expand(caller, opportunity_ids or [], program_ids or [])
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
        "Validate the SQL with data_validate, try it on a synthetic opportunity, then give the person the "
        "SQL to run on the Data page (/labs/data/) -- they see the result there."
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
    opportunity_ids,
    *,
    field_search: str | None = None,
    load_missing: bool = False,
    include_registries: bool = True,
    for_agent: bool = False,
    include_fields: bool = True,
    program_ids=None,
    workflow: dict | None = None,
) -> dict:
    """What can be queried, and how to answer from it.

    ``for_agent`` (a canopy call): STRUCTURE only -- form paths, types, the SQL that
    reads each, the registries -- with no answer values, fill rates or row counts,
    unless every opportunity is synthetic or labs has cleared the agent for sensitive data.
    The person on the page gets everything.
    """
    selection = select(caller, opportunity_ids, program_ids=program_ids, workflow=workflow)
    opps = selection.opportunities
    withheld = not agent_sees_sensitive_rows(for_agent, opps)
    loaded = warm(caller, opps) if load_missing and not withheld else []
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


def _pipeline_relations(selection: Selection, withheld: bool) -> dict:
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
    return out


def validate_selected(sql: str, selection: Selection) -> dict:
    """``validate`` over a resolved selection: a workflow's pipelines are tables too."""
    return engine.validate_query(sql, relations=selection.relations, field_opps=selection.opportunities)


def validate(sql: str) -> dict:
    """Check SQL end to end over zero rows. Touches no data, so it needs no scope.

    Over ``visits`` only; a query naming a workflow's pipelines is checked by running
    it with that workflow chosen."""
    return engine.validate_query(sql)


def query(
    caller: Caller,
    opportunity_ids,
    sql: str,
    max_rows: int | None = None,
    *,
    for_agent: bool = False,
    shaped: bool = True,
    program_ids=None,
    workflow: dict | None = None,
) -> dict:
    """Run SQL. ``for_agent`` (a canopy call) gets production rows only when cleared
    (``agent_sees_sensitive_rows``), and its result is shaped for a model's context --
    unless ``shaped`` is False: the caller keeps the whole result and shapes what it
    hands the model itself (the MCP tool, which keeps the rows as a result to read back)."""
    selection = select(caller, opportunity_ids, program_ids=program_ids, workflow=workflow)
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
        "open_on_page": open_on_page_url(
            opportunity_ids, sql, program_ids=program_ids, workflow=workflow, max_rows=max_rows
        ),
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


def drill_into(
    caller: Caller, opportunity_ids, sql: str, row: list, column: int, *, program_ids=None, workflow=None
) -> dict:
    """The SQL selecting the rows behind one cell of ``sql``'s result, and how many there are.

    The SQL is written from the query alone (``drill.py``); the count is a query of its
    own, scoped, checked and audit-logged like any other. For a cell that counts rows
    (``COUNT(*)``, ``COUNT(*) FILTER``, ``SUM(CASE WHEN .. THEN 1)``) the count should
    equal the cell, and ``matches`` says whether it does.
    """
    from . import drill

    try:
        found = drill.drill(sql, list(row or []), int(column))
    except drill.NotDrillable as e:
        raise engine.QueryError("not_drillable", str(e)) from e
    counted = query(
        caller,
        opportunity_ids,
        f"SELECT COUNT(*) AS rows_behind FROM (\n{found.sql}\n) AS behind",
        max_rows=1,
        program_ids=program_ids,
        workflow=workflow,
    )
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


def open_on_page_url(
    opportunity_ids, sql: str = "", *, program_ids=None, workflow: dict | None = None, max_rows: int | None = None
) -> str:
    """The Data page with this selection, this SQL in the editor and its row cap set:
    everything it takes to run the same query again (the page runs it only when the link
    asks, ``&run=1``, and the person is signed in: ``views.DataView``)."""
    from urllib.parse import urlencode

    from django.conf import settings
    from django.urls import reverse

    params = {}
    if workflow:
        owner = workflow.get("opportunity_id") or workflow.get("program_id")
        kind = "opp" if workflow.get("opportunity_id") else "program"
        params["workflow"] = f"{int(workflow['id'])}:{kind}:{int(owner)}"
    if program_ids:
        params["programs"] = ",".join(str(int(p)) for p in program_ids)
    if opportunity_ids:
        params["opps"] = ",".join(str(int(o)) for o in opportunity_ids)
    if sql:
        params["sql"] = sql
    if max_rows is not None:
        params["max_rows"] = str(max(1, min(int(max_rows), HARD_MAX_ROWS)))
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return f"{base}{reverse('labsdata:index')}?{urlencode(params)}"


def visits(caller: Caller, opportunity_ids, visit_ids, *, for_agent: bool = False) -> dict:
    """Whole visits by id -- every column and the full form_json -- from the caller's opportunities."""
    opps = scope.resolve(caller, opportunity_ids)
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
