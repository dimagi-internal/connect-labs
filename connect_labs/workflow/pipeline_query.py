"""Server-side row queries over a pipeline's cached terminal rows.

`actions.queryPipelineRows(alias, {filters, search, order_by, limit, offset})` in a
workflow's render code lands here (via ``views.pipeline_query_api``). It exists for
pipelines too large to ship to the browser whole -- the ``load: "on_demand"``
sources a run page does not stream (see ``data_access.PIPELINE_LOAD_ON_DEMAND``).
Workflow 23765 is the motivating case: 173k answer rows of free text, 27 s and
~435 MB of browser heap on every page load, to read one question's answers at a time.

Everything here is answered IN SQL from the pipeline's computed cache (the visit,
entity or FLW cache, whichever is its terminal stage): the filter, the search, the
order, the count and the page. Only the requested page of rows is ever loaded into
Python. Field names are checked against the pipeline's declared fields before they
reach the query, and every value travels as a bound parameter (the ORM builds the
SQL; nothing user-supplied is interpolated into it).

Values in a JSON field are compared as TEXT (Postgres ``->>``): ``3`` matches a
stored ``3``, ``true`` a stored boolean true. Ordering on a JSON field uses the
jsonb value, so numbers sort as numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db.models import F, Q
from django.db.models.fields.json import KeyTextTransform, KeyTransform

from connect_labs.labs.analysis.backends.sql.backend import (
    entity_row_from_cache,
    flw_row_from_cache,
    visit_row_from_cache,
)
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.config import GROUPING_COLUMN, AnalysisPipelineConfig, CacheStage

logger = logging.getLogger(__name__)

MAX_QUERY_LIMIT = 500
DEFAULT_QUERY_LIMIT = 100
MAX_QUERY_OFFSET = 1_000_000
MAX_FILTER_FIELDS = 20
MAX_FILTER_VALUES = 500
MAX_SEARCH_TEXT = 200
MAX_SEARCH_FIELDS = 30
MAX_ORDER_BY = 5


class PipelineQueryError(Exception):
    """A query that cannot be answered as asked, with the HTTP status the caller owes."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class _Stage:
    json_column: str
    # Public row key -> model column, for the columns a row carries outside the JSON.
    base_columns: dict


_STAGES = {
    CacheStage.VISIT_LEVEL: _Stage(
        "computed_fields",
        {
            "id": "visit_id",
            "username": "username",
            "visit_date": "visit_date",
            "status": "status",
            "flagged": "flagged",
            "entity_id": "entity_id",
            "entity_name": "entity_name",
            "deliver_unit_name": "deliver_unit",
            "deliver_unit_id": "deliver_unit_id",
        },
    ),
    CacheStage.ENTITY: _Stage(
        "aggregated_fields",
        {
            "entity_id": "entity_id",
            "entity_name": "entity_name",
            "username": "username",
            "total_visits": "total_visits",
            "first_visit_date": "first_visit_date",
            "last_visit_date": "last_visit_date",
        },
    ),
    CacheStage.AGGREGATED: _Stage(
        "aggregated_fields",
        {
            "username": "username",
            "total_visits": "total_visits",
            "approved_visits": "approved_visits",
            "pending_visits": "pending_visits",
            "rejected_visits": "rejected_visits",
            "flagged_visits": "flagged_visits",
            "first_visit_date": "first_visit_date",
            "last_visit_date": "last_visit_date",
        },
    ),
}


def declared_fields(config: AnalysisPipelineConfig) -> list[str]:
    """The pipeline's own field names, in declaration order: what a query may name
    besides the stage's base columns."""
    names = [f.name for f in config.fields]
    names += [w.name for w in getattr(config, "window_fields", None) or [] if getattr(w, "name", None)]
    # A grouped entity pipeline's rows also carry `grouping` (named groupings) and
    # each group key -- a base column key (e.g. `status`) is not a declared field
    # but is a column of the row, so it can be filtered and ordered on too.
    groupings = config.effective_groupings() if hasattr(config, "effective_groupings") else []
    if groupings:
        if any(g.name for g in groupings):
            names.append(GROUPING_COLUMN)
        names += [k for g in groupings for k in g.group_by if k not in ("entity_id", "entity_name", "username")]
    return list(dict.fromkeys(names))


# --------------------------------------------------------------------------- parsing


