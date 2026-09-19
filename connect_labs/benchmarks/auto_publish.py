"""Publishing a cohort from a saved run: by hand, on save, and after a history rebuild.

`publish_run` is the one path. `benchmarks_publish` (MCP) calls it for a human who
names a run. `publish_for_completed_run` calls it when a run of a cohort's
`source_workflow_id` is saved, for every cohort with `auto_publish_on_completion`
on -- so the peer figures follow the report rather than freezing at the run
someone last published by hand. `publish_latest_for_workflow` does the same after
a history rebuild, from the newest completed run: a trend is the series of saved
runs, so a rebuild changes every peer line, and publishing BEFORE rebuilding (done
by hand on 2026-09-18) left an older opportunity with no line at all because its
early weeks were not in the history yet.

The automatic paths are best effort. A failure is logged and reported, never
raised into the save that triggered it: a report that saved and did not publish is
recoverable by publishing; a save refused because a benchmark could not be
written is not what anyone asked for.
"""

from __future__ import annotations

import logging

from connect_labs.benchmarks.models import BenchmarkCohort
from connect_labs.benchmarks.publish import publish_benchmark
from connect_labs.workflow.templates import resolve_snapshot_contract

logger = logging.getLogger(__name__)


class PublishRefused(Exception):
    """The run cannot be published as asked: not completed, another workflow's, or undated."""


def run_history(wda, workflow_id: int, state_key: str) -> list[dict]:
    """Every completed run of `workflow_id`, oldest first, projected to `byOpp`.

    A failure here costs the SERIES and nothing else, so it is logged and
    swallowed rather than taking the publication down with it: the point values
    are the publication's substance and they come from the snapshot already in
    hand. A publication with no series is a visible, recoverable state; a
    refused publication because a history read timed out is not.
    """
    # ONE POINT PER PERIOD, not per run. A period can hold several completed
    # runs -- a hand-saved one and the one `workflow_rebuild_history` generated
    # for the same week, or seven re-runs of the same week while something was
    # being fixed -- and they do not agree, because each was computed from what
    # was cached when it ran. Taking all of them made consecutive points
    # alternate between two unrelated figures for the whole length of the
    # series, which renders as a violently oscillating indicator rather than as
    # the duplication it is. The LATEST completion of a period wins: a
    # recomputation supersedes what it recomputed.
    latest: dict[str, tuple] = {}
    try:
        # The iteration is inside the guard, not just the call: `list_runs`
        # resolves lazily, so the upstream failure surfaces on the first `for`.
        for run in wda.list_runs(definition_id=workflow_id):
            if not getattr(run, "is_completed", False):
                continue
            payload = ((run.snapshot or {}).get("state") or {}).get(state_key) or {}
            by_opp = {}
            for name, block in [("C", payload)] + sorted((payload.get("series") or {}).items()):
                cells = {}
                for entry in (block or {}).get("byOpp") or []:
                    if entry.get("opp") is not None:
                        cells[int(entry["opp"])] = entry.get("ind") or {}
                if cells:
                    by_opp[name] = cells
            if not by_opp:
                continue
            period = str(run.period_end or run.completed_at or "")[:10]
            stamp = str(run.completed_at or "")
            if period not in latest or stamp >= latest[period][0]:
                latest[period] = (stamp, {"date": period, "byOpp": by_opp})
    except Exception:
        logger.warning("benchmark publication could not read run history for workflow %s", workflow_id, exc_info=True)
        return []
    return [entry for _, entry in sorted((p, e) for p, (_, e) in latest.items())]


