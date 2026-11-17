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

import re
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
#: Marks the overview labs maintains, so renaming or re-sourcing it cannot make labs lose track of it.
DASHBOARD_SYSTEM_KEY = "hq_project_overview"


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
            existing = next(
                (
                    d
                    for d in workflows.list_definitions()
                    if (d.data or {}).get("system") == DASHBOARD_SYSTEM_KEY
                    # made before the marker existed: labs' own (no author) and still named for it
                    or (d.name == DASHBOARD_NAME and not (d.data or {}).get("created_by"))
                ),
                None,
            )
            if existing is not None:
                wanted = {
                    **existing.data,
                    "name": DASHBOARD_NAME,
                    "system": DASHBOARD_SYSTEM_KEY,
                    "pipeline_sources": [{"pipeline_id": pipeline.id, "alias": "workers"}],
                    "config": {"templateType": "hq_project_overview", "showFilters": False, "showSummaryCards": False},
                }
                if wanted != existing.data:
                    workflows.update_definition(existing.id, wanted)
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
            workflows.update_definition(definition.id, {**definition.data, "system": DASHBOARD_SYSTEM_KEY})
            workflows.save_render_code(definition.id, DASHBOARD_RENDER_CODE)
            return definition.id
    finally:
        workflows.close()
        pipelines.close()


def _source_alias(source: dict) -> str:
    return source.get("alias") or f"pipeline_{source.get('hq_pipeline_id') or source.get('pipeline_id')}"


def _hq_pipeline_entry(user, project: HQProject, source: dict) -> dict:
    """One ``pipelines`` entry for a source naming a saved ``commcare_pipeline_*`` pipeline of this project."""
    from connect_labs.hqdata.pipelines import PipelineProblem, config_for, pipeline_rows
    from connect_labs.hqdata.stores import StoreUnavailable
    from connect_labs.workflow.pipeline_reads import _alias_metadata

    scope = hq_scope_id(project.pk)
    pipeline_id = source.get("hq_pipeline_id")
    pipeline = project.pipelines.filter(pk=pipeline_id).first() if isinstance(pipeline_id, int) else None
    if pipeline is None:
        error = f"Pipeline {pipeline_id!r} is not in this project."
        return {
            "rows": [],
            "metadata": {
                "pipeline_id": pipeline_id,
                "pipeline_name": None,
                "row_count": 0,
                "opportunity_ids": [scope],
                "per_opp": {str(scope): {"row_count": 0, "from_cache": False, "error": error}},
            },
        }
    error = None
    rows: list[dict] = []
    try:
        rows = pipeline_rows(user, pipeline)
    except (PipelineProblem, StoreUnavailable) as exc:
        error = str(exc)
    per_opp = {str(scope): {"row_count": len(rows), "from_cache": False, **({"error": error} if error else {})}}
    try:
        metadata = _alias_metadata(
            pipeline_id=pipeline.pk,
            name=pipeline.name,
            rows=rows,
            opp_ids=[scope],
            per_opp=per_opp,
            config=config_for(pipeline),
        )
    except PipelineProblem as exc:
        # One source that cannot run shows its own error; it never takes the page's other sources down with it.
        per_opp[str(scope)]["error"] = per_opp[str(scope)].get("error") or str(exc)
        metadata = {
            "pipeline_id": pipeline.pk,
            "pipeline_name": pipeline.name,
            "row_count": len(rows),
            "opportunity_ids": [scope],
            "per_opp": per_opp,
        }
    return {"rows": rows, "metadata": metadata}


