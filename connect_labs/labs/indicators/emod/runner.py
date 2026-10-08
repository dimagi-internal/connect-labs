"""Client for the on-demand EMOD worker: build a PMC scenario request, run it, keep the result.

The worker (``tools/pmc_emod/worker/run_scenarios.py``) lives on a stopped-when-idle EC2 box.
``execute`` writes the request to the artifacts bucket under ``requests/``, starts the box if
needed, runs the worker over SSM, and reads ``results/<hash>.json`` back. It is synchronous and
slow (minutes); callers run it from a Celery task.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import shlex
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from connect_labs.labs.indicators import pmc
from connect_labs.labs.indicators.models import PmcModelRun

logger = logging.getLogger(__name__)

#: The southern-Nigeria-like setting every live run uses until states are calibrated: exactly the
#: setting the precomputed grid was run with (``setting.model_inputs`` in pmc_emod_sweep.json, i.e.
#: PMC_POP=5000 PMC_LARVAL=6e7), so a live run starts from the grid's own burn-in and its effect is
#: comparable with the grid's rows. test_emod_runner.py fails if this drifts from the JSON.
#: ``larval_capacity`` must stay the float 6e7: the worker's burn-in cache key hashes its JSON text.
DEFAULT_SETTING = {
    "name": "SW_Nigeria_like",
    "larval_capacity": 6e7,
    "habitat_times": [0, 30, 60, 91, 122, 152, 182, 213, 243, 274, 304, 334, 365],
    "habitat_values": [1.0, 0.8, 1.0, 2.0, 4.0, 6.0, 6.0, 5.0, 6.0, 5.0, 3.0, 1.5, 1.0],
    "pop": 5000,
    "case_mgmt": 0.5,
    "net_coverage": 0.5,
}

#: The comparison every live request carries, so effects are always relative to no PMC.
BASELINE_SCHEDULE = {"code": "none", "rounds": []}
INTERVENTION_YEARS = 2
DEFAULT_SEEDS = 3

#: Command that runs the worker on the box (system python3 lacks the libraries).
WORKER_PYTHON = "/opt/emod/.venv/bin/python"
WORKER_SCRIPT = "/opt/emod/run_scenarios.py"

BOOT_TIMEOUT_S = 600
READY_TIMEOUT_S = 900
#: The worker's whole-request deadline (burn-in plus pick-ups), passed to it as
#: EMOD_REQUEST_TIMEOUT_S. Its worst case is a cold burn-in plus the pick-ups, so a single bound
#: on the request is what keeps a demo wait finite.
WORKER_REQUEST_TIMEOUT_S = 2100
# The SSM command limit: longer than the worker's own deadline, so the worker reports its own
# timeout (with a readable message) before SSM kills it.
RUN_TIMEOUT_S = 2400

#: While a task owns a row it touches ``updated_at`` this often (emod/tasks.py heartbeat).
ROW_HEARTBEAT_S = 60
#: A ``running`` row untouched for three heartbeats belongs to a dead worker (a cold-killed
#: Celery process): it may be re-queued and re-claimed.
STALE_RUNNING_AFTER_S = 3 * ROW_HEARTBEAT_S

FIT_REASONS = {
    "more_seasonal": "more seasonal than the modelled setting: SMC, not PMC",
    "less_seasonal": "less seasonal than the modelled setting",
    "unknown": "no prevalence or rainfall data to check fit against",
}


def state_fit(state: str) -> str:
    """How well a Nigerian state matches the modelled setting, by the same rule the explorer uses."""
    from connect_labs.labs.indicators import boundaries as boundary_set
    from connect_labs.labs.indicators.resolve import BulkResolver

    unit = boundary_set.owned().filter(iso_code=pmc.ISO, admin_level=1, name__iexact=state).first()
    if unit is None:
        raise ValueError(f"unknown state {state!r}")
    bulk = BulkResolver([unit])
    prevalence = bulk.get("malaria_prevalence", unit)
    wettest = bulk.get("rain_wettest_quarter", unit)
    return pmc.fit_for(prevalence.value if prevalence else None, wettest.value if wettest else None)


def build_request(state: str, schedules: list[dict], seeds: int = DEFAULT_SEEDS, *, fit: str | None = None) -> dict:
    """The worker request for ``schedules`` in ``state``.

    Until states are calibrated, a state that matches the modelled setting (``near`` or
    ``prevalence_differs``, the explorer's ranked fits) runs in the default southern setting;
    any other raises ValueError naming why. ``fit`` skips the registry lookup (tests).
    """
    if not isinstance(seeds, int) or isinstance(seeds, bool) or seeds < 1:
        raise ValueError("seeds must be an integer >= 1")
    if not schedules:
        raise ValueError("at least one schedule is required")
    fit = state_fit(state) if fit is None else fit
    if fit not in pmc.RANKED_FITS:
        raise ValueError(f"{state} cannot be modelled: {FIT_REASONS.get(fit, fit)}")
    codes = [s.get("code") for s in schedules]
    if BASELINE_SCHEDULE["code"] in codes:
        raise ValueError(f"schedule code {BASELINE_SCHEDULE['code']!r} is reserved for the no-PMC baseline")
    return {
        "setting": copy.deepcopy(DEFAULT_SETTING),
        "schedules": [copy.deepcopy(BASELINE_SCHEDULE)] + [copy.deepcopy(s) for s in schedules],
        "seeds": list(range(seeds)),
        "intervention_years": INTERVENTION_YEARS,
    }


def request_hash(req: dict) -> str:
    """sha256 of the canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(req, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_or_create_run(req: dict) -> tuple[PmcModelRun, bool]:
    return PmcModelRun.objects.get_or_create(inputs_hash=request_hash(req), defaults={"request": req})


def default_instance():
    """The configured on-demand instance, or a clear error when this deploy has none."""
    from canopy_sdk.ondemand import OnDemandInstance

    instance_id = getattr(settings, "LABS_EMOD_INSTANCE_ID", None)
    region = getattr(settings, "LABS_EMOD_REGION", None)
    if not instance_id or not region:
        raise RuntimeError("LABS_EMOD_INSTANCE_ID and LABS_EMOD_REGION must be set to run EMOD")
    return OnDemandInstance(instance_id, region, "emod")


def default_bucket() -> str:
    bucket = getattr(settings, "LABS_EMOD_BUCKET", None)
    if not bucket:
        raise RuntimeError("LABS_EMOD_BUCKET must be set to run EMOD")
    return bucket


def default_s3():
    import boto3

    return boto3.client("s3", region_name=getattr(settings, "LABS_EMOD_REGION", None))


def _claim(run: PmcModelRun, reclaim_stale_after_s: float | None) -> bool:
    """Atomically move a ``queued`` row (or, with ``reclaim_stale_after_s``, a ``running`` row
    untouched that long) to running. False for anything else: another worker owns it, it already
    completed, or it failed (the service re-queues a failed row before it enqueues a task).

    The claim records ``started_at`` (epoch) in timings: the run-time ETA counts from it, because
    the heartbeat keeps moving ``updated_at``.
    """
    now = timezone.now()
    claimable = Q(status=PmcModelRun.QUEUED)
    if reclaim_stale_after_s is not None:
        claimable |= Q(status=PmcModelRun.RUNNING, updated_at__lt=now - timedelta(seconds=reclaim_stale_after_s))
    claimed = (
        PmcModelRun.objects.filter(pk=run.pk)
        .filter(claimable)
        .update(status=PmcModelRun.RUNNING, error="", result=None, completed_at=None, updated_at=now)
    )
    if claimed:
        run.refresh_from_db()
        run.timings = {**(run.timings or {}), "started_at": now.timestamp()}
        PmcModelRun.objects.filter(pk=run.pk).update(timings=run.timings)
    return bool(claimed)


def execute(run: PmcModelRun, instance, bucket: str, s3=None, reclaim_stale_after_s: float | None = None) -> None:
    """Run ``run.request`` on the worker and store the outcome on ``run``.

    The row is claimed atomically first and only a ``queued`` row is taken: a run another worker
    holds (status ``running``) is left alone and this returns at once, unless
    ``reclaim_stale_after_s`` is given and the row has not been touched for that long (its worker
    died). A completed or failed row is never re-run here; re-queue a failed one first.

    A failed run does not raise: the failure is recorded on the row (status ``failed`` with the
    message) so a Celery task can finish cleanly and the caller can read it.
    """
    from canopy_sdk.ondemand import OnDemandError

    if not bucket:
        raise RuntimeError("LABS_EMOD_BUCKET must be set to run EMOD")
    if not _claim(run, reclaim_stale_after_s):
        logger.info("EMOD run %s is already running; leaving it", run.inputs_hash[:12])
        return
    s3 = s3 or default_s3()
    key = run.inputs_hash
    request_key, result_key = f"requests/{key}.json", f"results/{key}.json"
    timings: dict[str, float] = {}
    started = time.monotonic()
    try:
        s3.put_object(
            Bucket=bucket,
            Key=request_key,
            Body=json.dumps(run.request, sort_keys=True).encode(),
            ContentType="application/json",
        )
        up = instance.ensure_running(boot_timeout_s=BOOT_TIMEOUT_S, ready_timeout_s=READY_TIMEOUT_S)
        timings.update({f"boot_{k}": v for k, v in up.timings.items()})
        timings["cold_start"] = bool(up.cold)
        t = time.monotonic()
        command = (
            f"EMOD_REQUEST_TIMEOUT_S={WORKER_REQUEST_TIMEOUT_S} {WORKER_PYTHON} {WORKER_SCRIPT} "
            f"--request {shlex.quote(f's3://{bucket}/{request_key}')} "
            f"--out {shlex.quote(f's3://{bucket}/{result_key}')}"
        )
        res = instance.run(
            [command],
            timeout_s=RUN_TIMEOUT_S,
        )
        timings["worker_s"] = round(time.monotonic() - t, 1)
        if not res.ok:
            tail = (res.stderr or res.stdout or "").strip()[-1500:]
            raise OnDemandError(f"worker exited {res.exit_code} ({res.status}): {tail}")
        body = s3.get_object(Bucket=bucket, Key=result_key)["Body"].read()
        run.result = json.loads(body)
        run.status = PmcModelRun.COMPLETED
    except Exception as exc:  # noqa: BLE001 - recorded on the row, including SDK errors like InstanceGone
        logger.warning("EMOD run %s failed: %s", key[:12], exc)
        run.status = PmcModelRun.FAILED
        run.error = str(exc) or exc.__class__.__name__
    timings["total_s"] = round(time.monotonic() - started, 1)
    run.timings = timings
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "result", "error", "timings", "completed_at"])
