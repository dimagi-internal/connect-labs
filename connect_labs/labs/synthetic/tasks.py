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

import logging
from typing import Any

from django.conf import settings

from config import celery_app

logger = logging.getLogger(__name__)

# Every profiling task below is declared acks_late + reject_on_worker_lost.
#
# Celery's default acks a task when the worker RECEIVES it, so a worker that dies
# mid-run takes the job with it: the broker considers it delivered, nothing is
# redelivered, and the last update_state sticks — the caller polls PROGRESS
# forever on a job nobody is running. That is worse than the inline failure this
# module replaced, which at least surfaced an error.
#
# Not hypothetical. Opp 874's first queued run was received at 13:39:07 and lost
# when the worker restarted underneath it at 13:45:40; its status still read
# "PROGRESS 4/6" forty minutes later. These are ten-plus-minute jobs, so they are
# far likelier than a short task to be in flight across a deploy's rolling
# restart.
#
# Safe because profiling is idempotent: it reads production and writes a bundle
# keyed by opportunity id, so a redelivered run overwrites its own output rather
# than duplicating anything. acks_late buys at-least-once delivery, and
# at-least-once is only a hazard when re-running has side effects. Here it does
# not.
TASK_OPTS = {"acks_late": True, "reject_on_worker_lost": True}


@celery_app.task(bind=True, **TASK_OPTS)
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


@celery_app.task(bind=True, **TASK_OPTS)
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


@celery_app.task(bind=True, **TASK_OPTS)
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


@celery_app.task(bind=True, **TASK_OPTS)
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
