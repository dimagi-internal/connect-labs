"""Shared reading of a workflow run's ``active_job`` heartbeat.

Lifted out of ``connect_labs.workflow.views`` (which re-exports both names for
its existing importers) so that the callers who must judge "is this job still
alive?" identically — the run page, the resume path in
``connect_labs.workflow.audit_generation``, and the stale-run sweep — share ONE
definition instead of each growing its own threshold that can silently drift
from what the UI tells a human.
"""

from __future__ import annotations

import datetime as _dt
import os as _os
import socket as _socket


def active_job_age_seconds(active_job):
    """Seconds since ``active_job``'s last heartbeat, or ``None`` if unknown.

    Prefers ``updated_at`` (stamped by every progress tick — see
    ``progress_callback`` in ``connect_labs/workflow/tasks.py``) over
    ``started_at`` (stamped once at job init and never refreshed). A job that's
    still genuinely working keeps refreshing its heartbeat, so staleness is
    "no progress in JOB_STALE_SECONDS", not "running longer than
    JOB_STALE_SECONDS total" — a real batch that legitimately takes longer
    than the window isn't falsely killed as long as it's still ticking.
    ``started_at`` remains the fallback for a job whose first progress tick
    hasn't landed yet, or one persisted before this field existed.
    """
    from datetime import datetime

    aj = active_job or {}
    reference = aj.get("updated_at") or aj.get("started_at")
    if not reference:
        return None
    try:
        parsed = datetime.fromisoformat(reference)
    except (ValueError, TypeError):
        return None
    return (datetime.now() - parsed).total_seconds()


# A running job whose active_job hasn't advanced (no heartbeat, see
# active_job_age_seconds) in this long is treated as dead (its worker stopped
# without writing a terminal status -- a deploy cutover, a crash). This is the
# ONE authoritative threshold: job_status_snapshot and JobStatusStreamView
# below both gate on it, and it's what actually decides whether a reconnect
# shows real progress or "the server job stopped" -- a template's own
# reconnect-on-mount logic (see weekly_dual_track_audit.py) only decides
# whether to attempt reconnecting at all; this constant decides what the
# reconnect actually reports on its first poll. Real dual-track batches
# (extraction + AI review + duplicate detection across two tracks) routinely
# run 15-20+ minutes per CloudWatch — 15 minutes made every healthy run look
# dead. 45 minutes gives real long-tail runs headroom while still catching an
# actual zombie in a reasonable time.
JOB_STALE_SECONDS = 45 * 60


# Identity of THIS worker process, fixed at import and therefore changing on
# every process (re)start. A job stamps it while running so a later sweep can
# tell "the process that owned this job is gone" (it was killed out from under
# the job) apart from "the process is still here and the job stopped moving"
# (it hung). Those two deserve opposite treatment, and nothing else in the
# job's recorded state distinguishes them: an abrupt kill leaves exactly the
# same "running" record as a hang.
# Computed at IMPORT, not on first use: in a celery worker this module loads
# during startup, so it records when the process began rather than whenever it
# first happened to run a job. The difference matters -- the whole comparison
# below is "did this process start after that job went quiet?", and a boot
# stamp taken late would excuse jobs the restart could not possibly have ended.
_WORKER_BOOT_AT = _dt.datetime.now().isoformat()
_WORKER_ID = f"{_socket.gethostname()}:{_os.getpid()}:{_WORKER_BOOT_AT}"


def worker_identity() -> tuple[str, str]:
    """``(worker_id, worker_boot_at_iso)`` for the current process."""
    return _WORKER_ID, _WORKER_BOOT_AT


def job_died_with_its_worker(active_job) -> bool:
    """Was this job's process killed out from under it, rather than hanging?

    True when BOTH hold:

    - the job was stamped by a DIFFERENT process than the one asking (so the
      owner is not this process), and
    - this process booted AFTER the job's last heartbeat — i.e. a restart
      happened at or after the moment the job went quiet, which is what a
      deploy cutover looks like from the inside (``deploy-labs.yml`` stops
      every task with no drain, so whatever was running dies mid-step).

    The second condition is what keeps a genuine hang from being excused: a job
    that stopped ticking inside a process that is STILL RUNNING was not killed
    by anything, and a worker that booted BEFORE the job went quiet cannot have
    been the thing that ended it.

    Conservative by construction — when the stamps are missing (a job from
    before this existed) or unparseable, it returns False, so an unclear case
    is treated as the job's own failure rather than silently excused.

    Caveat: with more than one worker task, the sweep may run on a worker that
    is older than the job's own, and this returns False even though the job's
    worker really was replaced. That misattribution costs a charged retry —
    exactly the behaviour before this existed — never a wrongly-excused one.
    """
    from datetime import datetime

    aj = active_job or {}
    job_worker_id = aj.get("worker_id")
    if not job_worker_id:
        return False

    current_id, current_boot_at = worker_identity()
    if job_worker_id == current_id:
        return False  # our own process — it was never restarted

    reference = aj.get("updated_at") or aj.get("started_at")
    if not reference:
        return False
    try:
        last_heartbeat = datetime.fromisoformat(reference)
        booted = datetime.fromisoformat(current_boot_at)
    except (ValueError, TypeError):
        return False
    return booted >= last_heartbeat
