"""Start-or-fetch a live EMOD run, shared by the HTTP views and the agent tools.

``submit_run`` is the one place that decides what a request for (state, schedules) means: serve
the cached answer, leave an in-flight run alone, re-queue a failed one, or create and enqueue a
new one. Callers turn the returned ``(status_code, payload)`` into a response; the HTTP views
return it as JSON and the MCP tools translate it for the agent.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

# Rough whole-run estimate for the status endpoint: a cold start (instance boot plus the
# simulation) measured about 300 s, a warm instance about 110 s. Per-run cold/warm is only known
# once a run finishes, so a run in flight is estimated at the cold figure.
PMC_RUN_ETA_COLD_S = 300
# A run still 'queued' this long after its last touch is assumed to have lost its enqueue.
PMC_RUN_REQUEUE_AFTER_S = 120


def run_eta(run) -> int:
    """Seconds left: the cold estimate minus time since the run was last queued or claimed (floor 10)."""
    from connect_labs.labs.indicators.models import PmcModelRun

    if run.status in (PmcModelRun.COMPLETED, PmcModelRun.FAILED):
        return 0
    elapsed = (timezone.now() - run.updated_at).total_seconds()
    return max(10, int(PMC_RUN_ETA_COLD_S - elapsed))


def status_payload(run) -> dict:
    from connect_labs.labs.indicators.models import PmcModelRun

    return {
        "run_id": run.pk,
        "status": run.status,
        "cached": False,
        "result": run.result if run.status == PmcModelRun.COMPLETED else None,
        "error": run.error,
        "timings": run.timings,
        "eta_s": run_eta(run),
    }


def _cached_payload(run) -> dict:
    return {"run_id": run.pk, "status": run.status, "cached": True, "result": run.result, "timings": run.timings}


def submit_run(state, schedules) -> tuple[int, dict]:
    """Start (or find) the run for ``state`` and ``schedules``. Returns ``(http_status, payload)``.

    200 cached result; 202 queued or running; 400 ``{"error"}`` for a request the model cannot take
    (including a state outside its fit); 503 ``{"error": "live model unavailable: ..."}`` when this
    deploy has no worker configured.
    """
    from connect_labs.labs.indicators.emod import runner, tasks
    from connect_labs.labs.indicators.models import PmcModelRun

    if not isinstance(state, str) or not state.strip():
        return 400, {"error": "state is required"}
    if not isinstance(schedules, list) or not all(isinstance(s, dict) for s in schedules):
        return 400, {"error": "schedules must be a list of objects"}
    try:
        req = runner.build_request(state.strip(), schedules)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except (TypeError, KeyError, AttributeError) as exc:
        return 400, {"error": f"malformed schedules: {exc}"}
    # A finished answer is served even if this deploy has no worker configured.
    done = PmcModelRun.objects.filter(inputs_hash=runner.request_hash(req), status=PmcModelRun.COMPLETED).first()
    if done is not None:
        return 200, _cached_payload(done)
    try:
        runner.default_instance()
        runner.default_bucket()
    except RuntimeError as exc:
        return 503, {"error": f"live model unavailable: {exc}"}

    run, created = runner.get_or_create_run(req)
    if run.status == PmcModelRun.COMPLETED:  # finished between the lookup and here
        return 200, _cached_payload(run)
    enqueue = created
    now = timezone.now()
    # updated_at is set explicitly: queryset.update() skips auto_now. Only the request whose
    # filtered update matches enqueues.
    if run.status == PmcModelRun.FAILED:
        # Failures are not cached: re-queue the same row.
        enqueue = bool(
            PmcModelRun.objects.filter(pk=run.pk, status=PmcModelRun.FAILED).update(
                status=PmcModelRun.QUEUED, error="", updated_at=now
            )
        )
        run.status = PmcModelRun.QUEUED
    elif run.status == PmcModelRun.QUEUED and not created:
        # Queued but untouched for a while: the enqueue was probably lost (broker blip). Retry it.
        enqueue = bool(
            PmcModelRun.objects.filter(
                pk=run.pk,
                status=PmcModelRun.QUEUED,
                updated_at__lt=now - timedelta(seconds=PMC_RUN_REQUEUE_AFTER_S),
            ).update(updated_at=now)
        )
    if enqueue:
        transaction.on_commit(lambda pk=run.pk: tasks.run_pmc_model.delay(pk))
    return 202, {"run_id": run.pk, "status": run.status, "cached": False}
