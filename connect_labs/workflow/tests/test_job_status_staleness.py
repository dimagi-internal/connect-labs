"""Tests for the server-side job-staleness gate (JOB_STALE_SECONDS).

This is the ONE authoritative check for "has this job's worker died mid-batch" --
see connect_labs.workflow.views.job_status_snapshot. A prior version measured
staleness purely from started_at (stamped once at job init, never refreshed),
which meant any job running longer than the window -- including a genuinely
healthy one -- read as dead. active_job_age_seconds now prefers a heartbeat
(updated_at, refreshed on every progress tick) over started_at, so staleness
means "no progress in JOB_STALE_SECONDS", not "running longer than
JOB_STALE_SECONDS total".
"""
from datetime import datetime, timedelta
from unittest import mock

from connect_labs.workflow.views import JOB_STALE_SECONDS, active_job_age_seconds, job_status_snapshot


def _iso(dt):
    return dt.isoformat()


def test_active_job_age_seconds_prefers_updated_at_over_started_at():
    now = datetime.now()
    active_job = {
        "started_at": _iso(now - timedelta(hours=2)),  # long-running, but...
        "updated_at": _iso(now - timedelta(seconds=30)),  # ...still ticking
    }
    age = active_job_age_seconds(active_job)
    assert age < 60  # measured from the recent heartbeat, not the 2-hour-old start


def test_active_job_age_seconds_falls_back_to_started_at_without_a_heartbeat():
    """A job whose first progress tick hasn't landed yet (or one persisted
    before updated_at existed) has no heartbeat -- fall back to started_at."""
    now = datetime.now()
    active_job = {"started_at": _iso(now - timedelta(minutes=5))}
    age = active_job_age_seconds(active_job)
    assert 290 < age < 310  # ~5 minutes


def test_active_job_age_seconds_returns_none_for_unparseable_timestamp():
    assert active_job_age_seconds({"updated_at": "not-a-date"}) is None
    assert active_job_age_seconds({}) is None
    assert active_job_age_seconds(None) is None


def test_a_long_running_job_with_a_recent_heartbeat_is_not_reported_failed():
    """The core regression this fixes: a batch that has genuinely been running
    for well over JOB_STALE_SECONDS (dual-track batches routinely take
    15-20+ minutes) must NOT be reported as a dead zombie as long as its
    heartbeat is recent."""
    now = datetime.now()
    active_job = {
        "status": "running",
        "job_id": "task-abc",
        "started_at": _iso(now - timedelta(seconds=JOB_STALE_SECONDS + 600)),  # well past the window
        "updated_at": _iso(now - timedelta(seconds=5)),  # but ticking 5s ago
    }
    with mock.patch("celery.result.AsyncResult") as mock_async_result:
        mock_async_result.return_value.state = "PROGRESS"
        mock_async_result.return_value.info = {"message": "Still working..."}
        snapshot = job_status_snapshot("task-abc", active_job)
    assert snapshot["status"] != "failed"


def test_a_running_job_whose_heartbeat_has_gone_stale_is_reported_failed():
    """The other half of the same fix: if progress genuinely stops (the worker
    was killed mid-batch), the job must still be caught as a zombie -- just
    measured from the last heartbeat, not total elapsed time."""
    now = datetime.now()
    active_job = {
        "status": "running",
        "job_id": "task-abc",
        "started_at": _iso(now - timedelta(seconds=JOB_STALE_SECONDS + 600)),
        "updated_at": _iso(now - timedelta(seconds=JOB_STALE_SECONDS + 60)),  # heartbeat itself is stale
    }
    snapshot = job_status_snapshot("task-abc", active_job)
    assert snapshot["status"] == "failed"
    assert "didn't finish" in snapshot["error"]
