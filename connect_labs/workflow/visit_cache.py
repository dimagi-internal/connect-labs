"""Ensure a workflow's visit data is cached -- explicitly, one opportunity at a time.

A workflow's numbers come from two caches with DIFFERENT lifetimes:

  raw      `labs_raw_visit_cache`, one copy per (opportunity, pipeline). Semantic
           Layer 1 -- every indicator -- reads it directly.
  computed the visit / entity / FLW caches a pipeline answers from, built FROM raw.

Both expire (an hour in production) and a cleanup job deletes what has expired.
The computed cache can outlive the raw rows it was built from, and a pipeline
serves from it without touching raw. So "run the pipelines" -- the only warm-up
there was, whether by opening the run page or previewing a pipeline -- could
succeed in seconds while the raw rows the indicators need were already gone. The
figures then came back partial, or zero, with nothing failing: measured on
2026-09-11, 9 of 12 KMC opportunities had no raw rows while every pipeline
reported healthy, and a report read 1,681 cases for a cohort of 8,823.

This makes the state explicit and deterministic. For each opportunity in the
workflow's scope, for each pipeline it reads:

  raw       fetched through the backend's own path -- a cache hit if valid, a delta
            top-up if only new visits are missing, a full rebuild if expired -- then
            HELD for a bounded window.
  computed  the pipeline is run (recomputing from fresh raw only if its cache is
            gone), then held for the same window.

The hold is BOUNDED on purpose. Expiry is load-bearing: the periodic full re-read
is the only thing that picks up a visit's status changing on review (see
`_try_delta_refresh`). Holding long enough to finish a job is safe; holding
indefinitely would freeze statuses, so `hold_minutes` is capped.

Rolling, like the history rebuild: `limit` opportunities per call, a cursor back,
and one progress ping per opportunity. A cold 12-opportunity cohort is minutes of
download, which is not one request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HOLD_MINUTES = 90
MAX_HOLD_MINUTES = 180


class VisitCacheError(Exception):
    """The cache could not be ensured, with a stable code.

    codes: no_owner, no_definition, no_pipelines, no_opportunities, bad_hold, bad_limit
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def workflow_pipeline_ids(definition) -> list[int]:
    """Every pipeline a workflow reads. Each has its own raw-cache slot (#116)."""
    out = []
    for source in (
        getattr(definition, "pipeline_sources", None) or (definition.data or {}).get("pipeline_sources") or []
    ):
        pid = source.get("pipeline_id") if isinstance(source, dict) else None
        if pid is not None and int(pid) not in out:
            out.append(int(pid))
    return out


def workflow_opportunity_ids(definition, owner_opportunity_id: int | None) -> list[int]:
    """The opportunities a workflow spans: its `opportunity_ids`, else its owner."""
    ids = getattr(definition, "opportunity_ids", None) or (definition.data or {}).get("opportunity_ids") or []
    ids = [int(o) for o in ids]
    if not ids and owner_opportunity_id is not None:
        ids = [int(owner_opportunity_id)]
    return ids


def _default_slot(access_token: str | None, owner_scope: dict[str, Any]):
    """The production wiring: a cache manager, a raw fetch and a pipeline run per slot."""
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline
    from connect_labs.workflow.data_access import PipelineDataAccess

    analysis = AnalysisPipeline(access_token=access_token)

    def manager(opp: int, pipeline_id: int):
        return SQLCacheManager(opp, pipeline_id=pipeline_id)

    def fetch_raw(opp: int, pipeline_id: int) -> int:
        # The backend's own path decides hit / delta / full rebuild; the return is
        # the slim visit list (storage is always full -- skip_form_json only trims
        # what comes back), so this costs a count, not 11,000 form payloads.
        rows = analysis.backend.fetch_raw_visits(
            opportunity_id=opp,
            access_token=access_token,
            expected_visit_count=analysis.expected_visits_for(opp),
            pipeline_id=pipeline_id,
            skip_form_json=True,
        )
        return len(rows or [])

    def run_pipeline(opp: int, pipeline_id: int) -> dict:
        # Pipelines are owned by the workflow's owner, so the definition is read in
        # THAT scope while the data runs for `opp` -- the same shape a multi-opp
        # workflow's own read has.
        pda = PipelineDataAccess(access_token=access_token, **owner_scope)
        try:
            return pda.execute_pipeline(pipeline_id, opp)
        finally:
            pda.close()

    return manager, fetch_raw, run_pipeline


