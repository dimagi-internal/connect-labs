"""How a workflow turns visits into what it shows, in terms of labs data's ``visits``.

"Is this workflow doing what we want?" is the question labs data is mostly for. A
workflow reads pipelines; a pipeline extracts fields from each visit's form (a list
of candidate paths per field, a transform, a filter) and aggregates them by a key.
This module hands an agent -- or a person -- that recipe for one workflow, plus
``visits_sql``: a query over ``visits`` that extracts each pipeline's fields per
visit with the pipeline's OWN expressions (``generate_sql_preview``), so a number on
the dashboard can be recomputed from the raw data and the visits behind it read
whole.

``visits_sql`` is checked end to end over zero rows before it is returned; one that
does not run is reported with its error rather than handed over broken.
"""

from __future__ import annotations

import logging

from connect_labs.labs.access.scopes import Caller

from . import engine, guidance, scope

logger = logging.getLogger(__name__)

#: Raw columns every per-visit extraction carries, so rows can be grouped the way the
#: pipeline groups them and traced back to a visit.
_KEY_COLUMNS = ("opportunity_id", "visit_id", "username", "entity_id", "visit_date", "status")


class WorkflowNotFound(Exception):
    code = "workflow_not_found"


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def visits_sql(field_expressions: dict, predicates: list[str]) -> str:
    """One SELECT over ``visits``: the key columns, then each field as the pipeline computes it."""
    columns = list(_KEY_COLUMNS)
    for name, expr in field_expressions.items():
        sql = (expr or {}).get("transformed_sql") or (expr or {}).get("extraction_sql")
        if sql:
            columns.append(f"{sql} AS {_quote(name)}")
    where = f"\nWHERE {' AND '.join(predicates)}" if predicates else ""
    return "SELECT " + ",\n       ".join(columns) + "\nFROM visits" + where


def _schema_fields(schema: dict) -> list[dict]:
    keep = ("name", "path", "paths", "aggregation", "transform", "filter_path", "filter_value", "description")
    return [{k: f[k] for k in keep if k in f} for f in (schema or {}).get("fields") or [] if isinstance(f, dict)]


def _pipeline(source: dict, token: str, owner: dict) -> dict:
    """What one pipeline source reads and how, read where its record lives."""
    from connect_labs.labs.analysis.backends.sql.query_builder import generate_sql_preview
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT, raw_cache_slot
    from connect_labs.workflow.data_access import PIPELINE_HOME_SCOPE_KEYS, PipelineDataAccess

    pipeline_id = int(source["pipeline_id"])
    home = {k: int(v) for k, v in (source.get("home_scope") or {}).items() if k in PIPELINE_HOME_SCOPE_KEYS and v}
    entry = {"alias": source.get("alias"), "pipeline_id": pipeline_id}
    pda = PipelineDataAccess(access_token=token, **(home or owner))
    try:
        definition = pda.get_definition(pipeline_id)
        if definition is None:
            return {**entry, "error": "pipeline not readable here; try pipeline_get with its home scope"}
        schema = definition.schema or {}
        config = pda._schema_to_config(schema, pipeline_id)
    except Exception as e:  # noqa: BLE001 -- one unreadable pipeline must not cost the rest
        logger.info("labsdata: pipeline %s unreadable", pipeline_id, exc_info=True)
        return {**entry, "error": f"pipeline could not be read: {str(e)[:200]}"}
    finally:
        pda.close()

    source_type = getattr(config.data_source, "type", None)
    entry.update(
        {
            "name": getattr(definition, "name", ""),
            "data_source": source_type,
            "grouping_key": schema.get("grouping_key"),
            "terminal_stage": schema.get("terminal_stage"),
            "filters": schema.get("filters") or {},
            "fields": _schema_fields(schema),
        }
    )
    if raw_cache_slot(pipeline_id, source_type, config.data_source) != USER_VISITS_RAW_SLOT:
        entry["visits_sql"] = None
        entry["note"] = (
            "This pipeline reads another source (not Connect visits), so its rows are not in `visits`. "
            "Read its definition with pipeline_get."
        )
        return entry
    # Only the field expressions and row filters are used: ``visits`` is already scoped,
    # so the scope the preview's own WHERE names does not matter.
    preview = generate_sql_preview(config, owner.get("opportunity_id") or 0)
    sql = visits_sql(preview.get("field_expressions") or {}, preview.get("visit_filter_predicates") or [])
    try:
        engine.validate_query(sql)
        entry["visits_sql"] = sql
    except engine.QueryError as e:
        entry["visits_sql"] = None
        entry["visits_sql_error"] = {**e.as_dict(), "sql": sql}
    return entry


def workflow_context(
    caller: Caller, definition_id: int, *, opportunity_id: int | None = None, program_id: int | None = None
) -> dict:
    """A workflow's opportunities, registry binding and pipelines, as labs data reads them.

    Definitions only -- no visit rows -- so it needs no sensitive-data clearance; the
    opportunities it names are checked against what the caller holds before any
    pipeline is read.
    """
    from connect_labs.labsdata.service import _token
    from connect_labs.workflow.agent_sharing import agent_config
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.visit_cache import workflow_opportunity_ids

    owner = {"opportunity_id": opportunity_id} if opportunity_id is not None else {"program_id": program_id}
    if (opportunity_id is None) == (program_id is None):
        raise scope.ScopeError("bad_scope", "give exactly one of opportunity_id or program_id")
    token = _token(caller)
    wda = WorkflowDataAccess(access_token=token, **owner)
    try:
        definition = wda.get_definition(int(definition_id))
    finally:
        wda.close()
    if definition is None:
        raise WorkflowNotFound(f"No workflow {definition_id} readable in that scope.")

    opp_ids = workflow_opportunity_ids(definition, opportunity_id)
    held = scope.resolve(caller, opp_ids[: scope.MAX_OPPORTUNITIES]) if opp_ids else []
    pipelines = [
        _pipeline(s, token, owner)
        for s in definition.pipeline_sources
        if isinstance(s, dict) and s.get("pipeline_id") is not None
    ]
    return {
        "workflow": {
            "id": definition.id,
            "name": definition.name,
            "template": getattr(definition, "template_type", None),
            "registry_source": definition.registry_source or None,
            # The author's brief for an embedded agent (config.agent.brief, workflow/agent_sharing.py).
            "brief": agent_config(definition).brief or None,
        },
        "opportunities": [{"id": o.id, "name": o.name, "llo": o.llo} for o in held],
        "opportunities_not_listed": opp_ids[scope.MAX_OPPORTUNITIES :],
        # The programme's other opportunities, held by the visitor, that this workflow does
        # not read: what its programme-level numbers silently leave out.
        "programme_opportunities_not_in_workflow": [
            {"id": o.id, "name": o.name, "llo": o.llo, "program": o.program_name}
            for o in scope.left_out_of(caller, opp_ids)
        ],
        "pipelines": pipelines,
        "how_to_verify": guidance.VERIFYING_A_WORKFLOW,
    }
