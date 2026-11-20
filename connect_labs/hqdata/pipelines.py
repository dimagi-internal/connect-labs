"""Run a saved pipeline over a CommCare project, as a person who belongs to it."""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.hqdata.access import require_project

MAX_ROWS_SHOWN = 500


class PipelineProblem(ValueError):
    """The schema cannot run. The message is written for the person who wrote it."""


@dataclass(frozen=True)
class SavedPipeline:
    """A saved pipeline as a person sees it. Stored as the engine's own pipeline record under the project's scope;
    what it reads from (forms or cases, and any narrowing) is its schema's ``data_source``."""

    id: int
    name: str
    store_kind: str
    app_id: str
    xmlns: str
    case_type: str
    schema: dict
    created_by: int | None


def data_source_for(project_id: int, store_kind: str, app_id="", xmlns="", case_type="") -> dict:
    """Where a pipeline reads from: its own project, and nothing a schema says."""
    source = {"type": "hq_store", "project_id": project_id, "store_kind": store_kind}
    if store_kind == "forms":
        source.update(app_id=app_id, xmlns=xmlns)
    else:
        source.update(case_type=case_type)
    return source


def stamped_schema(project, store_kind: str, schema: dict, app_id="", xmlns="", case_type="") -> dict:
    """``schema`` with its ``data_source`` set to this project's, whatever the caller's schema said."""
    return {**schema, "data_source": data_source_for(project.pk, store_kind, app_id, xmlns, case_type)}


def describe(record) -> SavedPipeline:
    source = (record.schema or {}).get("data_source") or {}
    return SavedPipeline(
        id=record.id,
        name=record.name,
        store_kind=source.get("store_kind", ""),
        app_id=source.get("app_id", ""),
        xmlns=source.get("xmlns", ""),
        case_type=source.get("case_type", ""),
        schema={k: v for k, v in (record.schema or {}).items() if k != "data_source"},
        created_by=(record.data or {}).get("created_by"),
    )


def config_for(schema: dict, pipeline_id: int = 0):
    """The runnable config for a stamped schema. Raises PipelineProblem if it cannot run."""
    from connect_labs.workflow.data_access import PipelineDataAccess

    if not isinstance(schema, dict) or not schema.get("fields"):
        raise PipelineProblem("A pipeline needs a list of fields to compute.")
    if schema.get("joins"):
        raise PipelineProblem("Pipelines over a CommCare project cannot join other pipelines yet.")
    try:
        # `_schema_to_config` never reads `self` (checked with ast); passing None avoids building a
        # PipelineDataAccess, which needs a Connect token, for a conversion that does not use one.
        return PipelineDataAccess._schema_to_config(None, schema, pipeline_id)
    except PipelineProblem:
        raise
    except Exception as exc:  # a bad transform, an unknown aggregation...
        raise PipelineProblem(str(exc) or type(exc).__name__) from exc


def _dao(user, project):
    from connect_labs.hqdata.workflows import pipeline_dao

    return pipeline_dao(user, project)


def list_pipelines(user, project) -> list[SavedPipeline]:
    dao = _dao(user, project)
    try:
        return sorted((describe(r) for r in dao.list_definitions()), key=lambda p: p.name.lower())
    finally:
        dao.close()


def find_pipeline(user, project, pipeline_id=None, name=None):
    """The engine record of one of this project's pipelines by id or name, or None."""
    dao = _dao(user, project)
    try:
        if isinstance(pipeline_id, int) and not isinstance(pipeline_id, bool):
            return dao.get_definition(pipeline_id)
        return next((r for r in dao.list_definitions() if r.name == (name or "")), None)
    finally:
        dao.close()


def save_pipeline(
    user,
    project,
    *,
    name,
    store_kind,
    schema,
    app_id="",
    xmlns="",
    case_type="",
    pipeline_id=None,
    must_be_new=False,
):
    """Create a pipeline, or replace the one with this name (or ``pipeline_id``). Returns the engine record.

    Validated before anything is written. The name is unique within the project, serialised so two members saving
    the same name never make two.
    """
    from django.db import connection, transaction

    name = (name or "").strip() if isinstance(name, str) else ""
    if not name or len(name) > 120:
        raise PipelineProblem("Give the pipeline a name (at most 120 characters).")
    if store_kind not in ("forms", "cases"):
        raise PipelineProblem("Choose forms or cases.")
    if not isinstance(schema, dict):
        raise PipelineProblem("The schema must be an object.")
    if store_kind == "forms":
        case_type = ""
    else:
        app_id = xmlns = ""
    stamped = stamped_schema(project, store_kind, schema, app_id, xmlns, case_type)
    config_for(stamped)
    dao = _dao(user, project)
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [74_003, project.pk])
            existing = dao.list_definitions()
            target = next((r for r in existing if r.id == pipeline_id), None) if pipeline_id else None
            if pipeline_id and target is None:
                raise PipelineProblem("No such pipeline in this project.")
            same_name = next((r for r in existing if r.name == name and (target is None or r.id != target.id)), None)
            if same_name is not None and (target is not None or must_be_new):
                raise PipelineProblem("A pipeline with that name already exists.")
            target = target or same_name
            if target is None:
                return dao.create_definition(name, "", stamped, created_by=user.pk)
            return dao.update_definition(target.id, name=name, schema=stamped)
    finally:
        dao.close()


