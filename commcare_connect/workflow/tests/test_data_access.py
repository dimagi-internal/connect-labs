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


class TestMultiOppProperty:
    def test_defaults_false(self):
        rec = _make_definition_record(data={"name": "X", "description": "Y"})
        assert rec.multi_opp is False

    def test_true_when_config_sets_it(self):
        rec = _make_definition_record(data={"name": "X", "description": "Y", "config": {"multi_opp": True}})
        assert rec.multi_opp is True

    def test_false_when_config_explicitly_false(self):
        rec = _make_definition_record(data={"name": "X", "description": "Y", "config": {"multi_opp": False}})
        assert rec.multi_opp is False


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


class TestCloseIdempotency:
    def test_close_is_safe_to_call_twice(self, workflow_data_access):
        wda, _ = workflow_data_access
        mock_client = MagicMock()
        wda.http_client = mock_client

        wda.close()
        wda.close()  # second call should be a no-op

        mock_client.close.assert_called_once()
        assert wda.http_client is None


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


class TestListTemplatesMultiOpp:
    def test_multi_opp_defaults_false(self):
        from commcare_connect.workflow.templates import TEMPLATES, list_templates

        # Force a known single-opp template into the registry for the test
        TEMPLATES["__test_single__"] = {
            "key": "__test_single__",
            "name": "T",
            "description": "d",
        }
        try:
            listed = {t["key"]: t for t in list_templates()}
            assert listed["__test_single__"]["multi_opp"] is False
        finally:
            del TEMPLATES["__test_single__"]

    def test_multi_opp_true_when_template_sets_it(self):
        from commcare_connect.workflow.templates import TEMPLATES, list_templates

        TEMPLATES["__test_multi__"] = {
            "key": "__test_multi__",
            "name": "T",
            "description": "d",
            "multi_opp": True,
        }
        try:
            listed = {t["key"]: t for t in list_templates()}
            assert listed["__test_multi__"]["multi_opp"] is True
        finally:
            del TEMPLATES["__test_multi__"]


class TestCreateWorkflowFromTemplateOpportunityIds:
    def test_opportunity_ids_passed_to_create_definition(self, workflow_data_access):
        wda, _ = workflow_data_access
        from commcare_connect.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_multi_create__"] = {
            "key": "__test_multi_create__",
            "name": "T",
            "description": "d",
            "multi_opp": True,
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }
        try:
            wda.create_definition = MagicMock(return_value=_make_definition_record(definition_id=10))
            wda.save_render_code = MagicMock()

            create_workflow_from_template(wda, "__test_multi_create__", opportunity_ids=[700, 825])

            kwargs = wda.create_definition.call_args.kwargs
            assert kwargs["opportunity_ids"] == [700, 825]
        finally:
            del TEMPLATES["__test_multi_create__"]

    def test_opportunity_ids_default_empty_list_when_omitted(self, workflow_data_access):
        wda, _ = workflow_data_access
        from commcare_connect.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_single_create__"] = {
            "key": "__test_single_create__",
            "name": "T",
            "description": "d",
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }
        try:
            wda.create_definition = MagicMock(return_value=_make_definition_record(definition_id=11))
            wda.save_render_code = MagicMock()

            create_workflow_from_template(wda, "__test_single_create__")

            kwargs = wda.create_definition.call_args.kwargs
            assert kwargs["opportunity_ids"] == []
        finally:
            del TEMPLATES["__test_single_create__"]


