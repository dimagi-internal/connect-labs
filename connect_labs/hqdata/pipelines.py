"""Run a saved pipeline over a CommCare project, as a person who belongs to it."""

from __future__ import annotations

from connect_labs.hqdata.access import require_project
from connect_labs.hqdata.models import HQPipeline

MAX_ROWS_SHOWN = 500


class PipelineProblem(ValueError):
    """The schema cannot run. The message is written for the person who wrote it."""


def data_source_for(pipeline: HQPipeline) -> dict:
    """Where this pipeline reads from: its own project, and nothing a schema says."""
    source = {"type": "hq_store", "project_id": pipeline.project_id, "store_kind": pipeline.store_kind}
    if pipeline.store_kind == "forms":
        source.update(app_id=pipeline.app_id, xmlns=pipeline.xmlns)
    else:
        source.update(case_type=pipeline.case_type)
    return source


def config_for(pipeline: HQPipeline):
    """The runnable config for a saved pipeline. Raises PipelineProblem if it cannot run."""
    from connect_labs.workflow.data_access import PipelineDataAccess

    schema = pipeline.schema
    if not isinstance(schema, dict) or not schema.get("fields"):
        raise PipelineProblem("A pipeline needs a list of fields to compute.")
    if schema.get("joins"):
        raise PipelineProblem("Pipelines over a CommCare project cannot join other pipelines yet.")
    stamped = {**schema, "data_source": data_source_for(pipeline)}
    try:
        # `_schema_to_config` never reads `self` (checked with ast); passing None avoids building a
        # PipelineDataAccess, which needs a Connect token, for a conversion that does not use one.
        return PipelineDataAccess._schema_to_config(None, stamped, pipeline.pk or 0)
    except PipelineProblem:
        raise
    except Exception as exc:  # a bad transform, an unknown aggregation...
        raise PipelineProblem(str(exc) or type(exc).__name__) from exc


def pipeline_rows(
    user, pipeline: HQPipeline, *, force_refresh: bool = False, window: tuple[str, str] | None = None
) -> list[dict]:
    """EVERY row of a saved pipeline, as the person. The ``rows`` of ``run_pipeline`` are capped for display;
    a workflow draws from all of them.

    ``window`` is a half-open ``[start, end)`` visit-date span. It applies only to a pipeline whose schema
    sets ``period_scoped`` (and groups to one row per worker): that pipeline is re-aggregated over the
    window, exactly as the workflow engine does for a saved run; any other pipeline returns all its rows.

    ``require_project`` is checked here and again, inside the pipeline, on the read itself, so a
    caller that skips this function still cannot read.
    """
    from connect_labs.hqdata.stores import store_for
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.workflow.data_access import serialize_pipeline_row

    require_project(user, pipeline.project)
    config = config_for(pipeline)
    from connect_labs.hqdata.pipeline_source import ensure_fits

    ensure_fits(config.data_source)  # a very large project fails here, in words, not in the worker
    # The session closes whatever connection into a customer's database the run opened.
    scoped = bool(window) and bool((pipeline.schema or {}).get("period_scoped"))
    with store_for(pipeline.project).session():
        analysis = AnalysisPipeline(None, local_only_user=user)
        result = analysis.stream_analysis_ignore_events(
            config, opportunity_id=pipeline.project.scope_id, force_refresh=force_refresh
        )
        if scoped:
            config.date_from, config.date_to = window
            windowed = analysis.get_period_scoped_result_only(config, pipeline.project.scope_id)
            if windowed is None:
                raise PipelineProblem(
                    "This pipeline cannot be limited to a period (only a pipeline that groups to one row per "
                    "worker can)."
                )
            result = windowed
    return [serialize_pipeline_row(r) for r in result.rows]


def run_pipeline(user, pipeline: HQPipeline, *, force_refresh: bool = False) -> dict:
    """Run it and return ``{"rows": [...], "table": [[cell...]...], "count": n, "truncated": bool, "columns": [...]}``.

    Rows are capped at ``MAX_ROWS_SHOWN`` for display (``count`` is the true total).
    """
    rows = pipeline_rows(user, pipeline, force_refresh=force_refresh)
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
