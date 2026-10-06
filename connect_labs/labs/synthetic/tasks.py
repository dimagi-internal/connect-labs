"""Celery tasks for synthetic profiling.

Why this module exists: `docker/start` runs gunicorn with `--timeout 600`, a hard
cap on one request's wall time. Profiling a large opportunity exceeds it — opp 874
(11,581 visits) needs ~600s+ — so the worker is killed mid-call and the bundles it
had already written are abandoned along with the request (connect-labs#1581).
Nothing is wrong with the profiler; it never gets to finish.

Per-stage progress (#1220) could not fix that. Progress keeps a client's *idle*
timer alive, but gunicorn's is a total-duration cap: it fires whether or not bytes
are flowing. The only fix that removes the class is to stop doing the work inside
the request.

A worker has no such cap, so the job runs to completion and the caller polls. The
same shape audit creation already uses (`run_audit_creation`), including the
pre-generated task id so a result can be found before the task starts.
"""

import functools
import logging
from typing import Any

from django.conf import settings

from config import celery_app

logger = logging.getLogger(__name__)

# The worker is shared (audits, AI reviews, beat) and the project sets no global task
# time limit, so a profiling job that never finishes would hold its slot forever.
# opp 874 (11,581 visits) takes ~10 minutes; two hours is generous for a cohort.
PROFILE_SOFT_TIME_LIMIT = 2 * 3600
PROFILE_TIME_LIMIT = PROFILE_SOFT_TIME_LIMIT + 300

# At most this many synthetic jobs (profiling, generation, cloning) run at once across
# the whole system, whoever queued them. The worker's other slots stay free for
# audits and AI reviews however many people are cloning. A job that finds every slot
# taken waits on the queue (a Celery retry) rather than running anyway. The per-person
# limits are separate (connect_labs.mcp.profile_limits).
_SLOT_RETRY_SECONDS = 20
_SLOT_MAX_RETRIES = (2 * 3600) // _SLOT_RETRY_SECONDS


def _slots() -> int:
    return int(getattr(settings, "SYNTHETIC_JOB_SLOTS", 2))


def _acquire_slot(task_id: str) -> int | None:
    from django.core.cache import cache

    for i in range(_slots()):
        # cache.add is atomic: it sets the key only if no live job holds it. The TTL
        # outlives the hard time limit, so a worker killed mid-job frees its slot.
        if cache.add(f"synthetic:job-slot:{i}", task_id or "?", PROFILE_TIME_LIMIT + 60):
            return i
    return None


def _release_slot(slot: int, task_id: str) -> None:
    from django.core.cache import cache

    key = f"synthetic:job-slot:{slot}"
    if cache.get(key) == (task_id or "?"):
        cache.delete(key)