def parse_query(body) -> dict:
    """Validate the shape of a query body; field names are checked later, per pipeline."""
    if not isinstance(body, dict):
        raise PipelineQueryError("the query must be a JSON object")
    unknown = set(body) - {"alias", "opportunity_id", "filters", "search", "order_by", "limit", "offset"}
    if unknown:
        raise PipelineQueryError(f"unknown query keys: {sorted(unknown)}")

    filters = body.get("filters") or {}
    if not isinstance(filters, dict):
        raise PipelineQueryError("filters must be an object of {field: value | [values]}")
    if len(filters) > MAX_FILTER_FIELDS:
        raise PipelineQueryError(f"at most {MAX_FILTER_FIELDS} filter fields")
    for name, value in filters.items():
        values = value if isinstance(value, list) else [value]
        if isinstance(value, list) and not value:
            raise PipelineQueryError(f"filters.{name} is an empty list")
        if len(values) > MAX_FILTER_VALUES:
            raise PipelineQueryError(f"filters.{name}: at most {MAX_FILTER_VALUES} values")
        for v in values:
            if v is not None and not isinstance(v, (str, int, float, bool)):
                raise PipelineQueryError(f"filters.{name}: values must be strings, numbers, booleans or null")

    search = body.get("search")
    if search is not None:
        if isinstance(search, str):
            search = {"text": search}
        if not isinstance(search, dict) or set(search) - {"text", "fields"}:
            raise PipelineQueryError("search must be {text, fields?}")
        text = search.get("text")
        if not isinstance(text, str) or not text.strip():
            raise PipelineQueryError("search.text must be a non-empty string")
        if len(text) > MAX_SEARCH_TEXT:
            raise PipelineQueryError(f"search.text: at most {MAX_SEARCH_TEXT} characters")
        fields = search.get("fields")
        if fields is not None and (
            not isinstance(fields, list) or not fields or not all(isinstance(f, str) for f in fields)
        ):
            raise PipelineQueryError("search.fields must be a non-empty list of field names")
        if fields and len(fields) > MAX_SEARCH_FIELDS:
            raise PipelineQueryError(f"search.fields: at most {MAX_SEARCH_FIELDS}")
        search = {"text": text.strip(), "fields": fields}

    order_by = body.get("order_by") or []
    if isinstance(order_by, str):
        order_by = [order_by]
    if not isinstance(order_by, list) or not all(isinstance(o, str) and o.lstrip("-") for o in order_by):
        raise PipelineQueryError("order_by must be a field name, '-field' for descending, or a list of them")
    if len(order_by) > MAX_ORDER_BY:
        raise PipelineQueryError(f"order_by: at most {MAX_ORDER_BY} fields")

    try:
        limit = int(body.get("limit") if body.get("limit") is not None else DEFAULT_QUERY_LIMIT)
        offset = int(body.get("offset") or 0)
    except (TypeError, ValueError):
        raise PipelineQueryError("limit and offset must be integers") from None
    if not 1 <= limit <= MAX_QUERY_LIMIT:
        raise PipelineQueryError(f"limit must be 1-{MAX_QUERY_LIMIT}")
    if not 0 <= offset <= MAX_QUERY_OFFSET:
        raise PipelineQueryError(f"offset must be 0-{MAX_QUERY_OFFSET}")

    return {"filters": filters, "search": search, "order_by": order_by, "limit": limit, "offset": offset}


# --------------------------------------------------------------------------- the cache


def _stage(config: AnalysisPipelineConfig) -> CacheStage:
    return config.terminal_stage if config.terminal_stage in _STAGES else CacheStage.VISIT_LEVEL


