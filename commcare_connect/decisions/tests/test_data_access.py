"""Unit tests for DecisionsDataAccess. API client is mocked."""

from unittest.mock import MagicMock

import pytest

from commcare_connect.decisions.data_access import DecisionsDataAccess
from commcare_connect.decisions.models import DecisionRecord
from commcare_connect.labs.models import LocalLabsRecord


@pytest.fixture
def decisions_da():
    """A DecisionsDataAccess with a mocked labs_api client."""
    da = DecisionsDataAccess.__new__(DecisionsDataAccess)
    da.labs_api = MagicMock()
    da.opportunity_id = 10001
    return da


def test_create_decision_action_taken_persists_all_fields(decisions_da):
    decisions_da.labs_api.create_record.return_value = LocalLabsRecord(
        {
            "id": 99,
            "experiment": "decisions",
            "type": "Decision",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {},  # filled in by API
        }
    )

    result = decisions_da.create_decision(
        workflow_run_id=503,
        opportunity_id=10001,
        flw_id="amina",
        decision_type="action_taken",
        reason_key="bad_muac_distribution",
        reason_label="MUAC distribution off-pattern",
        kpi_snapshot={"muac_dist_score": 0.41},
        audit_session_ids=[46],
        task_ids=[123],
        notes="Photo angle off",
        decided_by="jane_okeke",
    )

    assert isinstance(result, DecisionRecord)
    call = decisions_da.labs_api.create_record.call_args.kwargs
    assert call["experiment"] == "decisions"
    assert call["type"] == "Decision"
    assert call["username"] == "amina"
    data = call["data"]
    assert data["workflow_run_id"] == 503
    assert data["opportunity_id"] == 10001
    assert data["flw_id"] == "amina"
    assert data["reason_key"] == "bad_muac_distribution"
    assert data["reason_label"] == "MUAC distribution off-pattern"
    assert data["decision_type"] == "action_taken"
    assert data["kpi_snapshot"] == {"muac_dist_score": 0.41}
    assert data["audit_session_ids"] == [46]
    assert data["task_ids"] == [123]
    assert data["notes"] == "Photo angle off"
    assert data["decided_by"] == "jane_okeke"
    # decided_at defaulted to "now" — present and ISO-ish
    assert "T" in data["decided_at"]


def test_create_decision_no_issues_defaults(decisions_da):
    """A no_issues decision has empty audit/task lists and no reason."""
    decisions_da.labs_api.create_record.return_value = LocalLabsRecord(
        {
            "id": 1,
            "experiment": "decisions",
            "type": "Decision",
            "username": "binta",
            "opportunity_id": 10001,
            "data": {},
        }
    )

    decisions_da.create_decision(
        workflow_run_id=503,
        opportunity_id=10001,
        flw_id="binta",
        decision_type="no_issues",
        decided_by="jane_okeke",
    )

    data = decisions_da.labs_api.create_record.call_args.kwargs["data"]
    assert data["decision_type"] == "no_issues"
    assert data["reason_key"] is None
    assert data["reason_label"] is None
    assert data["audit_session_ids"] == []
    assert data["task_ids"] == []
    assert data["kpi_snapshot"] == {}


def test_create_decision_rejects_invalid_decision_type(decisions_da):
    with pytest.raises(ValueError, match="decision_type must be one of"):
        decisions_da.create_decision(
            workflow_run_id=503,
            opportunity_id=10001,
            flw_id="amina",
            decision_type="something_else",
        )


def test_create_decision_rejects_action_taken_without_reason(decisions_da):
    """An action_taken decision must have reason_key + reason_label."""
    with pytest.raises(ValueError, match="reason_key.*required"):
        decisions_da.create_decision(
            workflow_run_id=503,
            opportunity_id=10001,
            flw_id="amina",
            decision_type="action_taken",
        )


def test_create_decision_rejects_empty_flw_id(decisions_da):
    with pytest.raises(ValueError, match="flw_id"):
        decisions_da.create_decision(
            workflow_run_id=503,
            opportunity_id=10001,
            flw_id="",
            decision_type="no_issues",
        )
