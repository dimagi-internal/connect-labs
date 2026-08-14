"""Shared reading of a workflow run's ``active_job`` heartbeat.

Lifted out of ``connect_labs.workflow.views`` (which re-exports both names for
its existing importers) so that the callers who must judge "is this job still
alive?" identically — the run page, the resume path in
``connect_labs.workflow.audit_generation``, and the stale-run sweep — share ONE
definition instead of each growing its own threshold that can silently drift
from what the UI tells a human.
"""

from __future__ import annotations


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