def cached_queryset(config: AnalysisPipelineConfig, opportunity_id: int):
    """The live terminal-stage cache rows for (opportunity, pipeline config), or None when cold.

    A Google Drive pipeline's rows built from files that have since changed count as
    cold (``gdrive_freshness``), so the caller warms them like any other miss.
    """
    from connect_labs.labs.analysis.backends.sql.gdrive_freshness import computed_cache_is_current

    if not computed_cache_is_current(opportunity_id, config):
        return None
    manager = SQLCacheManager(opportunity_id, config)
    stage = _stage(config)
    if stage == CacheStage.VISIT_LEVEL:
        if not manager.has_valid_computed_visit_cache(0):
            return None
        qs = manager.get_computed_visits_queryset()
        # The pipeline's own `filters` apply to every read of it, as in
        # SQLBackend.get_cached_visit_result.
        for key, value in (config.filters or {}).items():
            if key == "entity_id":
                qs = qs.filter(entity_id=value)
            elif key == "status":
                qs = qs.filter(status__in=value) if isinstance(value, list) else qs.filter(status=value)
            else:
                qs = qs.filter(computed_fields__contains={key: value})
        return qs
    if stage == CacheStage.ENTITY:
        if config.feeds_joins and not manager.has_valid_computed_visit_cache(0):
            # Cached before it became a JOIN target: the JOIN's per-visit input is
            # missing, so warm it (see AnalysisPipeline._cached_result_still_serves).
            return None
        return manager.get_entity_results_queryset() if manager.has_valid_entity_cache(0) else None
    return manager.get_flw_results_queryset() if manager.has_valid_flw_cache(0) else None


def _as_text(value) -> str:
    """How Postgres' ->> renders a JSON scalar, so a filter value compares like for like."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def run_query(config: AnalysisPipelineConfig, opportunity_id: int, query: dict, qs) -> dict:
    """Apply a parsed query to a cache queryset; return ``{rows, total}``.

    ``qs`` is ``cached_queryset(...)``. Raises PipelineQueryError naming any field
    the pipeline does not declare.
    """
    from connect_labs.workflow.data_access import serialize_pipeline_row

    stage = _STAGES[_stage(config)]
    fields = declared_fields(config)
    json_fields = set(fields)
    known = set(stage.base_columns) | json_fields

    def check(name: str, where: str) -> None:
        if name not in known:
            raise PipelineQueryError(
                f"{where}: unknown field {name!r}. This pipeline's fields are {sorted(known)}", status=400
            )

    annotations: dict = {}

    def text_of(name: str) -> str:
        """The lookup name for a field's text value (a base column, or an annotation)."""
        if name in stage.base_columns and name not in json_fields:
            return stage.base_columns[name]
        key = f"_pq_text_{len(annotations)}"
        annotations[key] = KeyTextTransform(name, stage.json_column)
        return key

    conditions = Q()
    for name, value in query["filters"].items():
        check(name, "filters")
        values = value if isinstance(value, list) else [value]
        non_null = [v for v in values if v is not None]
        target = text_of(name)
        is_base = target == stage.base_columns.get(name)
        cond = Q()
        if non_null:
            if is_base:
                cond |= Q(**{f"{target}__in": non_null})
            else:
                cond |= Q(**{f"{target}__in": [_as_text(v) for v in non_null]})
        if len(non_null) != len(values):
            cond |= Q(**{f"{target}__isnull": True})
        conditions &= cond

    search = query.get("search")
    if search:
        search_fields = search.get("fields")
        if search_fields:
            for name in search_fields:
                check(name, "search.fields")
        else:
            search_fields = fields
        if not search_fields:
            raise PipelineQueryError("search: this pipeline declares no fields to search; name search.fields")
        any_match = Q()
        for name in search_fields:
            any_match |= Q(**{f"{text_of(name)}__icontains": search["text"]})
        conditions &= any_match

    if annotations:
        qs = qs.annotate(**annotations)
    qs = qs.filter(conditions)

    ordering = []
    for term in query["order_by"]:
        descending = term.startswith("-")
        name = term.lstrip("-")
        check(name, "order_by")
        if name in stage.base_columns and name not in json_fields:
            expr = F(stage.base_columns[name])
        else:
            expr = KeyTransform(name, stage.json_column)
        ordering.append(expr.desc(nulls_last=True) if descending else expr.asc(nulls_last=True))
    ordering.append(F("pk").asc())  # a stable order, so pages never overlap or skip

    total = qs.count()
    page = qs.order_by(*ordering)[query["offset"] : query["offset"] + query["limit"]]
    builder = {
        CacheStage.VISIT_LEVEL: visit_row_from_cache,
        CacheStage.ENTITY: entity_row_from_cache,
        CacheStage.AGGREGATED: flw_row_from_cache,
    }[_stage(config)]
    rows = [serialize_pipeline_row(builder(r), extra={"opportunity_id": opportunity_id}) for r in page]
    return {"rows": rows, "total": total}


# --------------------------------------------------------------------------- a cold cache

