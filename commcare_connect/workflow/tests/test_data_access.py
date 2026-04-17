"""Unit tests for WorkflowDataAccess and WorkflowDefinitionRecord.

All tests mock LabsRecordAPIClient to avoid real API calls.
"""


def _make_definition_record(definition_id=1, data=None, opportunity_id=700):
    """Build a WorkflowDefinitionRecord-like raw dict for tests."""
    from commcare_connect.workflow.data_access import WorkflowDefinitionRecord

    return WorkflowDefinitionRecord(
        {
            "id": definition_id,
            "experiment": "workflow",
            "type": "workflow_definition",
            "data": data or {"name": "Test", "description": "d"},
            "opportunity_id": opportunity_id,
        }
    )


class TestOpportunityIdsProperty:
    def test_returns_empty_list_when_absent(self):
        rec = _make_definition_record(data={"name": "X", "description": "Y"})
        assert rec.opportunity_ids == []

    def test_returns_list_when_present(self):
        rec = _make_definition_record(data={"name": "X", "description": "Y", "opportunity_ids": [700, 825]})
        assert rec.opportunity_ids == [700, 825]

    def test_returns_empty_list_when_explicitly_empty(self):
        rec = _make_definition_record(data={"name": "X", "description": "Y", "opportunity_ids": []})
        assert rec.opportunity_ids == []
