"""The reviewer's verdict on a coaching task, and reading every task for a worker.

Both halves exist for one reason: three KMC dashboards sit on the same opportunity and must
agree about a worker's coaching tasks. That only holds if the verdict is stored on the TASK
(opportunity-scoped) rather than in workflow run state (run-scoped), and if there is a read
path that returns closed tasks too.

Guarded here rather than left to the dashboards, because a verdict that silently fails to
save looks identical to one that saved — the dropdown shows what you picked either way.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.tasks.models import TASK_REVIEW_VALUES, TaskRecord
from connect_labs.tasks.views import task_update
from connect_labs.workflow.views import worker_tasks_api


def _task(task_id, username="flw_a", status="investigating", review=None, events=None, run_id=None):
    data = {
        "title": "Coaching task %d" % task_id,
        "status": status,
        "username": username,
        "events": events or [],
    }
    if review is not None:
        data["review"] = review
    if run_id is not None:
        data["workflow_run_id"] = run_id
    return TaskRecord({"id": task_id, "experiment": "tasks", "type": "Task", "opportunity_id": 1, "data": data})


def _created(ts):
    return {"event_type": "created", "timestamp": ts, "actor": "SD", "description": "created"}


def _ai_session(session_id):
    return {"event_type": "ai_session", "session_id": session_id, "actor": "SD", "description": "ai"}


def _get(view, path="/x/"):
    request = RequestFactory().get(path)
    request.user = MagicMock(is_authenticated=True)
    return view(request)


def _post_update(task, body):
    request = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    request.user = MagicMock(is_authenticated=True, get_display_name=lambda: "SD")
    access = MagicMock()
    access.get_task.return_value = task
    with patch("connect_labs.tasks.views.TaskDataAccess", return_value=access):
        response = task_update(request, task.id)
    return response, access


# --------------------------------------------------------------------------------------
# The verdict is stored ON THE TASK
# --------------------------------------------------------------------------------------
class TestReviewIsStoredOnTheTask:
    def test_a_verdict_is_saved(self):
        task = _task(7001)
        response, access = _post_update(task, {"review": "satisfied"})

        assert response.status_code == 200
        assert task.data["review"] == "satisfied"
        access.save_task.assert_called_once_with(task)

    @pytest.mark.parametrize("value", TASK_REVIEW_VALUES)
    def test_every_declared_verdict_is_accepted(self, value):
        """Parametrised off the vocabulary itself, so adding a value without teaching the
        endpoint about it fails here rather than in the browser."""
        task = _task(7001)
        response, _ = _post_update(task, {"review": value})

        assert response.status_code == 200
        assert task.data["review"] == value

    def test_an_unknown_verdict_is_rejected(self):
        task = _task(7001, review="satisfied")
        response, access = _post_update(task, {"review": "looks_fine_to_me"})

        assert response.status_code == 400
        # and the previous verdict is untouched — a rejected write must not half-apply
        assert task.data["review"] == "satisfied"
        access.save_task.assert_not_called()

    def test_null_clears_the_verdict_by_removing_the_key(self):
        """Cleared and never-set must read identically, so the dashboard's "—" option and a
        brand-new task produce the same stored shape."""
        task = _task(7001, review="satisfied")
        response, _ = _post_update(task, {"review": None})

        assert response.status_code == 200
        assert "review" not in task.data

    def test_the_change_is_recorded_on_the_timeline(self):
        task = _task(7001)
        _post_update(task, {"review": "unsatisfactory"})

        described = " ".join(e.get("description", "") for e in task.data.get("events", []))
        assert "review" in described.lower()

    def test_setting_a_verdict_does_not_touch_status(self):
        """status is the task's own lifecycle; review is the human judgement on it. A
        dashboard marking a task 'Satisfied' must not silently close it."""
        task = _task(7001, status="investigating")
        _post_update(task, {"review": "satisfied"})

        assert task.data["status"] == "investigating"


# --------------------------------------------------------------------------------------
# Reading every task for a worker, across every run
# --------------------------------------------------------------------------------------
class TestWorkerTasksApi:
    def _call(self, tasks):
        access = MagicMock()
        access.get_tasks.return_value = tasks
        with patch("connect_labs.tasks.data_access.TaskDataAccess", return_value=access):
            response = _get(worker_tasks_api)
        return response, json.loads(response.content)

    def test_closed_tasks_are_returned_too(self):
        """The whole reason this endpoint exists. open_tasks_api drops these, so a
        dashboard reading it can never show history or a closed task's verdict."""
        response, body = self._call([_task(1, status="closed"), _task(2, status="investigating")])

        assert response.status_code == 200
        assert [t["task_id"] for t in body["tasks"]["flw_a"]] == [2, 1]

    def test_every_task_is_returned_not_just_the_latest(self):
        _, body = self._call([_task(1), _task(2), _task(3)])

        assert len(body["tasks"]["flw_a"]) == 3

    def test_tasks_from_different_runs_all_appear(self):
        """Tasks are stored per opportunity; workflow_run_id only records which run created
        one. A verdict set in run 100 must be visible while viewing run 200."""
        _, body = self._call([_task(1, run_id=100, review="satisfied"), _task(2, run_id=200)])

        entries = body["tasks"]["flw_a"]
        assert {e["workflow_run_id"] for e in entries} == {100, 200}
        assert [e["review"] for e in entries if e["task_id"] == 1] == ["satisfied"]

    def test_the_verdict_comes_back(self):
        _, body = self._call([_task(1, review="needs_verification")])

        assert body["tasks"]["flw_a"][0]["review"] == "needs_verification"

    def test_a_task_with_no_verdict_reports_none_rather_than_omitting_the_key(self):
        _, body = self._call([_task(1)])

        assert body["tasks"]["flw_a"][0]["review"] is None

    def test_creation_time_and_sessions_are_extracted_from_events(self):
        task = _task(1, events=[_created("2026-09-10T09:00:00Z"), _ai_session("sess-a"), _ai_session("sess-b")])
        _, body = self._call([task])

        entry = body["tasks"]["flw_a"][0]
        assert entry["created_at"] == "2026-09-10T09:00:00Z"
        assert entry["session_ids"] == ["sess-a", "sess-b"]

    def test_a_repeated_session_id_is_not_duplicated(self):
        task = _task(1, events=[_ai_session("sess-a"), _ai_session("sess-a")])
        _, body = self._call([task])

        assert body["tasks"]["flw_a"][0]["session_ids"] == ["sess-a"]

    def test_workers_are_keyed_lowercase(self):
        """open_tasks_api lowercases its keys and the render code lowercases before lookup.
        Diverging here would make every mixed-case worker look task-less."""
        _, body = self._call([_task(1, username="FLW_Mixed")])

        assert "flw_mixed" in body["tasks"]

    def test_tasks_with_no_username_are_skipped_not_grouped_under_blank(self):
        _, body = self._call([_task(1, username=""), _task(2)])

        assert "" not in body["tasks"]
        assert list(body["tasks"]) == ["flw_a"]

    def test_several_workers_are_kept_apart(self):
        _, body = self._call([_task(1, username="flw_a"), _task(2, username="flw_b")])

        assert body["tasks"]["flw_a"][0]["task_id"] == 1
        assert body["tasks"]["flw_b"][0]["task_id"] == 2

    def test_undated_tasks_still_sort_newest_first(self):
        """Sorting on created_at would place tasks with no 'created' event arbitrarily; ids
        are monotonic and always present."""
        _, body = self._call([_task(5), _task(9, events=[_created("2020-01-01T00:00:00Z")]), _task(7)])

        assert [t["task_id"] for t in body["tasks"]["flw_a"]] == [9, 7, 5]
