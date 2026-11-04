"""Reading a workflow's pipelines: from the cache, or by way of a fill on the worker.

The rule and the protocol are `cache_warm.py`'s. This is the pipelines half of it:

    `read_pipeline_data`  a page's whole payload: every eager pipeline, every opportunity
    `read_pipeline_rows`  one pipeline's rows for one opportunity, for a drill
    `fill_pipelines`      the Celery side of both, and of `pipeline_query_api`

Both reads answer with a `PipelineAnswer`, and each endpoint decides only how to send it:
JSON for the endpoints a page polls, events for the old stream routes that forked copies
of a render still open. So the transports cannot give different answers.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from connect_labs.workflow.cache_warm import WarmSlot

logger = logging.getLogger(__name__)

COLD_PIPELINES_MESSAGE = "Loading the pipelines. The first load can take several minutes on a large opportunity."

# What a read of the query string means to a pipeline, carried to the fill because the
# worker has no query string of its own.
_FILL_PARAMS = ("refresh", "accept_low_count", "tolerance")


@dataclass
class PipelineAnswer:
    """Exactly one of: `pipelines`/`rows` to send, a fill to wait on, an error to report."""

    data: Any = None
    warming: dict | None = None
    waiting_message: str = COLD_PIPELINES_MESSAGE
    error: str | None = None
    status: int = 200
    details: dict = field(default_factory=dict)


def _waiting(slot: WarmSlot, message: str = COLD_PIPELINES_MESSAGE) -> PipelineAnswer:
    return PipelineAnswer(warming=slot.state() or {}, waiting_message=message)


def workflow_fill_slot(definition_id: int, opp_ids, token: str | None = None) -> WarmSlot:
    """The one fill of a workflow's eager pipelines, for this set of opportunities."""
    spanned = ",".join(str(o) for o in sorted(int(o) for o in opp_ids))
    digest = hashlib.sha1(spanned.encode()).hexdigest()[:12]  # noqa: S324 -- a cache key
    return WarmSlot(f"pipelines:warm:{int(definition_id)}:{digest}", token=token)


def _fill_params(request) -> dict:
    return {name: request.GET[name] for name in _FILL_PARAMS if request.GET.get(name)}


def _viewer_id(request) -> int | None:
    pk = getattr(getattr(request, "user", None), "pk", None)
    return pk if isinstance(pk, int) else None


def _visit_count(request) -> int | None:
    """The opportunity's visit count from this read's context, which `read_cached`
    validates against. A fill without it could call a cache good that the read then
    calls cold, and the read would wait on fills forever."""
    opportunity = (getattr(request, "labs_context", None) or {}).get("opportunity") or {}
    return opportunity.get("visit_count") or None


def queue_fill(
    request,
    slot: WarmSlot,
    *,
    definition_id: int,
    scope: dict,
    opportunity_ids: list,
    aliases: list[str] | None = None,
    definition_scope: dict | None = None,
) -> None:
    """Have the worker fill these pipelines, if no fill of them is under way.

    `scope` is where the pipeline records are read. `definition_scope` is where the
    workflow's own record is, when that differs: a program-owned workflow is only visible
    in its program, while its pipelines are read in its first opportunity.
    """
    from connect_labs.workflow.tasks import warm_pipelines

    slot.queue_once(
        lambda claim: warm_pipelines.delay(
            (request.session.get("labs_oauth", {}) or {}).get("access_token"),
            definition_id=int(definition_id),
            scope=scope,
            definition_scope=definition_scope,
            slot_key=slot.key,
            claim=claim,
            aliases=aliases,
            opportunity_ids=opportunity_ids,
            user_id=_viewer_id(request),
            params=_fill_params(request),
            visit_count=_visit_count(request),
        ),
        forced=request.GET.get("refresh") == "1",
    )


