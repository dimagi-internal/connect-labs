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


@celery_app.task(bind=True)
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