class TestGetPipelineDataMultiOpp:
    def _make_definition(self, opportunity_ids=None, pipeline_sources=None):
        data = {
            "name": "WF",
            "description": "d",
            "pipeline_sources": pipeline_sources or [{"pipeline_id": 101, "alias": "visits"}],
            "opportunity_ids": opportunity_ids or [],
        }
        return _make_definition_record(definition_id=1, data=data)

    def test_falls_back_to_primary_when_opportunity_ids_empty(self, workflow_data_access):
        wda, _ = workflow_data_access
        definition = self._make_definition(opportunity_ids=[])
        wda.get_definition = MagicMock(return_value=definition)

        with patch("commcare_connect.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.execute_pipeline.return_value = {
                "rows": [{"username": "a"}],
                "metadata": {"row_count": 1},
            }

            result = wda.get_pipeline_data(definition_id=1, opportunity_id=700)

            # Only primary opp used
            mock_instance.execute_pipeline.assert_called_once_with(101, 700)
            assert result["visits"]["metadata"]["opportunity_ids"] == [700]
            assert result["visits"]["rows"][0]["opportunity_id"] == 700

    def test_iterates_all_opps_and_tags_rows(self, workflow_data_access):
        wda, _ = workflow_data_access
        definition = self._make_definition(opportunity_ids=[700, 825])
        wda.get_definition = MagicMock(return_value=definition)

        with patch("commcare_connect.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance

            def fake_execute(pipeline_id, opp_id):
                return {
                    "rows": [{"username": f"u_{opp_id}"}],
                    "metadata": {"row_count": 1, "opp": opp_id},
                }

            mock_instance.execute_pipeline.side_effect = fake_execute

            result = wda.get_pipeline_data(definition_id=1, opportunity_id=700)

            assert mock_instance.execute_pipeline.call_count == 2
            rows = result["visits"]["rows"]
            assert len(rows) == 2
            assert {r["opportunity_id"] for r in rows} == {700, 825}
            # Row from opp 700 keeps its own username
            row700 = next(r for r in rows if r["opportunity_id"] == 700)
            assert row700["username"] == "u_700"
            meta = result["visits"]["metadata"]
            assert meta["opportunity_ids"] == [700, 825]
            assert meta["row_count"] == 2
            # per_opp keys are strings so the shape matches JSON-serialized form
            assert set(meta["per_opp"].keys()) == {"700", "825"}

    def test_per_opp_failure_records_error_and_continues(self, workflow_data_access):
        wda, _ = workflow_data_access
        definition = self._make_definition(opportunity_ids=[700, 825])
        wda.get_definition = MagicMock(return_value=definition)

        with patch("commcare_connect.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance

            def fake_execute(pipeline_id, opp_id):
                if opp_id == 825:
                    raise RuntimeError("boom")
                return {"rows": [{"username": "a"}], "metadata": {}}

            mock_instance.execute_pipeline.side_effect = fake_execute

            result = wda.get_pipeline_data(definition_id=1, opportunity_id=700)

            rows = result["visits"]["rows"]
            assert len(rows) == 1
            assert rows[0]["opportunity_id"] == 700
            per_opp = result["visits"]["metadata"]["per_opp"]
            assert "error" in per_opp["825"]

    def test_per_opp_error_metadata_from_execute_pipeline_is_surfaced(self, workflow_data_access):
        """execute_pipeline's documented contract: never raises, returns
        {"rows": [], "metadata": {"error": ...}} on failure. Verify
        get_pipeline_data forwards that error into per_opp[opp_id]."""
        wda, _ = workflow_data_access
        definition = self._make_definition(opportunity_ids=[700, 825])
        wda.get_definition = MagicMock(return_value=definition)

        with patch("commcare_connect.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance

            def fake_execute(pipeline_id, opp_id):
                if opp_id == 825:
                    return {"rows": [], "metadata": {"error": "schema invalid"}}
                return {"rows": [{"username": "a"}], "metadata": {"row_count": 1}}

            mock_instance.execute_pipeline.side_effect = fake_execute

            result = wda.get_pipeline_data(definition_id=1, opportunity_id=700)

            rows = result["visits"]["rows"]
            assert len(rows) == 1
            assert rows[0]["opportunity_id"] == 700
            per_opp = result["visits"]["metadata"]["per_opp"]
            assert per_opp["825"].get("error") == "schema invalid"
