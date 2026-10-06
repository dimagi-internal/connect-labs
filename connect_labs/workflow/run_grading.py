"""A run's per-worker indicator grading, read as a person: the cells the report colours.

One place for "how is this run graded right now", shared by the labs MCP's run tools
(``workflow_run_context`` / ``workflow_run_indicators``) and by workflow actions,
which brief a coaching conversation from the same cells (``coach_briefing.py``):

* a COMPLETED run answers from its stored snapshot -- the week as published;
* a live run is graded now, by the same builder a save would run
  (``snapshot_runtime.build_snapshot_for_run``), and cached briefly per person, per
  scope, per run. The cache holds only what that person could build.

Only workflows graded by the semantic layer have this (their snapshot carries
per-worker cells, ``byFLW``); ``is_semantic_report`` says which those are without
building anything.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: How long a live grading is reused for one person, scope and run.
GRADED_CACHE_SECONDS = 120


class NotGraded(Exception):
    """The run's snapshot carries no per-worker indicator grading."""


class GradingUnavailable(Exception):
    """Grading the run now failed; ``message`` is for a person."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def is_semantic_report(definition) -> bool:
    """Whether the workflow's snapshot is built by the semantic layer -- an indicator
    report whose runs grade every worker. Never raises."""
    from connect_labs.workflow.templates import resolve_snapshot_contract

    try:
        contract = resolve_snapshot_contract(definition)
    except Exception:  # noqa: BLE001 -- anything unreadable is "not an indicator report"
        return False
    return bool(contract.get("ok")) and (contract.get("snapshot_inputs") or {}).get("builder") == "semantic_snapshot"


def slim(payload: dict) -> dict:
    """The graded payload without its case records: every cell and catalog, none of
    the rows behind them (``byFLW[].rows`` indexes a case list that can be MBs)."""
    by_flw = [{k: v for k, v in f.items() if k != "rows"} for f in payload.get("byFLW") or []]
    by_llo = [{k: v for k, v in r.items() if k not in ("rows", "opps")} for r in payload.get("byLLO") or []]
    by_opp = [{k: v for k, v in r.items() if k != "rows"} for r in payload.get("byOpp") or []]
    return {
        "byFLW": by_flw,
        "byLLO": by_llo,
        "byOpp": by_opp,
        "programInd": payload.get("programInd") or {},
        "cMeasures": payload.get("cMeasures") or [],
        "display": payload.get("display") or {},
        "generated_at": payload.get("generated_at"),
        "opportunity_labels": (payload.get("deployment") or {}).get("opportunity_labels") or {},
    }


def graded_for_run(
    user, wda, run, *, opportunity_id: int | None = None, program_id: int | None = None, restricted: bool = False
) -> dict:
    """The run's grading, slimmed (``slim``), plus ``source`` (stored | live) and ``cache``.

    ``restricted`` callers are always graded live: a stored snapshot was built from
    whatever the run read when it was saved, which provenance as it is now does not
    vouch for (mcp/visit_access.caller_restricted).

    Raises ``NotGraded`` for a run whose snapshot has no per-worker grading, and
    ``GradingUnavailable`` when a live build fails.
    """
    from django.core.cache import cache

    from connect_labs.workflow import snapshot_runtime
    from connect_labs.workflow.agent_sharing import graded_payload

    if run.is_completed and run.snapshot and not restricted:
        payload = graded_payload(run.snapshot)
        if payload is None:
            raise NotGraded()
        return {**slim(payload), "source": "stored", "cache": None}

    # Scoped by opportunity/program as well as run id: labs-local run ids and
    # production run ids are separate sequences and overlap.
    scope = f"o{opportunity_id}" if opportunity_id is not None else f"p{program_id}"
    key = f"wf-agent-graded:v2:{user.pk}:{scope}:{run.id}:{'r' if restricted else 'f'}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    try:
        built = snapshot_runtime.build_snapshot_for_run(
            wda, run, requested_opportunity_id=opportunity_id, program_id=program_id
        )
    except snapshot_runtime.SnapshotBuildError as e:
        raise GradingUnavailable(e.message) from e
    payload = graded_payload(built["payload"])
    if payload is None:
        raise NotGraded()
    out = {**slim(payload), "source": "live", "cache": snapshot_runtime.cache_state(built["opportunity_ids"])}
    cache.set(key, out, GRADED_CACHE_SECONDS)
    return out
