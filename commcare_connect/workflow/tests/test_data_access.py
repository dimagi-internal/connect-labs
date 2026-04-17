"""Unit tests for WorkflowDataAccess and WorkflowDefinitionRecord.

All tests mock LabsRecordAPIClient to avoid real API calls.
"""

from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.labs.models import LocalLabsRecord


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


@pytest.fixture
def workflow_data_access():
    """Instantiate WorkflowDataAccess with a mocked LabsRecordAPIClient."""
    with patch("commcare_connect.workflow.data_access.LabsRecordAPIClient") as MockAPI:
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        with patch("commcare_connect.workflow.data_access.settings") as mock_settings:
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            from commcare_connect.workflow.data_access import WorkflowDataAccess

            wda = WorkflowDataAccess(opportunity_id=700, access_token="fake")
        wda.labs_api = mock_api
        yield wda, mock_api


class TestCreateDefinitionOpportunityIds:
    def test_opportunity_ids_stored_when_provided(self, workflow_data_access):
        wda, mock_api = workflow_data_access
        mock_api.create_record.return_value = LocalLabsRecord(
            {
                "id": 1,
                "experiment": "workflow",
                "type": "workflow_definition",
                "data": {},
                "opportunity_id": 700,
            }
        )

        wda.create_definition(name="WF", description="d", opportunity_ids=[700, 825, 912])

        mock_api.create_record.assert_called_once()
        sent_data = mock_api.create_record.call_args.kwargs["data"]
        assert sent_data["opportunity_ids"] == [700, 825, 912]

    def test_opportunity_ids_absent_when_not_provided(self, workflow_data_access):
        wda, mock_api = workflow_data_access
        mock_api.create_record.return_value = LocalLabsRecord(
            {
                "id": 1,
                "experiment": "workflow",
                "type": "workflow_definition",
                "data": {},
                "opportunity_id": 700,
            }
        )

        wda.create_definition(name="WF", description="d")

        sent_data = mock_api.create_record.call_args.kwargs["data"]
        # Either absent or empty list is acceptable for legacy behavior
        assert sent_data.get("opportunity_ids", []) == []


class TestUpdateOpportunityIds:
    def test_updates_opportunity_ids_preserving_other_fields(self, workflow_data_access):
        wda, mock_api = workflow_data_access
        existing = LocalLabsRecord(
            {
                "id": 5,
                "experiment": "workflow",
                "type": "workflow_definition",
                "data": {
                    "name": "WF",
                    "description": "d",
                    "opportunity_ids": [700],
                    "pipeline_sources": [{"pipeline_id": 1, "alias": "a"}],
                },
                "opportunity_id": 700,
            }
        )
        mock_api.get_record_by_id.return_value = existing
        mock_api.update_record.return_value = existing

        wda.update_opportunity_ids(5, [700, 825, 912])

        mock_api.update_record.assert_called_once()
        sent_data = mock_api.update_record.call_args.kwargs["data"]
        assert sent_data["opportunity_ids"] == [700, 825, 912]
        # Other fields preserved
        assert sent_data["name"] == "WF"
        assert sent_data["pipeline_sources"] == [{"pipeline_id": 1, "alias": "a"}]

    def test_returns_none_when_definition_not_found(self, workflow_data_access):
        wda, mock_api = workflow_data_access
        mock_api.get_record_by_id.return_value = None

        result = wda.update_opportunity_ids(999, [700])
        assert result is None
        mock_api.update_record.assert_not_called()
