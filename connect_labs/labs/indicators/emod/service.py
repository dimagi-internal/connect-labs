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
# Expected whole-run duration by instance state when the run is submitted.
PMC_RUN_EXPECTED_WARM_S = 120
PMC_RUN_EXPECTED_COLD_S = PMC_RUN_ETA_COLD_S
# At most this many runs may be queued or running at once; further new work is refused (429).
PMC_MAX_IN_FLIGHT = 3
# A queued/running row untouched this long is a corpse, not load.
PMC_IN_FLIGHT_FRESH_S = 3600
MAX_SEEDS = 6


def run_eta(run) -> int:
    """Seconds left: the run's expected duration minus time since it was last queued or claimed (floor 10).

    The expected duration is recorded at submit time (``timings["expected_s"]``: warm or cold instance,
    plus any runs ahead of it); a row without one is estimated at the cold figure.
    """
    from connect_labs.labs.indicators.models import PmcModelRun

    if run.status in (PmcModelRun.COMPLETED, PmcModelRun.FAILED):
        return 0
    expected = (run.timings or {}).get("expected_s") or PMC_RUN_ETA_COLD_S
    elapsed = (timezone.now() - run.updated_at).total_seconds()
    return max(10, int(expected - elapsed))


def _instance_is_warm() -> bool:
    """True when the on-demand instance is running. Any AWS or config error reads as cold; never raises."""
    try:
        from connect_labs.labs.indicators.emod import runner

        # No public status call in the SDK; _state() is a single describe_instances.
        return runner.default_instance()._state() == "running"
    except Exception:  # noqa: BLE001 - an estimate must never fail a submit
        return False


def _in_flight(exclude_pk=None):
    from connect_labs.labs.indicators.models import PmcModelRun

    qs = PmcModelRun.objects.filter(
        status__in=[PmcModelRun.QUEUED, PmcModelRun.RUNNING],
        updated_at__gte=timezone.now() - timedelta(seconds=PMC_IN_FLIGHT_FRESH_S),
    )
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


def expected_duration_s(exclude_pk=None) -> int:
    """How long a run submitted now should take: its own run (warm or cold) plus the runs ahead of it."""
    ahead = list(_in_flight(exclude_pk))
    # Runs ahead mean the instance is up (or coming up for them), so this one runs warm after them.
    if ahead:
        return PMC_RUN_EXPECTED_WARM_S + sum(run_eta(r) for r in ahead)
    return PMC_RUN_EXPECTED_WARM_S if _instance_is_warm() else PMC_RUN_EXPECTED_COLD_S


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


def submit_run(state, schedules, seeds=None) -> tuple[int, dict]:
    """Start (or find) the run for ``state`` and ``schedules``. Returns ``(http_status, payload)``.

    200 cached result; 202 queued or running; 400 ``{"error"}`` for a request the model cannot take
    (including a state outside its fit); 429 ``{"error", "busy": true}`` when PMC_MAX_IN_FLIGHT other
    runs are already queued or running (joining an in-flight run of the same inputs is always allowed);
    503 ``{"error": "live model unavailable: ..."}`` when this deploy has no worker configured.
    """
    from connect_labs.labs.indicators.emod import runner, tasks
    from connect_labs.labs.indicators.models import PmcModelRun

    if not isinstance(state, str) or not state.strip():
        return 400, {"error": "state is required"}
    if not isinstance(schedules, list) or not all(isinstance(s, dict) for s in schedules):
        return 400, {"error": "schedules must be a list of objects"}
    if seeds is None:
        seeds = runner.DEFAULT_SEEDS
    if isinstance(seeds, int) and not isinstance(seeds, bool) and seeds > MAX_SEEDS:
        return 400, {"error": f"seeds must be at most {MAX_SEEDS}"}
    try:
        req = runner.build_request(state.strip(), schedules, seeds)
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

    existing = PmcModelRun.objects.filter(inputs_hash=runner.request_hash(req)).first()
    joining = existing is not None and existing.status in (PmcModelRun.QUEUED, PmcModelRun.RUNNING)
    if not joining and _in_flight().count() >= PMC_MAX_IN_FLIGHT:
        return 429, {
            "error": "The live model is busy with other runs; try again in a few minutes.",
            "busy": True,
        }

    run, created = runner.get_or_create_run(req)
    if run.status == PmcModelRun.COMPLETED:  # finished between the lookup and here
        return 200, _cached_payload(run)
    enqueue = created
    if created or run.status == PmcModelRun.FAILED:
        run.timings = {**(run.timings or {}), "expected_s": expected_duration_s(run.pk)}
        PmcModelRun.objects.filter(pk=run.pk).update(timings=run.timings)
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
