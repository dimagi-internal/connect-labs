"""Workflows over a CommCare project: where they are stored and who may open them.

A project's workflow records (definitions, render code, pipeline definitions) are labs
``LabsLocalRecord`` rows scoped by the project's cache scope id (``scope.hq_scope_id``), exactly
as a labs-only opportunity's are, so the workflow engine reads and writes them unchanged. Nothing
is asked of Connect: there is no Connect token for a person who reaches labs only through CommCare.

``project_dao`` is the ONLY door. The local backend itself checks nothing but that the scope
exists (it is the tenant boundary), so a data access object over a project is built here, after
``require_project``, and ``BaseDataAccess`` refuses a project scope that did not come through
here. Never build one from ``labs_context``, a URL or a tool argument.
"""

from __future__ import annotations

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
    return cls(opportunity_id=hq_scope_id(project.pk), user=user, hq_access_user=user)


def workflow_dao(user, project: HQProject) -> WorkflowDataAccess:
    return project_dao(user, project, WorkflowDataAccess)


def pipeline_dao(user, project: HQProject) -> PipelineDataAccess:
    return project_dao(user, project, PipelineDataAccess)


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
DASHBOARD_RENDER_CODE = r"""function WorkflowUI({ definition, pipelines }) {
  var data = (pipelines || {}).workers || {};
  var rows = (data.rows || []).slice().sort(function (a, b) { return (b.forms || 0) - (a.forms || 0); });
  var total = rows.reduce(function (n, r) { return n + (r.forms || 0); }, 0);
  var top = rows.length ? rows[0].forms || 1 : 1;
  var error = data.metadata && data.metadata.per_opp && Object.values(data.metadata.per_opp).map(function (m) { return m.error; }).filter(Boolean)[0];

  function tile(label, value) {
    return (
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <div className="text-xs text-gray-600 uppercase tracking-wide">{label}</div>
        <div className="text-3xl font-bold text-brand-indigo mt-1">{value}</div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold text-brand-indigo mb-1">{(definition && definition.name) || 'Project overview'}</h1>
      <p className="text-sm text-gray-600 mb-6">Forms submitted per worker, from the forms this project keeps in labs.</p>
      {error ? <div className="mb-4 rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800">{error}</div> : null}
      <div className="grid grid-cols-2 gap-4 mb-6">
        {tile('Forms', total.toLocaleString())}
        {tile('Workers', rows.length.toLocaleString())}
      </div>
      {rows.length === 0 ? (
        <div className="rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm text-yellow-800">
          No forms stored yet. Sync the project, then reload this page.
        </div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg divide-y divide-gray-100">
          {rows.map(function (r) {
            return (
              <div key={r.username} className="flex items-center gap-3 px-4 py-2">
                <div className="w-48 truncate text-sm text-gray-900">{r.username || '(no username)'}</div>
                <div className="flex-1 bg-gray-100 rounded h-3">
                  <div className="bg-brand-indigo h-3 rounded" style={{ width: Math.max(2, Math.round(((r.forms || 0) / top) * 100)) + '%' }}></div>
                </div>
                <div className="w-16 text-right text-sm text-gray-700">{(r.forms || 0).toLocaleString()}</div>
                <div className="w-28 text-right text-xs text-gray-500">{r.last_visit_date || ''}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
"""


def ensure_project_dashboard(user, project: HQProject) -> int:
    """Make the project's overview workflow (and its pipeline) if it has none yet. Returns the workflow id.

    A page-kind definition with repo-owned render code and one pipeline over the project's forms.
    Idempotent by name, so opening a project twice never makes two.
    """
    workflows, pipelines = workflow_dao(user, project), pipeline_dao(user, project)
    try:
        for existing in workflows.list_definitions():
            if existing.name == DASHBOARD_NAME:
                return existing.id
        pipeline = next((p for p in pipelines.list_definitions() if p.name == DASHBOARD_PIPELINE_NAME), None)
        if pipeline is None:
            pipeline = pipelines.create_definition(
                DASHBOARD_PIPELINE_NAME,
                "Forms per worker, from this project's stored forms.",
                dashboard_pipeline_schema(project),
            )
        definition = workflows.create_definition(
            DASHBOARD_NAME,
            "Forms per worker over this CommCare project.",
            kind="page",
            statuses=[],
            templateType="hq_project_overview",
            config={
                "templateType": "hq_project_overview",
                "showFilters": False,
                "showSummaryCards": False,
            },
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
