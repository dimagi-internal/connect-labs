"""Tests for the program_admin_report template helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def stub_wda():
    """A WorkflowDataAccess with a mocked labs_api."""
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
    wda.labs_api = MagicMock()
    return wda


def _run(run_id, definition_id, status, completed_at, opportunity_id=10001):
    from commcare_connect.workflow.data_access import WorkflowRunRecord

    return WorkflowRunRecord(
        {
            "id": run_id,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": opportunity_id,
            "data": {
                "definition_id": definition_id,
                "status": status,
                "completed_at": completed_at,
                "state": {},
            },
        }
    )


def test_get_saved_runs_for_program_report_filters_by_source_and_window(stub_wda):
    """Only completed runs for the watched (opp, def) pair within window are returned."""
    from commcare_connect.workflow.data_access import get_saved_runs_for_program_report

    stub_wda.list_runs = MagicMock(
        side_effect=lambda definition_id: {
            47: [
                _run(1, 47, "completed", "2025-11-10T09:00:00Z", opportunity_id=10001),
                _run(2, 47, "in_progress", None, opportunity_id=10001),
                _run(3, 47, "completed", "2025-11-17T09:00:00Z", opportunity_id=10001),
                _run(4, 47, "completed", "2025-12-01T09:00:00Z", opportunity_id=10001),
                _run(5, 47, "completed", "2025-11-15T09:00:00Z", opportunity_id=99999),
            ],
            48: [
                _run(10, 48, "completed", "2025-11-10T09:00:00Z", opportunity_id=10002),
            ],
        }[definition_id]
    )

    window_start = datetime(2025, 11, 4, tzinfo=timezone.utc)
    window_end = datetime(2025, 11, 25, tzinfo=timezone.utc)
    sources = [
        {"opportunity_id": 10001, "workflow_definition_id": 47},
        {"opportunity_id": 10002, "workflow_definition_id": 48},
    ]

    result = get_saved_runs_for_program_report(
        stub_wda,
        watched_sources=sources,
        window_start=window_start,
        window_end=window_end,
    )

    assert len(result) == 2
    opp_to_runs = {entry["opportunity_id"]: entry for entry in result}
    assert sorted(r.id for r in opp_to_runs[10001]["runs"]) == [1, 3]
    assert sorted(r.id for r in opp_to_runs[10002]["runs"]) == [10]


def test_get_saved_runs_for_program_report_handles_missing_runs(stub_wda):
    """A source with no completed runs in window still appears with runs=[]."""
    from commcare_connect.workflow.data_access import get_saved_runs_for_program_report

    stub_wda.list_runs = MagicMock(return_value=[])
    window_start = datetime(2025, 11, 4, tzinfo=timezone.utc)
    window_end = datetime(2025, 11, 25, tzinfo=timezone.utc)

    result = get_saved_runs_for_program_report(
        stub_wda,
        watched_sources=[{"opportunity_id": 10001, "workflow_definition_id": 47}],
        window_start=window_start,
        window_end=window_end,
    )

    assert len(result) == 1
    assert result[0]["opportunity_id"] == 10001
    assert result[0]["workflow_definition_id"] == 47
    assert result[0]["runs"] == []


@pytest.fixture
def fake_run():
    from commcare_connect.workflow.data_access import WorkflowRunRecord

    return WorkflowRunRecord(
        {
            "id": 503,
            "experiment": "workflow_runs",
            "type": "WorkflowRun",
            "opportunity_id": 10001,
            "data": {
                "definition_id": 47,
                "status": "completed",
                "completed_at": "2025-11-10T09:30:00Z",
                "state": {},
            },
        }
    )


def _patch_path(name):
    return f"commcare_connect.workflow.templates.program_admin_report.{name}"


def test_build_snapshot_joins_decisions_with_tasks(fake_run, monkeypatch):
    from unittest.mock import MagicMock

    from commcare_connect.decisions.models import DecisionRecord
    from commcare_connect.tasks.models import TaskRecord
    from commcare_connect.workflow.templates import program_admin_report as par

    # Mock the data-access classes + the cross-workflow reader at module scope
    mock_wda_class = MagicMock()
    mock_dda_class = MagicMock()
    mock_tda_class = MagicMock()
    mock_get_saved_runs = MagicMock(
        return_value=[
            {"opportunity_id": 10001, "workflow_definition_id": 47, "runs": [fake_run]}
        ]
    )

    mock_dda_class.return_value.get_decisions_for_run.return_value = [
        DecisionRecord(
            {
                "id": 11,
                "experiment": "decisions",
                "type": "Decision",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "workflow_run_id": 503,
                    "flw_id": "amina",
                    "decision_type": "action_taken",
                    "reason_key": "bad_muac_distribution",
                    "reason_label": "Bad MUAC",
                    "audit_session_ids": [],
                    "task_ids": [123],
                    "decided_at": "2025-11-10T11:00:00Z",
                },
            }
        ),
    ]
    mock_tda_class.return_value.get_task.return_value = TaskRecord(
        {
            "id": 123,
            "experiment": "tasks",
            "type": "Task",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {
                "status": "closed",
                "resolution_details": {"official_action": "satisfactory"},
                "events": [
                    {"event_type": "created", "timestamp": "2025-11-10T11:01:00Z"},
                    {"event_type": "closed", "timestamp": "2025-11-15T14:00:00Z"},
                ],
            },
        }
    )

    monkeypatch.setattr(par, "WorkflowDataAccess", mock_wda_class)
    monkeypatch.setattr(par, "DecisionsDataAccess", mock_dda_class)
    monkeypatch.setattr(par, "TaskDataAccess", mock_tda_class)
    monkeypatch.setattr(par, "get_saved_runs_for_program_report", mock_get_saved_runs)

    state = {
        "window_start": "2025-11-04T00:00:00Z",
        "window_end": "2025-11-25T23:59:59Z",
        "watched_sources": [
            {"opportunity_id": 10001, "workflow_definition_id": 47},
        ],
    }

    snapshot = par.build_snapshot(
        pipelines={},
        state=state,
        opportunity_id=10001,
        workers=[],
        opportunity_ids=[10001],
        definition_id=999,
    )

    assert snapshot["schema_version"] == 1
    summary = snapshot["watched_summary"]
    assert len(summary) == 1
    source = summary[0]
    assert source["opportunity_id"] == 10001
    assert len(source["runs"]) == 1
    run = source["runs"][0]
    assert run["id"] == 503
    assert len(run["decisions"]) == 1
    decision = run["decisions"][0]
    assert decision["flw_id"] == "amina"
    assert decision["reason_key"] == "bad_muac_distribution"
    assert len(decision["task_outcomes"]) == 1
    outcome = decision["task_outcomes"][0]
    assert outcome["id"] == 123
    assert outcome["status"] == "closed"
    assert outcome["official_action"] == "satisfactory"
    assert outcome["closed_at"] == "2025-11-15T14:00:00Z"


def test_build_snapshot_missing_window_returns_error(monkeypatch):
    from commcare_connect.workflow.templates import program_admin_report as par

    snapshot = par.build_snapshot(
        pipelines={},
        state={"watched_sources": []},
        opportunity_id=10001,
        workers=[],
        opportunity_ids=[10001],
        definition_id=999,
    )
    assert snapshot["error"] == "missing_window"
    assert snapshot["watched_summary"] == []
