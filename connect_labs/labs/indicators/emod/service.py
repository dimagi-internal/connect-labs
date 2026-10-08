"""Start-or-fetch a live EMOD run, shared by the HTTP views and the agent tools.

``submit_run`` is the one place that decides what a request for (state, schedules) means: serve
the cached answer, leave an in-flight run alone, re-queue a failed one, or create and enqueue a
new one. Callers turn the returned ``(status_code, payload)`` into a response; the HTTP views
return it as JSON and the MCP tools translate it for the agent.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

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
# A queued row untouched this long is a corpse, not load. (A running row is a corpse once it misses
# three heartbeats: runner.STALE_RUNNING_AFTER_S.)
PMC_IN_FLIGHT_FRESH_S = 3600
MAX_SEEDS = 6

#: What a visitor (page or agent) is told when a run fails. The row's ``error`` keeps the detail
#: (SDK messages name the instance, SSM output, settings) for logs and admins; it is never shown.
PUBLIC_UNAVAILABLE = "The live model is unavailable right now."
PUBLIC_BUSY = "The live model is busy with other runs; try again in a few minutes."
PUBLIC_BUSY_TIMEOUT = "The live model was busy with other runs for too long; try again in a few minutes."

WAIT_WARM = "about two minutes"
WAIT_COLD = "about five minutes -- the model server is starting up"
WAIT_BEHIND = "after the run ahead finishes"


def public_error(error: str | None) -> str:
    """The one sentence a visitor sees for a failed run, whatever its internal ``error`` says."""
    if (error or "").startswith("runner busy"):
        return PUBLIC_BUSY_TIMEOUT
    return PUBLIC_UNAVAILABLE


def wait_phrase(run) -> str:
    """How long to tell someone a run will take, from the instance state recorded at submit.

    Keyed on ``timings.expected_s`` (warm vs cold instance), not the ticking ``eta_s``: a cold
    start is said once, as a cold start, rather than drifting into 'two minutes' as it counts down.
    """
    from connect_labs.labs.indicators.models import PmcModelRun

    t = run.timings or {}
    expected = t.get("expected_s") or PMC_RUN_EXPECTED_COLD_S
    phrase = WAIT_WARM if expected <= PMC_RUN_EXPECTED_WARM_S else WAIT_COLD
    if run.status == PmcModelRun.QUEUED and t.get("queued_behind_s"):
        phrase = f"{phrase}, {WAIT_BEHIND}"
    return phrase


def _started_ago_s(run) -> float:
    """Seconds since the run was claimed. The heartbeat moves ``updated_at``, so the claim's own
    ``started_at`` is the clock; rows claimed before it was recorded fall back to ``updated_at``."""
    started = (run.timings or {}).get("started_at")
    if started:
        return timezone.now().timestamp() - started
    return (timezone.now() - run.updated_at).total_seconds()


def _own_remaining_s(run) -> float:
    """Run time a run still needs on the instance (never its queue time): all of it if queued, the rest if running."""
    from connect_labs.labs.indicators.models import PmcModelRun

    expected = (run.timings or {}).get("expected_s") or PMC_RUN_ETA_COLD_S
    if run.status == PmcModelRun.RUNNING:
        return max(0.0, expected - _started_ago_s(run))
    return float(expected)


def run_eta(run) -> int:
    """Seconds left (floor 10).

    Recorded at submit in ``timings``: ``expected_s`` (this run's OWN duration, warm or cold),
    ``queued_behind_s`` (the run time of the runs ahead of it) and ``submitted_at`` (epoch).
    A queued row waits ``queued_behind_s`` then runs ``expected_s``, measured from submit; a running
    row has ``expected_s`` minus the time since it was claimed (``timings.started_at``).
    Rows without these fields are estimated at the cold figure from their last touch.
    """
    from connect_labs.labs.indicators.models import PmcModelRun

    if run.status in (PmcModelRun.COMPLETED, PmcModelRun.FAILED):
        return 0
    t = run.timings or {}
    expected = t.get("expected_s") or PMC_RUN_ETA_COLD_S
    now = timezone.now()
    if run.status == PmcModelRun.QUEUED and t.get("submitted_at"):
        elapsed = now.timestamp() - t["submitted_at"]
        return max(10, int(t.get("queued_behind_s", 0) + expected - elapsed))
    return max(10, int(expected - _started_ago_s(run)))


def _instance_is_warm() -> bool:
    """True when the on-demand instance is running. Any AWS or config error reads as cold; never raises."""
    try:
        from connect_labs.labs.indicators.emod import runner

        # No public status call in the SDK; _state() is a single describe_instances.
        return runner.default_instance()._state() == "running"
    except Exception:  # noqa: BLE001 - an estimate must never fail a submit
        return False


def _in_flight(exclude_pk=None):
    from django.db.models import Q

    from connect_labs.labs.indicators.emod import runner
    from connect_labs.labs.indicators.models import PmcModelRun

    now = timezone.now()
    qs = PmcModelRun.objects.filter(
        Q(status=PmcModelRun.QUEUED, updated_at__gte=now - timedelta(seconds=PMC_IN_FLIGHT_FRESH_S))
        | Q(status=PmcModelRun.RUNNING, updated_at__gte=now - timedelta(seconds=runner.STALE_RUNNING_AFTER_S))
    )
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


def expected_duration_s(exclude_pk=None) -> tuple[int, int]:
    """``(own duration, run time of the runs ahead)`` for a run submitted now."""
    ahead = list(_in_flight(exclude_pk))
    # Runs ahead mean the instance is up (or coming up for them), so this one runs warm after them.
    if ahead:
        return PMC_RUN_EXPECTED_WARM_S, int(sum(_own_remaining_s(r) for r in ahead))
    return (PMC_RUN_EXPECTED_WARM_S if _instance_is_warm() else PMC_RUN_EXPECTED_COLD_S), 0


def status_payload(run) -> dict:
    """The raw status of one run for the HTTP endpoint. ``error`` is the public sentence only."""
    from connect_labs.labs.indicators import pmc
    from connect_labs.labs.indicators.emod import live
    from connect_labs.labs.indicators.models import PmcModelRun

    in_flight = run.status in (PmcModelRun.QUEUED, PmcModelRun.RUNNING)
    return {
        "run_id": run.pk,
        "status": run.status,
        "cached": False,
        "label": live.LABEL,
        "result": run.result if run.status == PmcModelRun.COMPLETED else None,
        "error": public_error(run.error) if run.status == PmcModelRun.FAILED else "",
        "timings": run.timings,
        "eta_s": run_eta(run),
        "wait": wait_phrase(run) if in_flight else None,
        "caveats": list(pmc.CAVEATS),
    }


def _cached_payload(run) -> dict:
    return {"run_id": run.pk, "status": run.status, "cached": True, "result": run.result, "timings": run.timings}


def submit_run(state, schedules, seeds=None) -> tuple[int, dict]:
    """Start (or find) the run for ``state`` and ``schedules``. Returns ``(http_status, payload)``.

    200 cached result; 202 queued or running; 400 ``{"error"}`` for a request the model cannot take
    (including a state outside its fit); 429 ``{"error", "busy": true}`` when PMC_MAX_IN_FLIGHT other
    runs are already queued or running (joining an in-flight run of the same inputs is always allowed);
    503 ``{"error": PUBLIC_UNAVAILABLE, "unavailable": true}`` when this deploy has no worker
    configured (the detail goes to the log, not the response).
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
        logger.warning("live model unavailable: %s", exc)
        return 503, {"error": PUBLIC_UNAVAILABLE, "unavailable": True}

    existing = PmcModelRun.objects.filter(inputs_hash=runner.request_hash(req)).first()
    joining = existing is not None and existing.status in (PmcModelRun.QUEUED, PmcModelRun.RUNNING)
    if not joining and _in_flight().count() >= PMC_MAX_IN_FLIGHT:
        return 429, {"error": PUBLIC_BUSY, "busy": True}

    run, created = runner.get_or_create_run(req)
    if run.status == PmcModelRun.COMPLETED:  # finished between the lookup and here
        return 200, _cached_payload(run)
    enqueue = created
    now = timezone.now()
    # A running row that has missed three heartbeats lost its worker (killed mid-flight): re-queue it.
    dead = run.status == PmcModelRun.RUNNING and run.updated_at < now - timedelta(seconds=runner.STALE_RUNNING_AFTER_S)
    if created or dead or run.status == PmcModelRun.FAILED:
        own, behind = expected_duration_s(run.pk)
        run.timings = {
            **(run.timings or {}),
            "expected_s": own,
            "queued_behind_s": behind,
            "submitted_at": timezone.now().timestamp(),
        }
        PmcModelRun.objects.filter(pk=run.pk).update(timings=run.timings)
    # updated_at is set explicitly: queryset.update() skips auto_now. Only the request whose
    # filtered update matches enqueues.
    if dead:
        # The same staleness test in the filter, so two requests racing here enqueue once. The new
        # task waits out the dead worker's lease (at most its 300 s TTL) and its claim takes the row.
        enqueue = bool(
            PmcModelRun.objects.filter(
                pk=run.pk,
                status=PmcModelRun.RUNNING,
                updated_at__lt=now - timedelta(seconds=runner.STALE_RUNNING_AFTER_S),
            ).update(status=PmcModelRun.QUEUED, error="", updated_at=now)
        )
        run.status = PmcModelRun.QUEUED
    elif run.status == PmcModelRun.FAILED:
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
    return 202, {"run_id": run.pk, "status": run.status, "cached": False, "wait": wait_phrase(run)}