# A cold cache is filled the normal way (the pipeline runs, exactly as the page
# stream would run it), but OFF the request: on a big source that takes longer than
# the load balancer's 60 s idle timeout, and a request that dies there reports
# nothing. So the endpoint answers 202 `{status: "warming"}` at once, a Celery task
# fills the cache, and the caller asks again (actions.queryPipelineRows polls).
WARM_LOCK_SECONDS = 20 * 60
WARM_ERROR_SECONDS = 5 * 60


def warm_cache_key(opportunity_id: int, config: AnalysisPipelineConfig) -> str:
    from connect_labs.labs.analysis.utils import get_config_hash

    return f"pipeline_query:warm:{int(opportunity_id)}:{config.pipeline_id}:{get_config_hash(config)}"


def warm_pipeline_cache(
    access_token: str,
    *,
    definition_id: int,
    alias: str,
    opportunity_id: int,
    scope: dict,
    lock_key: str,
) -> dict:
    """Fill ``alias``'s terminal cache for ``opportunity_id`` -- and that of any
    pipeline it JOINs, when cold -- as the person who asked. Celery-side.

    Every gate of a normal read applies, because this IS a normal read
    (``AnalysisPipeline.stream_analysis``), under the caller's own token. A failure is
    left under ``lock_key + ":error"`` for the next poll to report.
    """
    from django.core.cache import cache

    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.labs.analysis.utils import resolve_join_hashes
    from connect_labs.workflow.data_access import PipelineDataAccess, WorkflowDataAccess, program_drive_scope
    from connect_labs.workflow.tasks import _create_mock_request
    from connect_labs.workflow.views import _resolve_pipeline_sources_for_run

    request = _create_mock_request(access_token, **scope)
    pipeline_access = None
    try:
        wf_access = WorkflowDataAccess(request=request)
        try:
            definition = wf_access.get_definition(definition_id)
        finally:
            wf_access.close()
        if definition is None:
            raise PipelineQueryError(f"workflow {definition_id} not found", status=404)
        sources = definition.pipeline_sources or []
        # `opportunity_id` is the cache scope: negative for a program-scoped Drive
        # source (gdrive_fetcher.program_cache_scope), which no record lives in -- the
        # records are then read in the workflow's program scope instead.
        is_program_key = int(opportunity_id) < 0
        opp_ids = [int(o) for o in (definition.opportunity_ids or [])] or (
            [] if is_program_key else [int(opportunity_id)]
        )
        record_opp = scope.get("opportunity_id") or (None if is_program_key else int(opportunity_id))
        pipeline_access = PipelineDataAccess(
            request=request,
            access_token=access_token,
            **({"opportunity_id": record_opp} if record_opp else {"program_id": scope.get("program_id")}),
        )
        pipeline_access.use_sources(sources)
        ordered, configs = _resolve_pipeline_sources_for_run(
            pipeline_access, sources, opp_ids=opp_ids, request=request, access_token=access_token
        )
        if configs:
            resolve_join_hashes(configs)
        if alias not in configs:
            raise PipelineQueryError(f"pipeline source {alias!r} could not be loaded", status=404)

        needed, frontier = set(), [alias]
        while frontier:
            current = frontier.pop()
            if current in needed:
                continue
            needed.add(current)
            frontier.extend(j.from_alias for j in getattr(configs.get(current), "joins", None) or [])

        warmed = []
        for source in ordered:
            name = source.get("alias")
            if name not in needed or name not in configs:
                continue
            if is_program_key and program_drive_scope(configs[name]) is None:
                raise PipelineQueryError(
                    f"{alias!r} is read once for its program, but it joins {name!r}, which is per-opportunity; "
                    "a program-scoped pipeline can only join other program-scoped pipelines.",
                    status=400,
                )
            if name != alias and cached_queryset(configs[name], opportunity_id) is not None:
                continue  # a warm dependency needs nothing
            AnalysisPipeline(request, access_token=access_token).stream_analysis_ignore_events(
                configs[name], opportunity_id
            )
            warmed.append(name)
        return {"warmed": warmed}
    except Exception as exc:
        logger.warning("pipeline-query warm of %s (workflow %s, opp %s) failed", alias, definition_id, opportunity_id)
        message = exc.message if isinstance(exc, PipelineQueryError) else str(exc)
        cache.set(lock_key + ":error", message[:500], WARM_ERROR_SECONDS)
        return {"error": message}
    finally:
        if pipeline_access is not None:
            pipeline_access.close()
        cache.delete(lock_key)
