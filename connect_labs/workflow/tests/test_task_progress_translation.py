"""Tests for the single Celery-meta -> UI-progress translation and the poll-first
job-status snapshot. These used to be copy-pasted in four places; the point of
these tests is that the ONE shared translation covers every state consistently,
and that the JSON poll endpoint's snapshot trusts run-state over Celery.
"""

from connect_labs.labs.analysis.sse_streaming import build_task_progress
from connect_labs.workflow.views import job_status_snapshot


class TestBuildTaskProgress:
    def test_pending(self):
        assert build_task_progress("PENDING", None)["status"] == "pending"

    def test_progress_carries_counts_and_stage(self):
        out = build_task_progress(
            "PROGRESS",
            {"message": "m", "processed": 3, "total": 10, "current_stage": 2, "total_stages": 4},
        )
        assert out["status"] == "running"
        assert out["processed"] == 3 and out["total"] == 10
        assert out["current_stage"] == 2 and out["total_stages"] == 4

    def test_progress_default_total_stages_is_one_not_guessed_four(self):
        # Single-stage tasks must not render a bogus "Stage 1/4".
        out = build_task_progress("PROGRESS", {"message": "m"})
        assert out["total_stages"] == 1

    def test_progress_includes_item_result_when_present(self):
        out = build_task_progress("PROGRESS", {"item_result": {"id": 7, "status": "running"}})
        assert out["item_result"] == {"id": 7, "status": "running"}

    def test_progress_omits_item_result_when_absent(self):
        assert "item_result" not in build_task_progress("PROGRESS", {"message": "m"})

    def test_success_unwraps_nested_result(self):
        out = build_task_progress("SUCCESS", {"result": {"sessions": [1, 2]}})
        assert out["status"] == "completed"
        assert out["result"] == {"sessions": [1, 2]}

    def test_success_uses_bare_info_when_not_nested(self):
        out = build_task_progress("SUCCESS", {"sessions_created": 4})
        assert out["result"] == {"sessions_created": 4}

    def test_failure_preserves_exception_message(self):
        # Regression: a non-dict FAILURE info (the exception) must keep its text,
        # not get flattened to "Unknown error".
        out = build_task_progress("FAILURE", RuntimeError("boom"))
        assert out["status"] == "failed"
        assert "boom" in out["error"]

    def test_revoked_maps_to_cancelled(self):
        assert build_task_progress("REVOKED", None)["status"] == "cancelled"


class _FakeAsyncResult:
    """Stand-in for celery.result.AsyncResult keyed by a canned (state, info)."""

    _canned: dict = {}

    def __init__(self, task_id):
        self.state, self.info = self._canned.get(task_id, ("PENDING", None))


class TestJobStatusSnapshot:
    """job_status_snapshot short-circuits on the run's authoritative active_job and
    only falls through to Celery when active_job is silent."""

    def test_active_job_completed_wins_over_celery(self):
        out = job_status_snapshot("t", {"status": "completed", "results": {"x": 1}})
        assert out == {"status": "completed", "message": "Complete!", "result": {"x": 1}}

    def test_active_job_cancelled(self):
        assert job_status_snapshot("t", {"status": "cancelled"})["status"] == "cancelled"

    def test_active_job_failed_carries_error(self):
        out = job_status_snapshot("t", {"status": "failed", "error": "died"})
        assert out["status"] == "failed" and out["error"] == "died"

    def test_running_but_stale_is_failed(self):
        # An old started_at with no terminal write = a dead worker.
        out = job_status_snapshot("t", {"status": "running", "started_at": "2000-01-01T00:00:00"})
        assert out["status"] == "failed"

    def test_falls_through_to_celery_when_no_active_job(self, monkeypatch):
        _FakeAsyncResult._canned = {"live": ("PROGRESS", {"message": "go", "processed": 2, "total": 5})}
        monkeypatch.setattr("connect_labs.workflow.views.AsyncResult", _FakeAsyncResult, raising=False)
        # views.py imports AsyncResult lazily inside job_status_snapshot; patch the
        # source module it imports from.
        monkeypatch.setattr("celery.result.AsyncResult", _FakeAsyncResult, raising=True)
        out = job_status_snapshot("live", None)
        assert out["status"] == "running" and out["processed"] == 2 and out["total"] == 5
