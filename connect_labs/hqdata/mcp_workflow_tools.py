"""The ``commcare_workflow_*`` MCP tools: write and change the workflow pages over a CommCare project.

A page is a workflow of the page kind (``workflow/page_mode.py``): render code drawn by the standard workflow
runner in a sandbox, reading pipelines saved in the project (``commcare_pipeline_save``), read-only, no runs. These
tools
act as the signed-in person and pass the same gate as everything in ``hqdata``: a verified member of the project; a
project the person is not in is "not found". A ``commcare``-scope token reaches them
(``token_scopes.COMMCARE_TOOLS``; a test pins that the registered ``commcare_*`` tools and that list are the same).
"""

from __future__ import annotations

from django.conf import settings

from connect_labs.audit_trail.models import Action
from connect_labs.hqdata.audit import project_event
from connect_labs.hqdata.mcp_tools import _PROJECT_ID, _project
from connect_labs.hqdata.models import HQProject, HQProjectMember
from connect_labs.hqdata.pipelines import PipelineProblem
from connect_labs.hqdata.workflows import (
    PageNotFound,
    PageProblem,
    ProjectWorkflowsUnavailable,
    create_page,
    delete_page,
    load_page,
    read_project_pipeline_data,
    update_page,
    workflow_dao,
)
from connect_labs.mcp.tool_registry import MCPToolError, register
from connect_labs.workflow.page_mode import is_page

_WORKFLOW_ID = {"type": "integer", "description": "The page's id (from commcare_workflow_list)."}


def _url(project: HQProject, workflow_id: int) -> str:
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return f"{base}/commcare/projects/{project.pk}/workflows/{workflow_id}/"


def _guard(call):
    """Run ``call``; say a refusal in the tool's own vocabulary."""
    try:
        return call()
    except PageProblem as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    except PageNotFound as exc:
        raise MCPToolError("NOT_FOUND", str(exc))
    except PipelineProblem as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))
    except PermissionError as exc:
        raise MCPToolError("PERMISSION_DENIED", str(exc))
    except ProjectWorkflowsUnavailable as exc:
        raise MCPToolError("UPSTREAM_ERROR", str(exc))


def _summary(project: HQProject, definition) -> dict:
    data = definition.data or {}
    return {
        "id": definition.id,
        "name": data.get("name"),
        "description": data.get("description", ""),
        "pipeline_sources": data.get("pipeline_sources") or [],
        "url": _url(project, definition.id),
    }


@register(
    name="commcare_workflow_list",
    description="The workflow pages in a CommCare project, each with its address.",
    input_schema={"type": "object", "properties": {"project_id": _PROJECT_ID}, "required": ["project_id"]},
)
def commcare_workflow_list(user, project_id=None, **_):
    project = _project(user, project_id)

    def run():
        dao = workflow_dao(user, project)
        try:
            return {"workflows": [_summary(project, d) for d in dao.list_definitions() if is_page(d)]}
        finally:
            dao.close()

    return _guard(run)


@register(
    name="commcare_workflow_get",
    description="One workflow page: its description, pipeline sources, flags and its render code (JSX).",
    input_schema={
        "type": "object",
        "properties": {"project_id": _PROJECT_ID, "workflow_id": _WORKFLOW_ID},
        "required": ["project_id", "workflow_id"],
    },
)
def commcare_workflow_get(user, project_id=None, workflow_id=None, **_):
    project = _project(user, project_id)
    if not isinstance(workflow_id, int):
        raise MCPToolError("INVALID_SCHEMA", "workflow_id is the page's id.")
    loaded = _guard(lambda: load_page(user, project, workflow_id))
    if loaded is None:
        raise MCPToolError("NOT_FOUND", "No such page in this project.")
    definition, render_code = loaded
    return {
        **_summary(project, definition),
        "config": (definition.data or {}).get("config") or {},
        "render_code": render_code,
    }