def read_project_pipeline_data(user, project: HQProject, definition_id: int) -> dict:
    """The ``pipelines`` prop of one of this project's workflows: ``{alias: {"rows", "metadata"}}``.

    A source names either a saved project pipeline (``{"hq_pipeline_id", "alias"}``: what the
    ``commcare_pipeline_*`` tools manage) or a pipeline record in the project's workflow store
    (``{"pipeline_id", "alias"}``: what the overview uses). Computed inline from the project's stored rows
    (they are labs' own, so there is no download to wait on). Every read passes the project gate again,
    inside the pipeline, as this member.
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
            if not isinstance(source, dict):
                continue
            alias = _source_alias(source)
            if source.get("hq_pipeline_id") is not None:
                out[alias] = _hq_pipeline_entry(user, project, source)
                continue
            pipeline_id = source.get("pipeline_id")
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


# -- authoring a project's pages ---------------------------------------------------------------------

#: The runner flags a page may set. Anything else (``sandbox``, agent sharing...) is not an author's to choose.
PAGE_CONFIG_FLAGS = frozenset({"showFilters", "showSummaryCards", "renderWhileLoading"})
MAX_PAGE_SOURCES = 20
MAX_RENDER_CODE_BYTES = 300_000
_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")


class PageProblem(ValueError):
    """What is wrong with a page being made or changed, written for the person (or AI) who sent it."""


class PageNotFound(LookupError):
    """No such page in this project. Its own type so a stray KeyError is never mistaken for it."""


def clean_sources(project: HQProject, sources) -> list[dict]:
    """``[{"hq_pipeline_id", "alias"}]`` for pipelines saved in THIS project, or ``PageProblem``.

    A source names a saved ``commcare_pipeline_*`` pipeline by id and the alias the page reads it under
    (``pipelines.<alias>.rows``). Nothing else is accepted, so a page cannot point at another project's pipeline,
    a Connect source, or anything the engine would fetch on its own.
    """
    if sources is None:
        return []
    if not isinstance(sources, list) or len(sources) > MAX_PAGE_SOURCES:
        raise PageProblem(f"pipeline_sources is a list of at most {MAX_PAGE_SOURCES} entries.")
    out, seen = [], set()
    for entry in sources:
        if not isinstance(entry, dict) or set(entry) - {"hq_pipeline_id", "alias"}:
            raise PageProblem('Each pipeline source is {"hq_pipeline_id": <id>, "alias": "<name>"} and nothing else.')
        pipeline_id, alias = entry.get("hq_pipeline_id"), entry.get("alias")
        if not isinstance(pipeline_id, int) or isinstance(pipeline_id, bool):
            raise PageProblem("hq_pipeline_id must be the id of a pipeline saved in this project.")
        if not isinstance(alias, str) or not _ALIAS.match(alias):
            raise PageProblem("alias starts with a letter and is letters, digits and underscores (at most 41).")
        if alias in seen:
            raise PageProblem(f"alias {alias!r} is used twice.")
        if not project.pipelines.filter(pk=pipeline_id).exists():
            raise PageProblem(f"Pipeline {pipeline_id} is not saved in this project.")
        seen.add(alias)
        out.append({"hq_pipeline_id": pipeline_id, "alias": alias})
    return out


def clean_config(config) -> dict:
    if config is None:
        return {"showFilters": False, "showSummaryCards": False}
    if not isinstance(config, dict):
        raise PageProblem("config is an object.")
    unknown = sorted(set(config) - PAGE_CONFIG_FLAGS)
    if unknown or not all(isinstance(v, bool) for v in config.values()):
        raise PageProblem(
            f"config takes only true/false for {sorted(PAGE_CONFIG_FLAGS)}; not {unknown or 'non-booleans'}."
        )
    return dict(config)


def _check_render_code(render_code) -> str:
    from connect_labs.workflow.render_code_lint import render_code_problem

    if not isinstance(render_code, str):
        raise PageProblem("render_code is the JSX text of a function WorkflowUI({...}).")
    try:
        size = len(render_code.encode("utf-8"))
    except UnicodeEncodeError:
        raise PageProblem("render_code is not valid text.") from None
    if size > MAX_RENDER_CODE_BYTES:
        raise PageProblem("render_code is too large.")
    problem = render_code_problem(render_code)
    if problem:
        raise PageProblem(problem)
    return render_code


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "page"


def create_page(
    user, project: HQProject, *, name, description="", render_code=None, pipeline_sources=None, config=None
):
    """Make a page over this project for a member. Always the page kind: sandboxed, read-only, no runs."""
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name or len(name) > 200:
        raise PageProblem("A page needs a name (at most 200 characters).")
    sources, flags = clean_sources(project, pipeline_sources), clean_config(config)
    code = _check_render_code(render_code) if render_code is not None else None
    dao = workflow_dao(user, project)
    try:
        with transaction.atomic():
            definition = dao.create_definition(
                name,
                str(description or "")[:2000],
                kind="page",
                page={"slug": _slug(name)},
                statuses=[],
                config=flags,
                pipeline_sources=sources,
                opportunity_ids=[],
            )
            dao.update_definition(definition.id, {**definition.data, "created_by": user.pk})
            if code is not None:
                dao.save_render_code(definition.id, code)
            return definition
    finally:
        dao.close()


def update_page(user, project: HQProject, workflow_id: int, **changes):
    """Change a page's name, description, pipeline sources, flags or render code. ``None`` leaves a field alone.

    Serialised per page and re-read under the lock, so two people changing different fields of one page both
    land. ``config`` is merged onto what the page has, not substituted for it.
    """
    from django.db import connection

    unknown = sorted(set(changes) - {"name", "description", "render_code", "pipeline_sources", "config"})
    if unknown:
        raise PageProblem(f"Cannot change {unknown}.")
    if load_page(user, project, workflow_id) is None:
        raise PageNotFound("No such page in this project.")
    # Validate everything before touching anything.
    name = None
    if changes.get("name") is not None:
        name = changes["name"].strip() if isinstance(changes["name"], str) else ""
        if not name or len(name) > 200:
            raise PageProblem("A page needs a name (at most 200 characters).")
    sources = (
        clean_sources(project, changes["pipeline_sources"]) if changes.get("pipeline_sources") is not None else None
    )
    flags = clean_config(changes["config"]) if changes.get("config") is not None else None
    code = _check_render_code(changes["render_code"]) if changes.get("render_code") is not None else None
    dao = workflow_dao(user, project)
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [74_002, int(workflow_id)])
            definition = dao.get_definition(int(workflow_id))
            if definition is None:
                raise PageNotFound("No such page in this project.")
            data = dict(definition.data)
            if data.get("system"):
                raise PageProblem(
                    "The project overview is kept up to date by labs. Make a page of your own to change it."
                )
            if name is not None:
                data["name"] = name
            if changes.get("description") is not None:
                data["description"] = str(changes["description"])[:2000]
            if sources is not None:
                data["pipeline_sources"] = sources
            if flags is not None:
                data["config"] = {**(data.get("config") or {}), **flags}
            dao.update_definition(definition.id, data)
            if code is not None:
                stored = dao.get_render_code(definition.id)
                dao.save_render_code(definition.id, code, version=(stored.version + 1) if stored else 1)
    finally:
        dao.close()
    return int(workflow_id)


def delete_page(user, project: HQProject, workflow_id: int, *, is_admin: bool) -> None:
    """Delete a page: its author or a project admin. A backup of its definition and code is kept first."""
    loaded = load_page(user, project, workflow_id)
    if loaded is None:
        raise PageNotFound("No such page in this project.")
    definition, _ = loaded
    author = (definition.data or {}).get("created_by")
    if not is_admin and author != user.pk:
        raise PermissionError("Only the page's author or a project admin can delete it.")
    dao = workflow_dao(user, project)
    try:
        dao.delete_definition(definition.id)
    finally:
        dao.close()