def holds_a_synthetic_slot(fn):
    """Run a bound task only while holding one of the system-wide synthetic slots."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        task_id = getattr(self.request, "id", None) or ""
        slot = _acquire_slot(task_id)
        if slot is None:
            raise self.retry(countdown=_SLOT_RETRY_SECONDS, max_retries=_SLOT_MAX_RETRIES)
        try:
            return fn(self, *args, **kwargs)
        finally:
            _release_slot(slot, task_id)

    return wrapper


@celery_app.task(bind=True, soft_time_limit=PROFILE_SOFT_TIME_LIMIT, time_limit=PROFILE_TIME_LIMIT)
@holds_a_synthetic_slot
def run_synthetic_profile_opp(
    self,
    *,
    source_opportunity_id: int,
    out_dir: str,
    oauth_token: str,
    curate: bool = False,
    mirror: bool = False,
) -> dict[str, Any]:
    """Profile one opportunity into a bundle. Returns the same dict the inline atom did.

    The OAuth token is passed in rather than re-derived: the worker has no request
    and no session, so the caller's credential has to travel with the job. It is
    used only to read the export endpoints and is never persisted — the bundle
    carries aggregate stats and program config, not credentials.
    """
    from connect_labs.labs.synthetic.bundle import make_bundle_store
    from connect_labs.labs.synthetic.clone_from_prod import profile_opp_to_bundle
    from connect_labs.labs.synthetic.gdrive import DriveClient

    def _progress(current, total, message=""):
        # Surfaced through AsyncResult.info so a poller can show real movement
        # rather than a spinner. update_state is cheap and the backend is Redis.
        self.update_state(
            state="PROGRESS",
            meta={
                "current": current,
                "total": total,
                "message": str(message),
                "source_opportunity_id": source_opportunity_id,
            },
        )

    logger.info("[SyntheticProfile] start opp=%s out=%s", source_opportunity_id, out_dir)
    drive = DriveClient() if str(out_dir).startswith("gdrive:") else None
    store = make_bundle_store(out_dir, drive=drive)
    handle = profile_opp_to_bundle(
        source_opportunity_id,
        curate=curate,
        mirror=mirror,
        base_url=settings.CONNECT_PRODUCTION_URL,
        oauth_token=oauth_token,
        store=store,
        progress=_progress,
    )
    resolved = f"gdrive:{store.root_folder_id}" if hasattr(store, "root_folder_id") else str(out_dir)
    logger.info("[SyntheticProfile] done opp=%s bundle=%s", source_opportunity_id, handle)
    return {
        "bundle_dir": str(handle),
        "bundle_root": resolved,
        "source_opportunity_id": source_opportunity_id,
    }


def _progress_reporter(task, **context):
    """A progress callback that reports through the task's own state.

    Surfaced via AsyncResult.info so a poller shows real movement rather than a
    spinner. Cheap: the result backend is Redis.
    """

    def _report(current, total, message=""):
        task.update_state(
            state="PROGRESS",
            meta={"current": current, "total": total, "message": str(message), **context},
        )

    return _report


@celery_app.task(bind=True, soft_time_limit=PROFILE_SOFT_TIME_LIMIT, time_limit=PROFILE_TIME_LIMIT)
@holds_a_synthetic_slot
def run_synthetic_profile_opps_bulk(
    self,
    *,
    source_opportunity_ids: list[int],
    out_dir: str,
    oauth_token: str,
    curate: bool = False,
    mirror: bool = False,
) -> dict[str, Any]:
    """Profile several opportunities into one bundle_root."""
    from connect_labs.labs.synthetic.clone_from_prod import profile_opps_bulk
    from connect_labs.labs.synthetic.gdrive import DriveClient

    drive = DriveClient() if str(out_dir).startswith("gdrive:") else None
    resolved, handles = profile_opps_bulk(
        source_opportunity_ids,
        curate=curate,
        mirror=mirror,
        base_url=settings.CONNECT_PRODUCTION_URL,
        oauth_token=oauth_token,
        bundle_root=out_dir,
        drive=drive,
        progress=_progress_reporter(self, opportunity_ids=source_opportunity_ids),
    )
    return {
        "bundle_root": resolved,
        "bundle_dirs": handles,
        "succeeded": len(handles),
        "requested": len(source_opportunity_ids),
    }


@celery_app.task(bind=True, soft_time_limit=PROFILE_SOFT_TIME_LIMIT, time_limit=PROFILE_TIME_LIMIT)
@holds_a_synthetic_slot
def run_synthetic_clone_profile(self, *, spec_yaml: str, oauth_token: str) -> dict[str, Any]:
    """Phase 1 for a whole cohort spec. The longest of these by far."""
    from connect_labs.labs.synthetic.clone_from_prod import profile_cohort
    from connect_labs.labs.synthetic.cohort import CohortSpec
    from connect_labs.labs.synthetic.gdrive import DriveClient

    spec = CohortSpec.from_yaml(spec_yaml)
    drive = DriveClient() if str(spec.bundle_root).startswith("gdrive:") else None
    spec = profile_cohort(
        spec,
        base_url=settings.CONNECT_PRODUCTION_URL,
        oauth_token=oauth_token,
        drive=drive,
        progress=_progress_reporter(self, opportunity_ids=spec.opportunity_ids),
    )
    return {
        "spec_yaml": spec.to_yaml(),
        "bundle_root": spec.bundle_root,
        "opportunity_ids": spec.opportunity_ids,
    }


@celery_app.task(bind=True, soft_time_limit=PROFILE_SOFT_TIME_LIMIT, time_limit=PROFILE_TIME_LIMIT)
@holds_a_synthetic_slot
def run_synthetic_profile_from_prod(
    self,
    *,
    opportunity_id: int,
    oauth_token: str,
    form_json_paths: list[str] | None = None,
    mirror: bool = False,
) -> dict[str, Any]:
    """Profile one opportunity straight to a manifest YAML (no bundle)."""
    from connect_labs.mcp.tools.synthetic import _profile_from_prod_inner

    return _profile_from_prod_inner(
        opportunity_id=opportunity_id,
        token=oauth_token,
        form_json_paths=form_json_paths,
        mirror=mirror,
        progress=_progress_reporter(self, opportunity_id=opportunity_id),
    )


@celery_app.task(bind=True, soft_time_limit=PROFILE_SOFT_TIME_LIMIT, time_limit=PROFILE_TIME_LIMIT)
@holds_a_synthetic_slot
def run_synthetic_tool(self, *, tool_name: str, arguments: dict, user_id: int, restricted: bool) -> Any:
    """Run a synthetic MCP tool's work on the worker, as the person who called it.

    Generation, cloning and fidelity scoring are CPU-heavy, and the web tier is
    three processes on one vCPU: run inline, one person's clone stalls everyone's
    page loads. The tool queues this instead and (by default) waits for it, so its
    reply is unchanged. ``restricted`` is the calling MCP request's answer, carried
    over because the worker has no request to read it from.
    """
    from connect_labs.mcp.tools.synthetic import run_on_this_worker
    from connect_labs.mcp.visit_access import restricted_job
    from connect_labs.users.models import User

    user = User.objects.get(pk=user_id)
    with restricted_job(restricted):
        return run_on_this_worker(tool_name, user, arguments, progress=_progress_reporter(self, tool=tool_name))


@celery_app.task(bind=True, soft_time_limit=PROFILE_SOFT_TIME_LIMIT, time_limit=PROFILE_TIME_LIMIT)
@holds_a_synthetic_slot
def run_synthetic_clone_opp(
    self,
    *,
    source_opportunity_ids: list[int],
    program_name: str,
    case_timelines: bool,
    oauth_token: str,
    user_id: int,
    restricted: bool,
) -> dict[str, Any]:
    """synthetic_clone_opp: profile every source, then generate a clone of each.

    One job, so a person asks for a clone once and polls once. Access to every
    source was checked when the job was queued; the clones are written only onto
    labs-only ids the person may write (the same authorizer synthetic_clone_generate
    uses), and they are made visible to their creator.
    """
    from connect_labs.labs.synthetic.access import labs_only_target_denied_reason
    from connect_labs.labs.synthetic.clone_from_prod import generate_cohort, profile_cohort
    from connect_labs.labs.synthetic.cohort import CohortSpec
    from connect_labs.labs.synthetic.gdrive import DriveClient
    from connect_labs.mcp.visit_access import restricted_job
    from connect_labs.users.models import User

    user = User.objects.get(pk=user_id)
    progress = _progress_reporter(self, opportunity_ids=source_opportunity_ids)
    drive = DriveClient()
    spec = CohortSpec(
        opportunity_ids=[int(x) for x in source_opportunity_ids],
        program_name=program_name,
        org_name="Labs Synthetic",
        bundle_root="gdrive:",
        mirror=case_timelines,
    )

    def authorize(opp_id):
        reason = labs_only_target_denied_reason(user, opp_id)
        if reason:
            raise PermissionError(reason)

    with restricted_job(restricted):
        progress(0, 2, "Step 1 of 2: measuring the real opportunities")
        spec = profile_cohort(
            spec, base_url=settings.CONNECT_PRODUCTION_URL, oauth_token=oauth_token, drive=drive, progress=progress
        )
        progress(1, 2, "Step 2 of 2: generating the clones")
        spec, results = generate_cohort(spec, drive=drive, authorize=authorize, created_by=user, progress=progress)
    if not user.view_synthetic_opps:
        user.view_synthetic_opps = True
        user.save(update_fields=["view_synthetic_opps"])
    labs = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return {
        "program_id": spec.program_id,
        "program_name": spec.program_name,
        "clones": [
            {"source_opportunity_id": r.source_opportunity_id, "opportunity_id": r.opportunity_id}
            for r in results
            if not r.skipped
        ],
        "profile_bundles": spec.bundle_root,
        "how_to_open": (
            f"Open {labs}/labs/overview/ and pick the clone in the opportunity selector, under program "
            f"'{spec.program_name}'. Synthetic opportunities are now shown in your labs lists."
        ),
    }


def slot_holders() -> list[str | None]:
    """The task id holding each system-wide synthetic slot, or None where the slot is free."""
    from django.core.cache import cache

    return [cache.get(f"synthetic:job-slot:{i}") for i in range(_slots())]


def cancel_job(task_id: str) -> None:
    """Stop a synthetic job: revoke it (ending it if it is running), mark it revoked, free its slot.

    Marking it revoked in the result backend is what the per-person limits and the
    status page read, so a job lost in a worker restart -- queued forever as RETRY --
    stops counting against its owner at once.
    """
    from django.core.cache import cache

    celery_app.control.revoke(task_id, terminate=True)
    celery_app.backend.store_result(task_id, None, "REVOKED")
    for i in range(_slots()):
        key = f"synthetic:job-slot:{i}"
        if cache.get(key) == task_id:
            cache.delete(key)