@register(
    name="commcare_workflow_create",
    description=(
        "Create a workflow page over a CommCare project: render_code (JSX defining function WorkflowUI({definition, "
        "pipelines, scope})) drawn in the standard sandboxed runner, reading pipelines saved with "
        "commcare_pipeline_save. "
        "pipeline_sources is [{commcare_pipeline_id, alias}]; the page reads pipelines.<alias>.rows. Read "
        "commcare_workflow_authoring_guide first. Returns the page's address."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "name": {"type": "string"},
            "description": {"type": "string"},
            "render_code": {"type": "string"},
            "pipeline_sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"commcare_pipeline_id": {"type": "integer"}, "alias": {"type": "string"}},
                    "required": ["commcare_pipeline_id", "alias"],
                },
            },
            "config": {
                "type": "object",
                "description": "true/false flags: showFilters, showSummaryCards, renderWhileLoading.",
            },
        },
        "required": ["project_id", "name"],
    },
    is_write=True,
)
def commcare_workflow_create(
    user, project_id=None, name="", description="", render_code=None, pipeline_sources=None, config=None, **_
):
    project = _project(user, project_id)
    definition = _guard(
        lambda: create_page(
            user,
            project,
            name=name,
            description=description,
            render_code=render_code,
            pipeline_sources=pipeline_sources,
            config=config,
        )
    )
    project_event(Action.CREATE, project.pk, user=user, kind="workflow")
    return {"workflow_id": definition.id, "url": _url(project, definition.id)}


@register(
    name="commcare_workflow_update",
    description=(
        "Change a workflow page. Send only what changes: name, description, pipeline_sources (replaces the list), "
        "config flags, render_code (replaces the JSX). Any member of the project may change a page."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "workflow_id": _WORKFLOW_ID,
            "name": {"type": "string"},
            "description": {"type": "string"},
            "render_code": {"type": "string"},
            "pipeline_sources": {"type": "array", "items": {"type": "object"}},
            "config": {"type": "object"},
        },
        "required": ["project_id", "workflow_id"],
    },
    is_write=True,
)
def commcare_workflow_update(user, project_id=None, workflow_id=None, **changes):
    project = _project(user, project_id)
    if not isinstance(workflow_id, int):
        raise MCPToolError("INVALID_SCHEMA", "workflow_id is the page's id.")
    fields = {"name", "description", "render_code", "pipeline_sources", "config"}
    unknown = sorted(set(changes) - fields)
    if unknown:
        raise MCPToolError("INVALID_SCHEMA", f"commcare_workflow_update changes only {sorted(fields)}, not {unknown}.")
    allowed = dict(changes)
    _guard(lambda: update_page(user, project, workflow_id, **allowed))
    project_event(Action.UPDATE, project.pk, user=user, kind="workflow")
    return {
        "workflow_id": workflow_id,
        "url": _url(project, workflow_id),
        "changed": sorted(k for k, v in allowed.items() if v is not None),
    }


@register(
    name="commcare_workflow_delete",
    description=(
        "Delete a workflow page (a backup of its definition and code is kept). Its author or a project admin only."
    ),
    input_schema={
        "type": "object",
        "properties": {"project_id": _PROJECT_ID, "workflow_id": _WORKFLOW_ID},
        "required": ["project_id", "workflow_id"],
    },
    is_write=True,
)
def commcare_workflow_delete(user, project_id=None, workflow_id=None, **_):
    project = _project(user, project_id)
    if not isinstance(workflow_id, int):
        raise MCPToolError("INVALID_SCHEMA", "workflow_id is the page's id.")
    is_admin = HQProjectMember.objects.filter(project=project, user=user, role=HQProjectMember.ROLE_ADMIN).exists()
    _guard(lambda: delete_page(user, project, workflow_id, is_admin=is_admin))
    project_event(Action.DELETE, project.pk, user=user, kind="workflow")
    return {"deleted": True}


