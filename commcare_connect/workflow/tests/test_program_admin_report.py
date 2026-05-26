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
