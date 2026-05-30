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


def _patch_wda_with(side_effect, monkeypatch):
    """Patch WorkflowDataAccess so its list_runs returns scripted data per def_id.

    Used by reader tests since the reader constructs its own scoped WDA per
    watched source — we can't pre-stub a single WDA the way we used to.
    """
    from commcare_connect.workflow import data_access as wda_module

    instance = MagicMock()
    instance.list_runs = MagicMock(side_effect=side_effect)
    instance.access_token = "stub-token"
    instance.close = MagicMock()
    monkeypatch.setattr(wda_module, "WorkflowDataAccess", lambda *a, **kw: instance)
    return instance


def test_get_saved_runs_for_program_report_filters_by_source_and_window(monkeypatch):
    """Only completed runs for the watched (opp, def) pair within window are returned."""
    from commcare_connect.workflow.data_access import get_saved_runs_for_program_report

    runs_by_def = {
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
    }
    _patch_wda_with(lambda definition_id: runs_by_def[definition_id], monkeypatch)

    window_start = datetime(2025, 11, 4, tzinfo=timezone.utc)
    window_end = datetime(2025, 11, 25, tzinfo=timezone.utc)
    sources = [
        {"opportunity_id": 10001, "workflow_definition_id": 47},
        {"opportunity_id": 10002, "workflow_definition_id": 48},
    ]

    result = get_saved_runs_for_program_report(
        watched_sources=sources,
        window_start=window_start,
        window_end=window_end,
        access_token="stub-token",
    )

    assert len(result) == 2
    opp_to_runs = {entry["opportunity_id"]: entry for entry in result}
    assert sorted(r.id for r in opp_to_runs[10001]["runs"]) == [1, 3]
    assert sorted(r.id for r in opp_to_runs[10002]["runs"]) == [10]


def test_get_saved_runs_for_program_report_handles_missing_runs(monkeypatch):
    """A source with no completed runs in window still appears with runs=[]."""
    from commcare_connect.workflow.data_access import get_saved_runs_for_program_report

    _patch_wda_with(lambda definition_id: [], monkeypatch)
    window_start = datetime(2025, 11, 4, tzinfo=timezone.utc)
    window_end = datetime(2025, 11, 25, tzinfo=timezone.utc)

    result = get_saved_runs_for_program_report(
        watched_sources=[{"opportunity_id": 10001, "workflow_definition_id": 47}],
        window_start=window_start,
        window_end=window_end,
        access_token="stub-token",
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


def test_build_snapshot_joins_flags_audits_tasks_by_flw(fake_run, monkeypatch):
    from unittest.mock import MagicMock

    from commcare_connect.audit.models import AuditSessionRecord
    from commcare_connect.flags.models import FlagRecord
    from commcare_connect.tasks.models import TaskRecord
    from commcare_connect.workflow.templates import program_admin_report as par

    # Mock the data-access classes + the cross-workflow reader at module scope.
    # AuditDataAccess is imported lazily inside build_snapshot so we patch its
    # import path directly.
    mock_fda_class = MagicMock()
    mock_tda_class = MagicMock()
    mock_ada_class = MagicMock()
    mock_get_saved_runs = MagicMock(
        return_value=[{"opportunity_id": 10001, "workflow_definition_id": 47, "runs": [fake_run]}]
    )

    audit_record = AuditSessionRecord(
        {
            "id": 77,
            "experiment": "audit",
            "type": "AuditSession",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {
                "status": "completed",
                "overall_result": "pass",
                "image_results": {"pass": 5, "fail": 0, "pending": 0},
            },
        }
    )

    mock_fda_class.return_value.get_flags_for_run.return_value = [
        FlagRecord(
            {
                "id": 11,
                "experiment": "flags",
                "type": "Flag",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "workflow_run_id": 503,
                    "flw_id": "amina",
                    "flag_key": "sam_low",
                    "flag_label": "SAM rate low",
                    "evidence": {"sam_pct": 0.2},
                    "source": "auto",
                    "flagged_at": "2025-11-10T11:00:00Z",
                },
            }
        ),
    ]
    mock_tda_class.return_value.get_tasks_for_run.return_value = [
        TaskRecord(
            {
                "id": 123,
                "experiment": "tasks",
                "type": "Task",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "username": "amina",
                    "status": "closed",
                    "resolution_details": {"official_action": "satisfactory"},
                    "events": [
                        {"event_type": "created", "timestamp": "2025-11-10T11:01:00Z"},
                        {"event_type": "closed", "timestamp": "2025-11-15T14:00:00Z"},
                    ],
                },
            }
        )
    ]
    mock_ada_class.return_value.get_sessions_by_workflow_run.return_value = [audit_record]

    monkeypatch.setattr(par, "FlagsDataAccess", mock_fda_class)
    monkeypatch.setattr(par, "TaskDataAccess", mock_tda_class)
    monkeypatch.setattr(par, "get_saved_runs_for_program_report", mock_get_saved_runs)
    # AuditDataAccess is imported inside build_snapshot — patch at source.
    monkeypatch.setattr("commcare_connect.audit.data_access.AuditDataAccess", mock_ada_class)

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
    summary = snapshot["state"]["watched_summary"]
    assert snapshot["state"]["window_start"] == "2025-11-04T00:00:00Z"
    assert snapshot["state"]["window_end"] == "2025-11-25T23:59:59Z"
    assert len(summary) == 1
    source = summary[0]
    assert source["opportunity_id"] == 10001
    assert len(source["runs"]) == 1
    run = source["runs"][0]
    assert run["id"] == 503
    assert len(run["flw_rows"]) == 1
    fr = run["flw_rows"][0]
    assert fr["flw_id"] == "amina"
    assert len(fr["flags"]) == 1
    assert fr["flags"][0]["flag_key"] == "sam_low"
    assert len(fr["audits"]) == 1
    assert fr["audits"][0]["id"] == 77
    assert fr["audits"][0]["overall_result"] == "pass"
    assert len(fr["tasks"]) == 1
    task = fr["tasks"][0]
    assert task["id"] == 123
    assert task["status"] == "closed"
    assert task["official_action"] == "satisfactory"
    assert task["closed_at"] == "2025-11-15T14:00:00Z"
    audit = fr["audits"][0]
    assert audit["status"] == "completed"
    assert audit["pass_count"] == 5


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
    # missing-window error path returns the legacy top-level shape (no state)
    assert snapshot["watched_summary"] == []