@register(
    name="commcare_workflow_data",
    description=(
        "The data a page receives, exactly as its render code sees it: {alias: {rows, metadata}} for each pipeline "
        "source. Use it to write and debug render code. Rows are capped here for size (the page itself gets all)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project_id": _PROJECT_ID,
            "workflow_id": _WORKFLOW_ID,
            "max_rows": {"type": "integer", "default": 20},
        },
        "required": ["project_id", "workflow_id"],
    },
)
def commcare_workflow_data(user, project_id=None, workflow_id=None, max_rows=20, **_):
    project = _project(user, project_id)
    if not isinstance(workflow_id, int):
        raise MCPToolError("INVALID_SCHEMA", "workflow_id is the page's id.")
    if _guard(lambda: load_page(user, project, workflow_id)) is None:
        raise MCPToolError("NOT_FOUND", "No such page in this project.")
    cap = max(1, min(int(max_rows or 20), 200))
    data = _guard(lambda: read_project_pipeline_data(user, project, workflow_id))
    out = {}
    for alias, entry in data.items():
        rows = entry.get("rows") or []
        out[alias] = {
            "row_count": len(rows),
            "rows": rows[:cap],
            "truncated": len(rows) > cap,
            "metadata": entry.get("metadata"),
        }
    return {"pipelines": out}


GUIDE = """\
Workflow pages over a CommCare project
======================================

A page is a React component drawn by labs' workflow runner inside a sandboxed frame, read-only, for people who
belong to the project. It reads pipelines you save with commcare_pipeline_save.

1. Save a pipeline:   commcare_pipeline_save(project_id, name, store_kind="forms"|"cases", schema={...})
   schema = {"grouping_key": "username", "terminal_stage": "aggregated",
             "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}]}
   Form field paths start with form. (e.g. form.meta.username, form.<question>); case paths start with case.
   Check it with commcare_pipeline_run. Aggregations include count, sum, avg, min, max, first.
   Two shapes (read live on real projects):
   - "aggregated" groups rows by a worker: forms per username work this way.
   - "visit_level" returns ONE ROW PER form or case with the fields you list (plus entity_name = the case's
     name, username, visit_date), and you group it in render code. Use it for cases, which have no worker to
     group by: {"terminal_stage": "visit_level",
                "fields": [{"name": "case_type", "path": "case.case_type", "aggregation": "first"}]}
     then count rows per case_type with a plain JS reduce.
2. Make a page:      commcare_workflow_create(project_id, name, render_code,
                                            pipeline_sources=[{commcare_pipeline_id, alias}])
3. Look at its data: commcare_workflow_data(project_id, workflow_id)   -- exactly what render code receives
4. Change it:        commcare_workflow_update(project_id, workflow_id, render_code=...)

Render code is JSX defining ONE function component:

    function WorkflowUI({ definition, pipelines, scope, config }) {
      const rows = (pipelines.workers || {}).rows || [];
      return (<div className="max-w-4xl"> ... </div>);
    }

- pipelines.<alias>.rows are plain objects (one per group); pipelines.<alias>.metadata has row_count, errors.
- Style with Tailwind utility classes (text-brand-indigo is the accent). React is in scope (React.useState, ...).
  Chart.js is available as window.Chart for charts. Do not fetch(): the frame has no network.
- The page is read-only and sandboxed: no buttons that save, no runs, no links out except plain <a href>.
- A pipeline over a very large project is refused in words by commcare_pipeline_run; narrow it
  (app_id/xmlns/case_type).
- The page's address (returned by create/get/list) is /commcare/projects/<id>/workflows/<id>/; the same page can be
  embedded inside CommCare HQ once HQ is registered as an embed client.
"""


@register(
    name="commcare_workflow_authoring_guide",
    description=(
        "How to write a workflow page over a CommCare project: the pipeline schema, the render contract, an example."
    ),
    input_schema={"type": "object", "properties": {}},
)
def commcare_workflow_authoring_guide(user, **_):  # noqa: ARG001 -- read-only static text
    return {"guide": GUIDE}