def delete_pipeline(user, project, record, *, is_admin: bool) -> None:
    """Delete a pipeline: the person who saved it, or a project admin."""
    if not is_admin and (record.data or {}).get("created_by") != user.pk:
        raise PermissionError("Only the person who saved it, or a project admin, can delete it.")
    dao = _dao(user, project)
    try:
        dao.delete_definition(record.id)
    finally:
        dao.close()


def rows_for_schema(
    user, project, schema: dict, *, pipeline_id: int = 0, force_refresh: bool = False, window=None
) -> list[dict]:
    """EVERY row a pipeline schema computes over this project's stored rows, as the person.

    The one function that runs a project pipeline, whether it is a saved one (``pipeline_rows``) or a schema a
    report builds on the fly (the campaign coverage page). Whatever the schema's ``data_source`` says, it is
    stamped to this project (and the forms/cases narrowing it carries is kept), so a schema cannot read another
    project; ``require_project`` is checked here and again inside the pipeline, on the read itself.

    ``window`` is a half-open ``[start, end)`` visit-date span. It applies only to a schema that sets
    ``period_scoped`` (and groups to one row per worker): that pipeline is re-aggregated over the window, exactly
    as the workflow engine does for a saved run; any other returns all its rows.
    """
    from connect_labs.hqdata.stores import store_for
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.workflow.data_access import serialize_pipeline_row

    require_project(user, project)
    source = (schema or {}).get("data_source") or {}
    stamped = stamped_schema(
        project,
        source.get("store_kind", "forms"),
        schema,
        source.get("app_id", ""),
        source.get("xmlns", ""),
        source.get("case_type", ""),
    )
    config = config_for(stamped, pipeline_id)
    scoped = bool(window) and bool(stamped.get("period_scoped"))
    # The session closes whatever connection into a customer's database the run opened.
    with store_for(project).session():
        analysis = AnalysisPipeline(None, local_only_user=user)
        result = analysis.stream_analysis_ignore_events(
            config, opportunity_id=project.scope_id, force_refresh=force_refresh
        )
        if scoped:
            config.date_from, config.date_to = window
            windowed = analysis.get_period_scoped_result_only(config, project.scope_id)
            if windowed is None:
                raise PipelineProblem(
                    "This pipeline cannot be limited to a period (only a pipeline that groups to one row per "
                    "worker can)."
                )
            result = windowed
    return [serialize_pipeline_row(r) for r in result.rows]


def pipeline_rows(
    user, project, record, *, force_refresh: bool = False, window: tuple[str, str] | None = None
) -> list[dict]:
    """EVERY row of a saved pipeline, as the person (``rows_for_schema`` over its stored schema). The ``rows`` of
    ``run_pipeline`` are capped for display; a workflow draws from all of them."""
    return rows_for_schema(
        user, project, record.schema, pipeline_id=record.id, force_refresh=force_refresh, window=window
    )


def run_pipeline(user, project, record, *, force_refresh: bool = False) -> dict:
    """Run it and return ``{"rows": [...], "table": [[cell...]...], "count": n, "truncated": bool, "columns": [...]}``.

    Rows are capped at ``MAX_ROWS_SHOWN`` for display (``count`` is the true total).
    """
    rows = pipeline_rows(user, project, record, force_refresh=force_refresh)
    columns: list[str] = []
    for row in rows[:MAX_ROWS_SHOWN]:
        for key in row:
            if key not in columns:
                columns.append(key)
    shown = rows[:MAX_ROWS_SHOWN]
    return {
        "rows": shown,
        "table": [[row.get(c) for c in columns] for row in shown],
        "count": len(rows),
        "truncated": len(rows) > MAX_ROWS_SHOWN,
        "columns": columns,
    }
