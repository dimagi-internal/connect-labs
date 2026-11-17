"""Workflows over a CommCare project: where they are stored and who may open them.

A project's workflow records (definitions, render code, pipeline definitions) are labs
``LabsLocalRecord`` rows scoped by the project's cache scope id (``scope.hq_scope_id``), exactly
as a labs-only opportunity's are, so the workflow engine reads and writes them unchanged. Nothing
is asked of Connect: there is no Connect token for a person who reaches labs only through CommCare.

The local backend itself checks nothing (the scope is the tenant boundary), so membership is checked
where a data access object is built: ``BaseDataAccess`` refuses a project scope for anyone who is not
a verified member right now. ``project_dao`` is the front door (it also refuses a project that keeps
its data in a customer's database). Never build one from ``labs_context`` or a URL.
"""

from __future__ import annotations

from pathlib import Path

from django.db import transaction

from connect_labs.hqdata.access import require_project
from connect_labs.hqdata.models import HQProject
from connect_labs.hqdata.scope import hq_scope_id
from connect_labs.workflow.data_access import PipelineDataAccess, WorkflowDataAccess


class ProjectWorkflowsUnavailable(Exception):
    """This project cannot hold workflows (yet): said plainly rather than failing in the engine."""


def _refuse_external(project: HQProject) -> None:
    # A project that chose a customer's database promises nothing computed from it stays in labs
    # (hqdata/pipeline_source.py). Definitions hold no data, but run snapshots do, and the engine
    # has no customer-database path for them yet.
    if project.store == HQProject.STORE_EXTERNAL:
        raise ProjectWorkflowsUnavailable(
            "Workflows are not available for a project that keeps its data in your own database yet."
        )


def project_dao(user, project: HQProject, cls=WorkflowDataAccess):
    """A data access object over this project's workflow records, for a verified member only."""
    require_project(user, project)
    _refuse_external(project)
    return cls(opportunity_id=hq_scope_id(project.pk), user=user)


def workflow_dao(user, project: HQProject) -> WorkflowDataAccess:
    return project_dao(user, project, WorkflowDataAccess)


def pipeline_dao(user, project: HQProject) -> PipelineDataAccess:
    return project_dao(user, project, PipelineDataAccess)


def load_page(user, project: HQProject, definition_id: int):
    """``(definition, render_code)`` of one of the project's pages for a verified member, else None.

    Only a page (no runs) is ever served over a project. The definition is read in the project's own
    scope, so an id that belongs to another project is simply not found.
    """
    from connect_labs.workflow.page_mode import is_page

    dao = workflow_dao(user, project)
    try:
        definition = dao.get_definition(int(definition_id))
        if definition is None or not is_page(definition):
            return None
        record = dao.get_render_code(definition.id)
        return definition, ((record.data or {}).get("component_code") if record else "") or ""
    finally:
        dao.close()


# -- the project dashboard ---------------------------------------------------------------------------

DASHBOARD_PIPELINE_NAME = "Forms per worker"
DASHBOARD_NAME = "Project overview"


def dashboard_pipeline_schema(project: HQProject) -> dict:
    """Forms per worker, read from the project's stored forms.

    The data source names the project because a schema must validate on its own; whatever a stored
    schema says, a read through ``project_dao`` is pinned to the scope it was opened for.
    """
    return {
        "grouping_key": "username",
        "terminal_stage": "aggregated",
        "data_source": {"type": "hq_store", "project_id": project.pk, "store_kind": "forms"},
        "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}],
    }


#: Drawn in the page sandbox (workflow/sandbox.py): this code is data to the frame, not code the host runs.
DASHBOARD_RENDER_CODE = (Path(__file__).with_name("project_overview.jsx")).read_text()


def ensure_project_dashboard(user, project: HQProject) -> int:
    """Make the project's overview workflow (and its pipeline) if it has none, else bring it up to date.

    A page-kind definition with repo-owned render code and one pipeline over the project's forms.
    Idempotent by name and serialised per project, so a double click or two members opening it at once
    never make two. The render code and schema are rewritten when the repo's differ, so a fix to the
    overview reaches projects that already have one.
    """
    from django.db import connection

    workflows, pipelines = workflow_dao(user, project), pipeline_dao(user, project)
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [74_001, project.pk])
            schema = dashboard_pipeline_schema(project)
            pipeline = next((p for p in pipelines.list_definitions() if p.name == DASHBOARD_PIPELINE_NAME), None)
            if pipeline is None:
                pipeline = pipelines.create_definition(
                    DASHBOARD_PIPELINE_NAME, "Forms per worker, from this project's stored forms.", schema
                )
            elif pipeline.schema != schema:
                pipelines.update_definition(pipeline.id, schema=schema)
            existing = next((d for d in workflows.list_definitions() if d.name == DASHBOARD_NAME), None)
            if existing is not None:
                stored = workflows.get_render_code(existing.id)
                if stored is None or (stored.data or {}).get("component_code") != DASHBOARD_RENDER_CODE:
                    workflows.save_render_code(existing.id, DASHBOARD_RENDER_CODE)
                return existing.id
            definition = workflows.create_definition(
                DASHBOARD_NAME,
                "Forms per worker over this CommCare project.",
                kind="page",
                statuses=[],
                templateType="hq_project_overview",
                config={"templateType": "hq_project_overview", "showFilters": False, "showSummaryCards": False},
                pipeline_sources=[{"pipeline_id": pipeline.id, "alias": "workers"}],
                opportunity_ids=[],
            )
            workflows.save_render_code(definition.id, DASHBOARD_RENDER_CODE)
            return definition.id
    finally:
        workflows.close()
        pipelines.close()


def read_project_pipeline_data(user, project: HQProject, definition_id: int) -> dict:
    """The ``pipelines`` prop of one of this project's workflows: ``{alias: {"rows", "metadata"}}``.

    Computed inline from the project's stored rows (they are labs' own, so there is no download to
    wait on). Every read passes the project gate again, inside the pipeline, as this member.
    """
    from connect_labs.workflow.pipeline_reads import _alias_metadata

    scope = hq_scope_id(project.pk)
    workflows, pipelines = workflow_dao(user, project), pipeline_dao(user, project)
    try:
        definition = workflows.get_definition(definition_id)
        if definition is None:
            return {}
        out: dict[str, dict] = {}
        for source in definition.pipeline_sources or []:
            pipeline_id, alias = (
                source.get("pipeline_id"),
                source.get("alias") or f"pipeline_{source.get('pipeline_id')}",
            )
            record = pipelines.get_definition(pipeline_id) if pipeline_id else None
            if record is None:
                continue
            result = pipelines.execute_pipeline(pipeline_id, scope)
            rows = result.get("rows") or []
            error = (result.get("metadata") or {}).get("error")
            config = pipelines._schema_to_config(record.schema, pipeline_id)
            per_opp = {
                str(scope): {"row_count": len(rows), "from_cache": False, **({"error": error} if error else {})}
            }
            out[alias] = {
                "rows": rows,
                "metadata": _alias_metadata(
                    pipeline_id=pipeline_id,
                    name=record.name,
                    rows=rows,
                    opp_ids=[scope],
                    per_opp=per_opp,
                    config=config,
                ),
            }
        return out
    finally:
        workflows.close()
        pipelines.close()
