"""Unit tests for DecisionRecord proxy model."""

from commcare_connect.decisions.models import DecisionRecord


def _record(**data_overrides):
    """Build a DecisionRecord with the given data overrides."""
    return DecisionRecord(
        {
            "id": 42,
            "experiment": "decisions",
            "type": "Decision",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {
                "workflow_run_id": 503,
                "opportunity_id": 10001,
                "flw_id": "amina",
                "reason_key": "bad_muac_distribution",
                "reason_label": "MUAC distribution off-pattern",
                "decision_type": "action_taken",
                "kpi_snapshot": {"muac_dist_score": 0.41},
                "audit_session_ids": [46],
                "task_ids": [123],
                "notes": "Tape placement issue",
                "decided_at": "2025-11-11T11:42:00Z",
                "decided_by": "jane_okeke",
                **data_overrides,
            },
        }
    )


def test_property_round_trip():
    rec = _record()
    assert rec.workflow_run_id == 503
    assert rec.flw_id == "amina"
    assert rec.reason_key == "bad_muac_distribution"
    assert rec.reason_label == "MUAC distribution off-pattern"
    assert rec.decision_type == "action_taken"
    assert rec.kpi_snapshot == {"muac_dist_score": 0.41}
    assert rec.audit_session_ids == [46]
    assert rec.task_ids == [123]
    assert rec.notes == "Tape placement issue"
    assert rec.decided_at == "2025-11-11T11:42:00Z"
    assert rec.decided_by == "jane_okeke"


def test_decision_type_defaults_to_no_issues():
    rec = DecisionRecord(
        {"id": 1, "experiment": "decisions", "type": "Decision", "opportunity_id": 0, "data": {}}
    )
    assert rec.decision_type == "no_issues"


def test_list_and_dict_defaults_when_missing():
    rec = DecisionRecord(
        {"id": 1, "experiment": "decisions", "type": "Decision", "opportunity_id": 0, "data": {}}
    )
    assert rec.audit_session_ids == []
    assert rec.task_ids == []
    assert rec.kpi_snapshot == {}


def test_optional_fields_return_none_when_missing():
    rec = DecisionRecord(
        {"id": 1, "experiment": "decisions", "type": "Decision", "opportunity_id": 0, "data": {}}
    )
    assert rec.reason_key is None
    assert rec.reason_label is None
    assert rec.notes is None
    assert rec.decided_at is None
    assert rec.decided_by is None
    assert rec.workflow_run_id is None
    assert rec.flw_id is None