def publish_run(
    cohort,
    wda,
    workflow_id: int,
    run,
    *,
    run_id: int | None = None,
    published_by: str = "",
    benchmarkable_indicator_ids: set[str] | None = None,
):
    """Publish `cohort` from one completed run of `workflow_id`. Raises PublishRefused."""
    run_id = run_id if run_id is not None else getattr(run, "id", None)
    # A run loads fine under a workflow_id that is not its own, and the mismatch
    # would be invisible: the state_key would be resolved from a FOREIGN
    # definition's contract and `source_workflow_id` -- the provenance an
    # anonymised figure's defensibility rests on -- would be written false.
    if run.definition_id and int(run.definition_id) != int(workflow_id):
        raise PublishRefused(
            f"Run {run_id} belongs to workflow {int(run.definition_id)}, not {workflow_id}. "
            "Publishing it under the wrong workflow would record false provenance."
        )
    if not run.is_completed:
        raise PublishRefused(f"Run {run_id} is not completed -- its figures are still moving and cannot be published.")

    # The graded payload a saved run stores is one level down from
    # `run.snapshot`, under `["state"][<state_key>]` (see
    # `workflow/snapshot_builders.wrap_for_runner`); `state_key` is spec-driven
    # per workflow and defaults to "snapshot".
    definition = wda.get_definition(workflow_id)
    state_key = "snapshot"
    if definition is not None:
        contract = resolve_snapshot_contract(definition)
        if contract.get("ok"):
            state_key = (contract.get("snapshot_inputs") or {}).get("state_key") or "snapshot"

    state = (run.snapshot or {}).get("state") or {}
    graded_payload = state.get(state_key) or {}

    meta = graded_payload.get("meta") or {}
    registry_id = (meta.get("registry") or {}).get("registry_id")
    as_of = (
        meta.get("as_of")
        or (str(run.period_end)[:10] if run.period_end else None)
        or (str(run.completed_at)[:10] if run.completed_at else None)
    )
    if not as_of:
        # `as_of` is NOT NULL on BenchmarkPublication; without this the refusal is
        # a raw IntegrityError naming a column.
        raise PublishRefused(
            f"Run {run_id} carries no as-of date: its snapshot has no meta.as_of, and the run "
            "has neither a period_end nor a completed_at to fall back on. A publication must "
            "be dated, so there is nothing to publish."
        )

    # The SERIES comes from the workflow's completed runs, not from anything
    # inside the one being published: a saved run is one point of a trend.
    history = run_history(wda, workflow_id, state_key)

    return publish_benchmark(
        cohort,
        snapshot=graded_payload,
        history=history,
        source_workflow_id=workflow_id,
        source_run_id=run_id,
        registry_id=registry_id,
        as_of=as_of,
        published_by=published_by,
        benchmarkable_indicator_ids=benchmarkable_indicator_ids,
    )


def cohorts_following(workflow_id) -> list:
    """Cohorts that republish themselves when `workflow_id` saves a run."""
    if workflow_id is None:
        return []
    return list(BenchmarkCohort.objects.filter(auto_publish_on_completion=True, source_workflow_id=int(workflow_id)))


def publish_for_completed_run(wda, run, *, published_by: str = "auto") -> list[dict]:
    """Republish every cohort following this run's workflow. Best effort; returns a report."""
    workflow_id = getattr(run, "definition_id", None)
    report: list[dict] = []
    for cohort in cohorts_following(workflow_id):
        try:
            publication = publish_run(cohort, wda, int(workflow_id), run, published_by=published_by)
            report.append({"cohort_id": cohort.pk, "publication_id": publication.pk, "error": None})
        except Exception as exc:  # noqa: BLE001 -- one cohort must not cost the others, or the save
            logger.warning(
                "auto-publish of cohort %s from run %s failed", cohort.pk, getattr(run, "id", None), exc_info=True
            )
            report.append({"cohort_id": cohort.pk, "publication_id": None, "error": str(exc)})
    return report


def latest_completed_run(wda, workflow_id: int):
    """The newest completed run, by period end then completion time -- what the report opens on."""
    best, best_key = None, None
    for run in wda.list_runs(definition_id=workflow_id):
        if not getattr(run, "is_completed", False):
            continue
        key = (str(run.period_end or run.completed_at or "")[:10], str(run.completed_at or ""))
        if best_key is None or key > best_key:
            best, best_key = run, key
    return best


def publish_latest_for_workflow(wda, workflow_id: int, *, published_by: str = "auto") -> list[dict]:
    """After a history rebuild: republish every following cohort from the newest run."""
    if not cohorts_following(workflow_id):
        return []
    run = latest_completed_run(wda, workflow_id)
    if run is None:
        return []
    return publish_for_completed_run(wda, run, published_by=published_by)
