"""
Tests for TaskDataAccess to verify update_record is called with correct keyword arguments.

These tests specifically guard against a regression where update_record was called
with positional arguments (task.id, task.data) instead of keyword arguments
(record_id=, experiment=, type=, data=), which caused task.data (a dict) to be
passed as the `experiment` (string) parameter.
"""

from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.labs.models import LocalLabsRecord
from commcare_connect.tasks.models import TaskRecord


def _make_task_record(task_id=42, status="investigating", events=None):
    """Create a TaskRecord with realistic data for testing."""
    return TaskRecord(
        {
            "id": task_id,
            "experiment": "tasks",
            "type": "Task",
            "opportunity_id": 1,
            "data": {
                "title": "Test task",
                "description": "A test task",
                "priority": "medium",
                "status": status,
                "username": "testuser",
                "flw_name": "Test User",
                "user_id": None,
                "opportunity_id": 1,
                "assigned_to_type": "self",
                "assigned_to_name": "Admin",
                "audit_session_id": None,
                "resolution_details": {},
                "events": events or [],
            },
        }
    )


def _make_update_return_value(task):
    """Create a LocalLabsRecord that update_record would return."""
    return LocalLabsRecord(
        {
            "id": task.id,
            "experiment": "tasks",
            "type": "Task",
            "data": task.data,
            "opportunity_id": 1,
        }
    )


@pytest.fixture
def task_data_access():
    """Create a TaskDataAccess instance with a mocked LabsRecordAPIClient."""
    with patch("commcare_connect.workflow.data_access.LabsRecordAPIClient") as MockAPIClient:
        mock_api = MagicMock()
        MockAPIClient.return_value = mock_api

        with patch("commcare_connect.workflow.data_access.settings") as mock_settings:
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"

            from commcare_connect.tasks.data_access import TaskDataAccess

            tda = TaskDataAccess(
                opportunity_id=1,
                access_token="fake-token",
            )

        # Replace labs_api with our mock so we can inspect calls
        tda.labs_api = mock_api
        yield tda, mock_api


def _assert_update_record_kwargs(mock_api, task):
    """Assert that update_record was called with the correct keyword arguments."""
    mock_api.update_record.assert_called_once()
    call_kwargs = mock_api.update_record.call_args
    # Verify no unexpected positional arguments (only keyword args)
    assert call_kwargs.args == (), (
        f"update_record should be called with keyword arguments only, " f"got positional args: {call_kwargs.args}"
    )
    assert call_kwargs.kwargs["record_id"] == task.id
    assert call_kwargs.kwargs["experiment"] == "tasks"
    assert call_kwargs.kwargs["type"] == "Task"
    assert call_kwargs.kwargs["data"] is task.data


class TestAddEvent:
    def test_calls_update_record_with_correct_kwargs(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.add_event(task, event_type="note", actor="Admin", description="A note")

        _assert_update_record_kwargs(mock_api, task)

    def test_event_is_added_to_task_data(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.add_event(task, event_type="note", actor="Admin", description="A note")

        events = task.data["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "note"
        assert events[0]["actor"] == "Admin"


class TestAddComment:
    def test_calls_update_record_with_correct_kwargs(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.add_comment(task, actor="Admin", content="This is a comment")

        _assert_update_record_kwargs(mock_api, task)

    def test_comment_is_added_as_event(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.add_comment(task, actor="Admin", content="This is a comment")

        events = task.data["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "comment"
        assert events[0]["content"] == "This is a comment"


class TestUpdateStatus:
    def test_calls_update_record_with_correct_kwargs(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record(status="investigating")
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.update_status(task, new_status="closed", actor="Admin")

        _assert_update_record_kwargs(mock_api, task)

    def test_status_is_changed_in_data(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record(status="investigating")
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.update_status(task, new_status="closed", actor="Admin")

        assert task.data["status"] == "closed"

    def test_status_changed_event_is_added(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record(status="investigating")
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.update_status(task, new_status="closed", actor="Admin")

        events = task.data["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "status_changed"
        assert "investigating" in events[0]["description"]
        assert "closed" in events[0]["description"]


class TestAddAiSession:
    def test_calls_update_record_with_correct_kwargs(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        session_params = {"identifier": "test-flw", "platform": "test"}
        tda.add_ai_session(task, actor="Admin", session_params=session_params)

        _assert_update_record_kwargs(mock_api, task)


class TestAssignTask:
    def test_calls_update_record_with_correct_kwargs(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.assign_task(
            task,
            assigned_to_name="Manager",
            assigned_to_type="network_manager",
            actor="Admin",
        )

        _assert_update_record_kwargs(mock_api, task)


class TestWorkflowRunLink:
    def test_workflow_run_id_round_trips_through_create_task(self, task_data_access):
        tda, mock_api = task_data_access

        # Stub create_record to return a record echoing whatever data it received.
        def fake_create(experiment, type, data, username):
            return LocalLabsRecord(
                {
                    "id": 99,
                    "experiment": experiment,
                    "type": type,
                    "data": data,
                    "username": username,
                    "opportunity_id": 1,
                }
            )

        mock_api.create_record.side_effect = fake_create

        task = tda.create_task(
            username="testuser",
            opportunity_id=1,
            title="Hi",
            description="There",
            workflow_run_id=123,
        )

        assert task.workflow_run_id == 123
        # Confirm the API client was handed the value too.
        call_kwargs = mock_api.create_record.call_args.kwargs
        assert call_kwargs["data"]["workflow_run_id"] == 123

    def test_workflow_run_id_defaults_to_none_when_not_provided(self, task_data_access):
        tda, mock_api = task_data_access
        mock_api.create_record.return_value = LocalLabsRecord(
            {
                "id": 100,
                "experiment": "tasks",
                "type": "Task",
                "data": {"workflow_run_id": None},
                "username": "testuser",
                "opportunity_id": 1,
            }
        )

        task = tda.create_task(username="testuser", opportunity_id=1)

        assert task.workflow_run_id is None

    def test_get_tasks_for_run_filters_via_data_lookup(self, task_data_access):
        tda, mock_api = task_data_access
        mock_api.get_records.return_value = []

        tda.get_tasks_for_run(workflow_run_id=42)

        mock_api.get_records.assert_called_once_with(
            experiment="tasks",
            type="Task",
            model_class=TaskRecord,
            workflow_run_id=42,
        )

    def test_assignment_fields_are_updated(self, task_data_access):
        tda, mock_api = task_data_access
        task = _make_task_record()
        mock_api.update_record.return_value = _make_update_return_value(task)

        tda.assign_task(
            task,
            assigned_to_name="Manager",
            assigned_to_type="network_manager",
            actor="Admin",
        )

        assert task.data["assigned_to_name"] == "Manager"
        assert task.data["assigned_to_type"] == "network_manager"
        events = task.data["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "assigned"