def failure_entry(exc: Exception) -> dict:
    """One target's failure, in the per_opp metadata renders already read."""
    from connect_labs.labs.analysis.backends.sql.cache import CacheConcurrencyError, DuplicateSourceVisitsError
    from connect_labs.labs.integrations.commcare.api_client import CCHQAuthError

    entry: dict[str, Any] = {"error": str(exc) or type(exc).__name__}
    if isinstance(exc, CCHQAuthError):
        entry["auth_error"] = "commcare_hq"
        entry["auth_error_domain"] = exc.domain
    if isinstance(exc, DuplicateSourceVisitsError):
        entry["duplicate_visit_ids"] = exc.duplicate_ids[:20]
    elif isinstance(exc, CacheConcurrencyError):
        entry["concurrent_run"] = True
        entry["cache_table"] = exc.table
    return entry


def probe_cchq_access(request, definition, opportunity_id: int, access_token: str) -> PipelineAnswer | None:
    """Before queueing a fill that reads CommCare HQ, ask whether HQ will have it.

    A second or two here, against several minutes on the worker to discover the same
    thing. Answers with the error to report, or None to carry on.
    """
    from connect_labs.labs.analysis.data_access import fetch_opportunity_metadata
    from connect_labs.labs.integrations.commcare.api_client import CommCareDataAccess
    from connect_labs.workflow.data_access import PipelineDataAccess

    needs_cchq = False
    for source in definition.pipeline_sources or []:
        sid = source.get("pipeline_id")
        if not sid:
            continue
        try:
            access = PipelineDataAccess(request=request, access_token=access_token, opportunity_id=opportunity_id)
            access.use_sources(definition.pipeline_sources)
            try:
                pipeline = access.get_definition(sid)
            finally:
                access.close()
            if pipeline and pipeline.schema and pipeline.schema.get("data_source", {}).get("type") == "cchq_forms":
                needs_cchq = True
                break
        except Exception:  # noqa: BLE001 -- classifying a source must not block the read
            continue
    if not needs_cchq:
        return None
    try:
        metadata = fetch_opportunity_metadata(access_token, opportunity_id)
        domain = metadata.get("cc_domain")
        if not domain:
            return PipelineAnswer(
                error="Opportunity has no CommCare domain configured. Contact your project admin.", status=400
            )
        if not CommCareDataAccess(request, domain).verify_hq_access():
            return PipelineAnswer(
                error=(
                    "CommCare HQ access denied. The OAuth token may have expired or you may have lost "
                    "access to the project. Re-authorize at /labs/commcare/initiate/?next=/labs/overview/."
                ),
                status=403,
                details={
                    "cchq_auth_required": True,
                    "authorize_url": "/labs/commcare/initiate/?next=/labs/overview/",
                    "domain": domain,
                },
            )
    except Exception as exc:  # noqa: BLE001 -- the probe failing says nothing about HQ; the fill will find out
        logger.warning("[PipelineRead] CCHQ probe failed: %s", exc)
    return None


def _configs_signature(configs: dict) -> str:
    """Which versions of a workflow's pipelines a fill read, to tell it from an edit."""
    from connect_labs.labs.analysis.utils import get_config_hash

    return ",".join(f"{alias}:{get_config_hash(config)}" for alias, config in sorted((configs or {}).items()))


def _alias_metadata(*, pipeline_id, name, rows, opp_ids, per_opp, config) -> dict:
    from connect_labs.workflow.data_access import program_drive_scope

    metadata: dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "pipeline_name": name,
        "row_count": len(rows),
        "opportunity_ids": list(opp_ids),
        "per_opp": per_opp,
    }
    program_id = program_drive_scope(config)
    if program_id is not None:
        # Read once for the program; its rows carry opportunity_id null.
        metadata["program_id"] = program_id
        metadata["read_once_for_program"] = True
    # Lifted to the alias so a render can check `pipelines[alias].metadata.auth_error` once,
    # rather than dashboards showing "0 rows" with the real reason buried under per_opp.
    auth_failed = [k for k, m in per_opp.items() if m.get("auth_error") == "commcare_hq"]
    if auth_failed:
        metadata["auth_error"] = "commcare_hq"
        metadata["auth_error_domain"] = next((per_opp[k].get("auth_error_domain") for k in auth_failed), None)
        metadata["auth_authorize_url"] = "/labs/commcare/initiate/"
    # The same, for the raw-fetch shrink guard.
    anomalous = [k for k, m in per_opp.items() if m.get("raw_fetch_anomaly")]
    if anomalous:
        metadata["raw_fetch_anomalies"] = [{"opportunity_id": k, **per_opp[k]["raw_fetch_anomaly"]} for k in anomalous]
    return metadata


