"""A workflow's pipelines as relations beside ``visits``: what the dashboard was drawn from.

Choosing a workflow on the Data page adds one relation per pipeline it reads,
named ``p_<alias>`` (``p_flw_summary``). Each is the pipeline's own stored result
-- the rows its run computed and labs cached, the same rows the workflow renders
-- so a number on the dashboard can be found, grouped and checked in SQL.

**Why this is safe to offer.** Every step decides on the caller's own authority,
and nothing reaches a relation that the caller could not already read as visits:

1. The workflow is read with the caller's Connect token in the scope they name
   (``workflow_context.read_workflow``): one they could not open in Labs is not found.
2. Every opportunity it spans must be one they hold (``scope.resolve``), or the
   whole selection is refused -- never narrowed.
3. Each pipeline is read where it lives, again as the caller (``read_pipeline``).
   One they cannot read gets no relation.
4. Only pipelines computed from Connect visits get one. Their rows derive from the
   visits of those opportunities, which the caller can already query as
   ``visits``; a pipeline over another source (CommCare cases, a Drive sheet) has
   access rules of its own and is listed, not offered.
5. The relation is built by the engine, filtered to those opportunities and that
   pipeline's current configuration, and the database grants the reader role only
   those ``(opportunity, pipeline)`` rows (``engine._lock``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from connect_labs.labs.access.scopes import Caller

from . import scope
from .workflow_context import read_pipeline, read_workflow

#: Where each terminal stage's rows are stored, the base columns they carry, and
#: the JSON column holding the pipeline's fields.
STAGES: dict[str, dict] = {
    "visit_level": {
        "grain": "one row per visit",
        "table": "labs_computed_visit_cache",
        "fields_column": "computed_fields",
        "columns": [
            ("visit_id", "text"),
            ("username", "text"),
            ("visit_date", "date"),
            ("status", "text"),
            ("flagged", "boolean"),
            ("deliver_unit", "text"),
            ("entity_id", "text"),
            ("entity_name", "text"),
        ],
    },
    "aggregated": {
        "grain": "one row per worker (username)",
        "table": "labs_computed_flw_cache",
        "fields_column": "aggregated_fields",
        "columns": [
            ("username", "text"),
            ("total_visits", "integer"),
            ("approved_visits", "integer"),
            ("pending_visits", "integer"),
            ("rejected_visits", "integer"),
            ("flagged_visits", "integer"),
            ("first_visit_date", "date"),
            ("last_visit_date", "date"),
        ],
    },
    "entity": {
        "grain": "one row per case (the pipeline's grouping)",
        "table": "labs_computed_entity_cache",
        "fields_column": "aggregated_fields",
        "columns": [
            ("entity_id", "text"),
            ("entity_name", "text"),
            ("username", "text"),
            ("total_visits", "integer"),
            ("first_visit_date", "date"),
            ("last_visit_date", "date"),
        ],
    },
}

#: Columns every pipeline relation starts with, from the opportunity list.
LEADING_COLUMNS = [("opportunity_id", "integer"), ("opportunity_name", "text"), ("llo", "text")]


@dataclass(frozen=True)
class PipelineRelation:
    """One pipeline's stored result, as a relation a query may name."""

    name: str
    alias: str
    pipeline_id: int
    pipeline_name: str
    stage: str
    config_hash: str
    #: The pipeline's field names, in its own order, for the columns' order.
    field_names: tuple[str, ...]

    @property
    def table(self) -> str:
        return STAGES[self.stage]["table"]

    @property
    def fields_column(self) -> str:
        return STAGES[self.stage]["fields_column"]

    @property
    def stage_columns(self) -> list[tuple[str, str]]:
        """The columns stored for this stage, after the leading opportunity ones."""
        return STAGES[self.stage]["columns"]

    @property
    def base_columns(self) -> list[tuple[str, str]]:
        return LEADING_COLUMNS + self.stage_columns


def relation_name(alias: str | None, pipeline_id: int) -> str:
    """``p_`` + the alias as a plain lower-case identifier, so it needs no quoting."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", (alias or "").lower()).strip("_")
    return f"p_{cleaned or pipeline_id}"


@dataclass(frozen=True)
class WorkflowSelection:
    workflow: dict
    opportunities: list[scope.Opportunity]
    relations: list[PipelineRelation]
    #: Pipelines the workflow reads that get no relation, each with why.
    not_offered: list[dict]


def workflow_selection(
    caller: Caller, definition_id: int, *, opportunity_id: int | None = None, program_id: int | None = None
) -> WorkflowSelection:
    """The workflow, its opportunities (all held, or ScopeError) and its pipelines' relations."""
    from connect_labs.labs.analysis.utils import get_config_hash

    definition, token, owner, opp_ids = read_workflow(
        caller, definition_id, opportunity_id=opportunity_id, program_id=program_id
    )
    if not opp_ids:
        raise scope.ScopeError("no_opportunities", "this workflow names no opportunities")
    opps = scope.resolve(caller, opp_ids)

    relations: list[PipelineRelation] = []
    not_offered: list[dict] = []
    taken: set[str] = {"visits"}
    for source in definition.pipeline_sources or []:
        if not isinstance(source, dict) or source.get("pipeline_id") is None:
            continue
        entry, config = read_pipeline(source, token, owner)
        label = {"alias": entry.get("alias"), "pipeline_id": entry["pipeline_id"], "name": entry.get("name", "")}
        if config is None:
            not_offered.append({**label, "reason": entry.get("error") or "not readable"})
            continue
        if not entry.get("visits_source"):
            not_offered.append(
                {**label, "reason": "computed from another source (not Connect visits), so it is not offered here"}
            )
            continue
        stage = getattr(config.terminal_stage, "value", config.terminal_stage)
        if stage not in STAGES:
            not_offered.append({**label, "reason": f"stage {stage!r} is not stored where labs data can read it"})
            continue
        name = relation_name(entry.get("alias"), entry["pipeline_id"])
        while name in taken:
            name = f"{name}_{entry['pipeline_id']}"
        taken.add(name)
        relations.append(
            PipelineRelation(
                name=name,
                alias=entry.get("alias") or "",
                pipeline_id=int(entry["pipeline_id"]),
                pipeline_name=entry.get("name") or "",
                stage=stage,
                config_hash=get_config_hash(config),
                field_names=tuple(f.name for f in config.fields) + tuple(h.name for h in config.histograms),
            )
        )
    return WorkflowSelection(
        workflow={
            "id": definition.id,
            "name": definition.name,
            "scope": {k: v for k, v in owner.items()},
        },
        opportunities=opps,
        relations=relations,
        not_offered=not_offered,
    )