def ensure_visit_cache(
    data_access,
    definition_id: int,
    *,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    start_at: int = 0,
    limit: int | None = None,
    hold_minutes: int = DEFAULT_HOLD_MINUTES,
    progress: Callable | None = None,
    slot_factory: Callable | None = None,
) -> dict:
    """Make every (opportunity, pipeline) slot a workflow reads valid, and hold it.

    Returns `{opportunities: [...], done, next_start_at, hold_until}`. One entry per
    opportunity, naming what happened on each slot. A failure on one opportunity is
    reported, not raised, so one bad export cannot cost the other eleven -- and the
    report says which, so the caller knows exactly what is still missing.
    """
    from django.utils import timezone

    if (opportunity_id is None) == (program_id is None):
        raise VisitCacheError("no_owner", "provide exactly one of opportunity_id / program_id")
    if not 1 <= int(hold_minutes) <= MAX_HOLD_MINUTES:
        raise VisitCacheError(
            "bad_hold",
            f"hold_minutes must be 1-{MAX_HOLD_MINUTES}: long enough to finish a job, never so long it "
            "freezes visit statuses (expiry is what re-reads them)",
        )
    if limit is not None and limit < 1:
        raise VisitCacheError("bad_limit", f"limit must be a positive number of opportunities; got {limit}")

    definition = data_access.get_definition(definition_id)
    if definition is None:
        raise VisitCacheError("no_definition", f"workflow definition {definition_id} not found")
    pipeline_ids = workflow_pipeline_ids(definition)
    if not pipeline_ids:
        raise VisitCacheError(
            "no_pipelines", f"workflow {definition_id} reads no pipelines, so there is nothing to cache"
        )
    opps = workflow_opportunity_ids(definition, opportunity_id)
    if not opps:
        raise VisitCacheError("no_opportunities", f"workflow {definition_id} spans no opportunities")

    owner_scope = {"program_id": program_id} if program_id is not None else {"opportunity_id": opportunity_id}
    manager, fetch_raw, run_pipeline = (slot_factory or _default_slot)(
        getattr(data_access, "access_token", None), owner_scope
    )

    batch = opps[start_at:] if limit is None else opps[start_at : start_at + limit]
    next_start = start_at + len(batch)
    report: dict = {
        "definition_id": definition_id,
        "pipelines": pipeline_ids,
        "total_opportunities": len(opps),
        "hold_minutes": int(hold_minutes),
        "hold_until": (timezone.now() + timedelta(minutes=int(hold_minutes))).isoformat(),
        "opportunities": [],
        "done": next_start >= len(opps),
        "next_start_at": None if next_start >= len(opps) else next_start,
    }

    for index, opp in enumerate(batch, start=1):
        if progress is not None:
            try:
                progress(index, len(batch), f"caching visits for opportunity {opp}")
            except Exception:  # noqa: BLE001 -- telemetry must never fail the work
                logger.debug("progress callback raised; continuing", exc_info=True)
        entry: dict = {"opportunity_id": opp, "slots": [], "ok": True, "error": None}
        for pid in pipeline_ids:
            slot: dict = {"pipeline_id": pid}
            try:
                mgr = manager(opp, pid)
                before = mgr.get_raw_visit_count()
                slot["raw_visits"] = fetch_raw(opp, pid)
                after = mgr.get_raw_visit_count()
                slot["raw"] = "held" if before and before == after else "refreshed"
                mgr.extend_raw_cache_ttl(int(hold_minutes))
                result = run_pipeline(opp, pid) or {}
                error = (result.get("metadata") or {}).get("error")
                if error:
                    raise RuntimeError(f"pipeline {pid} failed: {error}")
                slot["computed"] = "held" if (result.get("metadata") or {}).get("from_cache") else "recomputed"
                slot["rows"] = len(result.get("rows") or [])
                mgr.hold_computed_caches(int(hold_minutes))
            except Exception as e:  # noqa: BLE001 -- one opportunity must not cost the rest
                logger.warning("visit cache for opp %s pipeline %s failed", opp, pid, exc_info=True)
                slot["error"] = str(e)
                entry["ok"] = False
                entry["error"] = entry["error"] or f"pipeline {pid}: {e}"
            entry["slots"].append(slot)
        report["opportunities"].append(entry)

    report["failed"] = [e["opportunity_id"] for e in report["opportunities"] if not e["ok"]]
    return report