def read_pipeline_data(request, definition_id: int, *, on_read: Callable | None = None) -> PipelineAnswer:
    """A page's pipelines from the cache, or a 202's worth of fill to wait on.

    Every gate of a normal read applies on every poll, in this request, as this viewer.
    `on_read(**counts)` hears each read, for telemetry.
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.labs.analysis.utils import resolve_join_hashes
    from connect_labs.workflow.data_access import (
        PipelineDataAccess,
        WorkflowDataAccess,
        on_demand_placeholder,
        pipeline_read_targets,
        serialize_pipeline_row,
        streamed_and_skipped_aliases,
    )
    from connect_labs.workflow.views import (
        _coerce_int,
        _resolve_pipeline_definition,
        _resolve_pipeline_sources_for_run,
    )

    labs_context = getattr(request, "labs_context", {}) or {}
    # Program-owned workflows drive this by program_id: their runner carries no owning
    # opportunity. The stringified `undefined`/`null` one used to send coerces to None.
    opportunity_id = _coerce_int(labs_context.get("opportunity_id") or request.GET.get("opportunity_id"))
    access_token = (request.session.get("labs_oauth", {}) or {}).get("access_token")
    if not access_token:
        return PipelineAnswer(error="No OAuth token found. Please log in to Connect.", status=401)

    data_access = WorkflowDataAccess(request=request)
    try:
        definition = data_access.get_definition(definition_id)
    finally:
        data_access.close()
    if not definition:
        return PipelineAnswer(error=f"Workflow {definition_id} not found", status=404)
    if not definition.pipeline_sources:
        return PipelineAnswer(data={})

    # A program-owned workflow has no owning opportunity in the request. The first it
    # spans scopes the record reads; the data below still covers every id in opp_ids. One
    # spanning NONE is still runnable when what it reads is program-scoped.
    owning_program = getattr(definition, "program_id", None)
    if not opportunity_id:
        spanned = definition.opportunity_ids or []
        if not spanned and not owning_program:
            return PipelineAnswer(error="No opportunity selected", status=400)
        opportunity_id = spanned[0] if spanned else None
    opp_ids = definition.opportunity_ids or ([int(opportunity_id)] if opportunity_id else [])

    # A poll during a fill stops here. It is what a waiting page sends every few seconds,
    # and everything below costs round trips. The exception is someone else's refresh:
    # the cache that fill is replacing still serves this reader, until one finds it can't.
    forced = request.GET.get("refresh") == "1"
    slot = workflow_fill_slot(definition_id, opp_ids)
    filling = slot.state()
    if filling and (forced or not filling.get("forced") or slot.known_cold(filling)):
        return _waiting(slot)

    scope = {"opportunity_id": int(opportunity_id)} if opportunity_id else {"program_id": int(owning_program)}
    finished = None if filling else slot.outcome()
    if forced and not (finished and finished.get("forced")):
        # A fill that ran for someone else's plain read is not the refresh asked for.
        finished = None

    def start_fill() -> PipelineAnswer:
        if opportunity_id:
            refused = probe_cchq_access(request, definition, int(opportunity_id), access_token)
            if refused is not None:
                return refused
        queue_fill(
            request,
            slot,
            definition_id=definition_id,
            scope=scope,
            definition_scope={"program_id": int(owning_program)} if owning_program else None,
            opportunity_ids=list(opp_ids),
        )
        return _waiting(slot)

    if forced and not finished:
        return start_fill()

    pipeline_access = PipelineDataAccess(request=request, access_token=access_token, **scope)
    pipeline_access.use_sources(definition.pipeline_sources)
    try:
        ordered, configs = _resolve_pipeline_sources_for_run(
            pipeline_access, definition.pipeline_sources, opp_ids=opp_ids, request=request, access_token=access_token
        )
        if configs:
            resolve_join_hashes(configs)
        # A fill of the pipelines as they were before an edit says nothing about these.
        if finished and finished.get("configs") not in (None, _configs_signature(configs)):
            finished = None
        # It failed before it reached any pipeline: nothing below could say why.
        if finished and finished.get("error") and not finished.get("report"):
            slot.take_outcome()
            return PipelineAnswer(error=f"The report's data could not be loaded: {finished['error']}", status=502)
        # `load: "on_demand"` sources are not shipped: render code queries them. One is
        # still filled when an eager pipeline JOINs it, which reads its computed cache.
        execute, on_demand = streamed_and_skipped_aliases(ordered, configs)
        report = (finished or {}).get("report") or {}
        analysis = AnalysisPipeline(request)
        pipelines: dict[str, dict] = {}
        for source in ordered:
            pipeline_id = source.get("pipeline_id")
            alias = source.get("alias", f"pipeline_{pipeline_id}")
            if not pipeline_id:
                continue
            if alias in on_demand and alias not in execute:
                pipelines[alias] = on_demand_placeholder(source, opp_ids)
                continue
            record = _resolve_pipeline_definition(
                pipeline_access, pipeline_id, opp_ids=opp_ids, request=request, access_token=access_token
            )
            if not record:
                pipelines[alias] = {
                    "rows": [],
                    "metadata": {
                        "pipeline_id": pipeline_id,
                        "pipeline_name": None,
                        "row_count": 0,
                        "opportunity_ids": list(opp_ids),
                        "per_opp": {str(o): {"error": "Pipeline not found"} for o in opp_ids},
                    },
                }
                continue
            config = configs.get(alias) or pipeline_access._schema_to_config(record.schema, pipeline_id)
            targets = pipeline_read_targets(config, opp_ids)
            rows: list[dict] = []
            per_opp: dict[str, dict] = {}
            for index, (key, opp, tag) in enumerate(targets, start=1):
                reported = (report.get(alias) or {}).get(key) or {}
                try:
                    # A failed target shows its failure, not the older rows it failed to replace.
                    result = None if reported.get("error") else analysis.read_cached(config, opp)
                    if result is None and finished and not reported.get("error"):
                        # The fill ran and this still reads as cold. A visit count that
                        # drifted must not read as no data.
                        result = analysis.get_cached_result_only(config, opp)
                except Exception as exc:  # noqa: BLE001 -- a read this viewer may not make is theirs alone
                    logger.warning("[PipelineRead] %s read failed for %s", alias, key, exc_info=True)
                    per_opp[key] = failure_entry(exc)
                    continue
                if result is None and not finished:
                    if not filling:
                        return start_fill()
                    slot.note_cold(filling)
                    return _waiting(slot)
                found = list(result.rows) if result else []
                per_opp[key] = {"row_count": len(found), "from_cache": True, **reported}
                rows.extend(serialize_pipeline_row(row, extra={"opportunity_id": tag}) for row in found)
                if on_read is not None:
                    on_read(
                        rows=len(found),
                        pipeline_id=_coerce_int(pipeline_id),
                        target=index,
                        targets=len(targets),
                        failed=bool(reported.get("error")),
                    )
            if alias in on_demand:
                placeholder = on_demand_placeholder(source, opp_ids)
                placeholder["metadata"]["per_opp"] = per_opp
                pipelines[alias] = placeholder
                continue
            pipelines[alias] = {
                "rows": rows,
                "metadata": _alias_metadata(
                    pipeline_id=pipeline_id,
                    name=record.name,
                    rows=rows,
                    opp_ids=opp_ids,
                    per_opp=per_opp,
                    config=config,
                ),
            }
        if finished and (forced or not finished.get("forced")):
            # Answered: this reader's next load starts over. A forced fill's outcome is
            # left for the reader who asked for it, or that reader would fill again.
            slot.take_outcome()
        return PipelineAnswer(data=pipelines)
    finally:
        pipeline_access.close()


def pipeline_rows_slot(config, cache_scope: int, token: str | None = None) -> WarmSlot:
    """The one fill of one pipeline for one opportunity. A drill and a query share it."""
    from connect_labs.workflow.pipeline_query import warm_cache_key

    return WarmSlot(warm_cache_key(cache_scope, config), token=token)


def read_pipeline_rows(request, definition_id: int, params: dict, pipeline_access, pipeline_id: int, config):
    """One pipeline's cached result for one opportunity, or a fill to wait on.

    Answers with `{"cached": {"rows", ...}, "from_cache": bool}` as data. The caller
    filters: a drill asks for one worker's rows, and the fill is the opportunity's.
    """
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    opportunity_id = params["opportunity_id"]
    message = f"Loading the {params['alias']} pipeline. The first load can take a few minutes."
    slot = pipeline_rows_slot(config, AnalysisPipeline.data_scope(config, opportunity_id))
    if slot.state():
        return _waiting(slot, message)
    cached = pipeline_access.get_cached_pipeline_result(pipeline_id, opportunity_id, config=config)
    if cached is not None:
        return PipelineAnswer(data={"cached": cached, "from_cache": True})
    finished = slot.take_outcome()
    if finished is not None:
        if finished.get("error"):
            return PipelineAnswer(error=finished["error"], status=502)
        return PipelineAnswer(data={"cached": {"rows": []}, "from_cache": False})  # it ran; there are none
    scope_opp = params.get("scope_opportunity_id")
    queue_fill(
        request,
        slot,
        definition_id=definition_id,
        scope={"opportunity_id": int(scope_opp)} if scope_opp else {"program_id": params.get("scope_program_id")},
        definition_scope=params.get("definition_scope"),
        opportunity_ids=[int(opportunity_id)],
        aliases=[params["alias"]],
    )
    return _waiting(slot, message)


def fill_pipelines(
    access_token: str,
    *,
    definition_id: int,
    scope: dict,
    slot_key: str,
    definition_scope: dict | None = None,
    claim: str | None = None,
    aliases: list[str] | None = None,
    opportunity_ids: list[int] | None = None,
    user_id: int | None = None,
    params: dict | None = None,
    visit_count: int | None = None,
) -> dict:
    """Hold the slot while `_fill` runs. Celery-side."""
    slot = WarmSlot(slot_key, token=claim)
    if slot.superseded():
        return {"superseded": True}
    with slot.running() as outcome:
        return _fill(
            access_token,
            slot=slot,
            outcome=outcome,
            definition_id=definition_id,
            scope=scope,
            definition_scope=definition_scope,
            aliases=aliases,
            opportunity_ids=opportunity_ids,
            user_id=user_id,
            params=params or {},
            visit_count=visit_count,
        )


def _fill(
    access_token: str,
    *,
    slot: WarmSlot,
    outcome: dict,
    definition_id: int,
    scope: dict,
    definition_scope: dict | None,
    aliases: list[str] | None,
    opportunity_ids: list[int] | None,
    user_id: int | None,
    params: dict,
    visit_count: int | None,
) -> dict:
    """Fill `aliases` (the page's eager pipelines when None), and any pipeline they JOIN,
    for `opportunity_ids`, as the person who asked.

    Every gate of a normal read applies, because this IS one (`stream_analysis`) under
    their own tokens. One target's failure does not cost the others: it lands in the
    outcome's `report`, keyed as the read keys `per_opp`, for the reader to show.

    `opportunity_ids` may hold a cache scope: negative for a program-scoped Drive source
    (`gdrive_fetcher.program_cache_scope`), which no record lives in, so the records are
    then read in the workflow's program scope.
    """
    from connect_labs.labs.analysis.pipeline import (
        EVENT_DOWNLOAD,
        EVENT_ERROR,
        EVENT_RESULT,
        EVENT_STATUS,
        AnalysisPipeline,
    )
    from connect_labs.labs.analysis.sse_streaming import download_progress
    from connect_labs.labs.analysis.utils import resolve_join_hashes
    from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none
    from connect_labs.workflow.data_access import (
        PipelineDataAccess,
        WorkflowDataAccess,
        pipeline_read_targets,
        program_drive_scope,
        streamed_and_skipped_aliases,
    )
    from connect_labs.workflow.pipeline_query import PipelineQueryError
    from connect_labs.workflow.tasks import _create_mock_request
    from connect_labs.workflow.views import _resolve_pipeline_sources_for_run

    user = None
    if user_id is not None:
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_id).first()
    cchq_token = cchq_token_or_none(user) if user is not None else None
    request = _create_mock_request(access_token, **scope)
    request.user = user
    request.GET = dict(params)
    if visit_count:
        request.labs_context["opportunity"] = {"visit_count": visit_count}
    outcome["forced"] = params.get("refresh") == "1"

    pipeline_access = None
    try:
        wf_access = WorkflowDataAccess(request=_create_mock_request(access_token, **(definition_scope or scope)))
        try:
            definition = wf_access.get_definition(definition_id)
        finally:
            wf_access.close()
        if definition is None:
            raise PipelineQueryError(f"workflow {definition_id} not found", status=404)
        sources = definition.pipeline_sources or []
        reading = [int(o) for o in (opportunity_ids or [])]
        is_program_key = bool(reading) and reading[0] < 0
        opp_ids = [int(o) for o in (definition.opportunity_ids or [])] or ([] if is_program_key else reading)
        pipeline_access = PipelineDataAccess(request=request, access_token=access_token, **scope)
        pipeline_access.use_sources(sources)
        ordered, configs = _resolve_pipeline_sources_for_run(
            pipeline_access, sources, opp_ids=opp_ids, request=request, access_token=access_token
        )
        if configs:
            resolve_join_hashes(configs)
        outcome["configs"] = _configs_signature(configs)

        if aliases is None:
            wanted = streamed_and_skipped_aliases(ordered, configs)[0]
        else:
            wanted = set(aliases)
            missing = [name for name in aliases if name not in configs]
            if missing:
                raise PipelineQueryError(f"pipeline source {missing[0]!r} could not be loaded", status=404)
        needed, frontier = set(), list(wanted)
        while frontier:
            current = frontier.pop()
            if current not in needed:
                needed.add(current)
                frontier.extend(j.from_alias for j in getattr(configs.get(current), "joins", None) or [])

        plan = []
        for source in ordered:
            name = source.get("alias")
            if name not in needed or name not in configs:
                continue
            if is_program_key and program_drive_scope(configs[name]) is None:
                raise PipelineQueryError(
                    f"{aliases[0]!r} is read once for its program, but it joins {name!r}, which is per-opportunity; "
                    "a program-scoped pipeline can only join other program-scoped pipelines.",
                    status=400,
                )
            targets = (
                [(str(reading[0]), reading[0], None)]
                if is_program_key
                else pipeline_read_targets(configs[name], reading)
            )
            plan.extend((name, *target) for target in targets)

        report: dict[str, dict] = {}
        warmed = []
        for step, (name, key, opp, _tag) in enumerate(plan, start=1):
            config = configs[name]

            def say(message: str, **progress) -> None:
                slot.report(message, stage_name=name, current_stage=step, total_stages=len(plan), **progress)

            analysis = AnalysisPipeline(request, access_token=access_token, cchq_access_token=cchq_token)
            try:
                result = None
                for event, data in analysis.stream_analysis(config, opp):
                    if event == EVENT_STATUS:
                        say(data.get("message", "Processing..."))
                    elif event == EVENT_DOWNLOAD:
                        say(**download_progress(data))
                    elif event == EVENT_RESULT:
                        result = data
                    elif event == EVENT_ERROR:
                        raise data.get("exception") or RuntimeError(data.get("message", "Pipeline error"))
                anomaly = (getattr(result, "metadata", None) or {}).get("raw_fetch_anomaly")
                if anomaly:
                    report.setdefault(name, {})[key] = {"raw_fetch_anomaly": anomaly}
                warmed.append(name)
            except Exception as exc:  # noqa: BLE001 -- one bad export must not cost the rest
                logger.warning(
                    "pipeline fill of %s for %s (workflow %s) failed", name, key, definition_id, exc_info=True
                )
                entry = failure_entry(exc)
                report.setdefault(name, {})[key] = entry
                outcome["error"] = outcome["error"] or entry["error"]
        if report:
            outcome["report"] = report
        return {"warmed": warmed, "failed": sorted(k for r in report.values() for k, v in r.items() if v.get("error"))}
    except Exception as exc:  # noqa: BLE001 -- the reader is polling; it must hear what happened
        logger.warning("pipeline fill for workflow %s failed", definition_id, exc_info=True)
        message = (exc.message if isinstance(exc, PipelineQueryError) else str(exc)) or type(exc).__name__
        outcome["error"] = message
        return {"error": message}
    finally:
        if pipeline_access is not None:
            pipeline_access.close()
