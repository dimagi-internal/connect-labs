"""Unit tests for WorkflowDataAccess and WorkflowDefinitionRecord.

All tests mock LabsRecordAPIClient to avoid real API calls.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from connect_labs.labs.models import LocalLabsRecord


def _make_definition_record(definition_id=1, data=None, opportunity_id=700):
    """Build a WorkflowDefinitionRecord-like raw dict for tests."""
    from connect_labs.workflow.data_access import WorkflowDefinitionRecord

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
    with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        with patch("connect_labs.workflow.data_access.settings") as mock_settings:
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            from connect_labs.workflow.data_access import WorkflowDataAccess

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


class TestCreateRunOwnership:
    def test_program_scoped_run_sets_program_fk_and_no_opp(self, workflow_data_access):
        wda, mock_api = workflow_data_access
        mock_api.create_record.return_value = LocalLabsRecord(
            {
                "id": 5,
                "experiment": "workflow",
                "type": "workflow_run",
                "data": {"definition_id": 9, "program_id": 176},
                "opportunity_id": None,
                "program_id": 176,
            }
        )

        run = wda.create_run(9, program_id=176, period_start="2026-01-01", period_end="2026-01-07")

        kwargs = mock_api.create_record.call_args.kwargs
        # Program FK passed to the client AND stamped into the run data.
        assert kwargs["program_id"] == 176
        assert kwargs["data"]["program_id"] == 176
        # No owning opportunity anywhere.
        assert "opportunity_id" not in kwargs["data"]
        assert run.program_id == 176
        assert run.opportunity_id is None

    def test_opp_scoped_run_byte_for_behavior_identical(self, workflow_data_access):
        wda, mock_api = workflow_data_access
        mock_api.create_record.return_value = LocalLabsRecord(
            {
                "id": 6,
                "experiment": "workflow",
                "type": "workflow_run",
                "data": {"definition_id": 9},
                "opportunity_id": 700,
            }
        )

        run = wda.create_run(9, opportunity_id=700, period_start="2026-01-01", period_end="2026-01-07")

        kwargs = mock_api.create_record.call_args.kwargs
        # Legacy opp path: no program_id kwarg, no owner keys stamped in data.
        assert "program_id" not in kwargs
        assert "program_id" not in kwargs["data"]
        assert "opportunity_id" not in kwargs["data"]
        assert kwargs["data"]["status"] == "in_progress"
        assert run.opportunity_id == 700

    def test_both_owners_raises(self, workflow_data_access):
        wda, _ = workflow_data_access
        with pytest.raises(ValueError):
            wda.create_run(9, opportunity_id=700, program_id=176, period_start="a", period_end="b")

    def test_no_owner_raises(self, workflow_data_access):
        wda, _ = workflow_data_access
        with pytest.raises(ValueError):
            wda.create_run(9, period_start="a", period_end="b")


class TestCreateDefinitionProgramScope:
    def _program_wda(self, MockAPI):
        with patch("connect_labs.workflow.data_access.settings") as mock_settings:
            mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
            from connect_labs.workflow.data_access import WorkflowDataAccess

            return WorkflowDataAccess(program_id=176, access_token="fake")

    def test_client_constructed_with_program_scope(self):
        with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
            self._program_wda(MockAPI)
            ctor_kwargs = MockAPI.call_args.kwargs
            assert ctor_kwargs["program_id"] == 176
            assert ctor_kwargs["opportunity_id"] is None

    def test_create_definition_returns_program_owned_record(self):
        with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
            wda = self._program_wda(MockAPI)
            mock_api = MockAPI.return_value
            wda.labs_api = mock_api
            mock_api.create_record.return_value = LocalLabsRecord(
                {
                    "id": 3,
                    "experiment": "workflow",
                    "type": "workflow_definition",
                    "data": {"name": "P"},
                    "opportunity_id": None,
                    "program_id": 176,
                }
            )

            rec = wda.create_definition(name="P", description="d")

            # The write went through the program-scoped client, so the returned
            # record carries the program FK and no owning opportunity.
            assert rec.program_id == 176
            assert rec.opportunity_id is None


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
        from connect_labs.workflow.templates import TEMPLATES, list_templates

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
        from connect_labs.workflow.templates import TEMPLATES, list_templates

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
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

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
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

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


class TestCreateWorkflowFromTemplatePipelineCreation:
    """Regression tests for the MCP path: ``request`` is None but the caller's
    ``data_access`` holds a valid access_token, so pipelines should still get
    created. Prior to the fix in PR #71 this was silently skipped, leaving the
    workflow with an empty pipeline_sources and no worker data at render time.
    """

    def test_pipeline_created_with_access_token_only(self, workflow_data_access):
        """When request=None but data_access.access_token is set, pipelines
        are created and returned just like the web-view path."""
        wda, _ = workflow_data_access
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_mcp_pipeline__"] = {
            "key": "__test_mcp_pipeline__",
            "name": "T",
            "description": "d",
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "pipeline_schema": {
                "name": "Test Pipeline",
                "description": "p",
                "version": 1,
                "grouping_key": "u",
                "terminal_stage": "agg",
                "fields": [],
            },
        }
        try:
            wda.create_definition = MagicMock(return_value=_make_definition_record(definition_id=10))
            wda.save_render_code = MagicMock()
            # workflow_data_access fixture already gives wda an access_token

            with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
                mock_instance = MagicMock()
                mock_pipeline = MagicMock()
                mock_pipeline.id = 555
                mock_instance.create_definition.return_value = mock_pipeline
                MockPipelineAccess.return_value = mock_instance

                _, _, pipeline_record = create_workflow_from_template(wda, "__test_mcp_pipeline__", request=None)

                # Pipeline was created despite request=None.
                assert pipeline_record is mock_pipeline
                MockPipelineAccess.assert_called_once()
                # Token AND scope are both forwarded so the new pipeline record
                # is scoped to the same opp as the workflow — otherwise scoped
                # reads (pipeline_get, list views) can't see it afterwards.
                call_kwargs = MockPipelineAccess.call_args.kwargs
                assert call_kwargs["request"] is None
                assert call_kwargs["access_token"] == wda.access_token
                assert call_kwargs["opportunity_id"] == wda.opportunity_id  # 700 from the fixture

                # The new pipeline was linked as a source on the workflow definition.
                create_def_kwargs = wda.create_definition.call_args.kwargs
                assert create_def_kwargs["pipeline_sources"] == [{"pipeline_id": 555, "alias": "data"}]
        finally:
            del TEMPLATES["__test_mcp_pipeline__"]

    def test_pipeline_skipped_when_no_request_and_no_token(self):
        """If neither request nor access_token is available, we still skip
        pipeline creation rather than crashing — preserves prior behaviour
        for any caller that never had auth in the first place."""
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_no_auth__"] = {
            "key": "__test_no_auth__",
            "name": "T",
            "description": "d",
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "pipeline_schema": {
                "name": "P",
                "description": "p",
                "version": 1,
                "grouping_key": "u",
                "terminal_stage": "agg",
                "fields": [],
            },
        }
        try:
            wda = MagicMock()
            # No access_token attribute on this mock → getattr returns None.
            del wda.access_token
            wda.create_definition.return_value = _make_definition_record(definition_id=10)

            with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
                _, _, pipeline_record = create_workflow_from_template(wda, "__test_no_auth__", request=None)
                assert pipeline_record is None
                MockPipelineAccess.assert_not_called()
        finally:
            del TEMPLATES["__test_no_auth__"]

    def test_template_can_declare_pipeline_alias(self, workflow_data_access):
        """A template may declare its own ``pipeline_alias`` so the created
        pipeline source key matches the alias its render code and
        ``snapshot_inputs`` reference. Without this, the source defaulted to
        ``"data"`` and a render reading ``view.pipelines.<other>`` got nothing
        (and the snapshot captured an empty pipelines dict)."""
        wda, _ = workflow_data_access
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_aliased__"] = {
            "key": "__test_aliased__",
            "name": "T",
            "description": "d",
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "pipeline_alias": "flw_kpis",
            "pipeline_schema": {
                "name": "P",
                "description": "p",
                "version": 1,
                "grouping_key": "u",
                "terminal_stage": "agg",
                "fields": [],
            },
        }
        try:
            wda.create_definition = MagicMock(return_value=_make_definition_record(definition_id=10))
            wda.save_render_code = MagicMock()

            with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
                mock_instance = MagicMock()
                mock_pipeline = MagicMock()
                mock_pipeline.id = 777
                mock_instance.create_definition.return_value = mock_pipeline
                MockPipelineAccess.return_value = mock_instance

                create_workflow_from_template(wda, "__test_aliased__", request=None)

                create_def_kwargs = wda.create_definition.call_args.kwargs
                assert create_def_kwargs["pipeline_sources"] == [{"pipeline_id": 777, "alias": "flw_kpis"}]
        finally:
            del TEMPLATES["__test_aliased__"]

    def test_llo_weekly_review_alias_matches_snapshot_inputs(self, workflow_data_access):
        """Regression for #464: the real llo_weekly_review template's pipeline
        source alias must equal the alias its snapshot_inputs and render code
        read (``flw_kpis``) — otherwise completed-run KPI cells render as
        dashes because the snapshot captured an empty pipelines dict."""
        wda, _ = workflow_data_access
        from connect_labs.workflow.templates import create_workflow_from_template
        from connect_labs.workflow.templates.llo_weekly_review import RENDER_CODE, TEMPLATE

        snapshot_aliases = TEMPLATE["snapshot_inputs"]["pipelines"]

        wda.create_definition = MagicMock(return_value=_make_definition_record(definition_id=10))
        wda.save_render_code = MagicMock()

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            mock_pipeline = MagicMock()
            mock_pipeline.id = 888
            mock_instance.create_definition.return_value = mock_pipeline
            MockPipelineAccess.return_value = mock_instance

            create_workflow_from_template(wda, "llo_weekly_review", request=None)

        sources = wda.create_definition.call_args.kwargs["pipeline_sources"]
        assert [s["alias"] for s in sources] == snapshot_aliases
        # The render code reads the same alias off view.pipelines.
        for alias in snapshot_aliases:
            assert f"view.pipelines.{alias}" in RENDER_CODE


class TestCreateWorkflowFromTemplateProgramOwnedPipelineScope:
    """Regression: a program-owned workflow's data_access is scoped
    opportunity_id=None/program_id=<X> — copying that scope straight onto an
    auto-created pipeline (the old behaviour) produces a program-scoped-only
    pipeline record that pipeline_get/pipeline_preview/the real
    get_pipeline_data runtime path (all opportunity_id-only readers) can
    never find. The created pipeline must instead be anchored to a real
    member opportunity, matching how pipelines are opp-owned everywhere else
    in this codebase."""

    def _make_program_scoped_wda(self, program_id=176):
        wda = MagicMock()
        wda.opportunity_id = None
        wda.program_id = program_id
        wda.organization_id = None
        wda.access_token = "fake-token"
        return wda

    def test_pipeline_anchored_to_first_opportunity_id_when_program_owned(self):
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_program_owned_pipeline__"] = {
            "key": "__test_program_owned_pipeline__",
            "name": "T",
            "description": "d",
            "multi_opp": True,
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "pipeline_schema": {
                "name": "P",
                "description": "p",
                "version": 1,
                "grouping_key": "u",
                "terminal_stage": "agg",
                "fields": [],
            },
        }
        try:
            wda = self._make_program_scoped_wda()
            wda.create_definition = MagicMock(
                return_value=_make_definition_record(definition_id=10, opportunity_id=None)
            )
            wda.save_render_code = MagicMock()

            with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
                mock_instance = MagicMock()
                mock_pipeline = MagicMock()
                mock_pipeline.id = 555
                mock_instance.create_definition.return_value = mock_pipeline
                MockPipelineAccess.return_value = mock_instance

                create_workflow_from_template(
                    wda, "__test_program_owned_pipeline__", request=None, opportunity_ids=[1973, 1976, 1978, 1982]
                )

                call_kwargs = MockPipelineAccess.call_args.kwargs
                # Anchored to the first spanned opportunity, NOT the program.
                assert call_kwargs["opportunity_id"] == 1973
                assert call_kwargs["program_id"] is None
        finally:
            del TEMPLATES["__test_program_owned_pipeline__"]

    def test_pipeline_falls_back_to_program_scope_when_no_opportunity_ids(self):
        """No member opportunities were given at all — nothing better to
        anchor to, so preserve the (still imperfect, but no worse than
        before) program-scoped fallback rather than crashing."""
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_program_owned_no_opps__"] = {
            "key": "__test_program_owned_no_opps__",
            "name": "T",
            "description": "d",
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "pipeline_schema": {
                "name": "P",
                "description": "p",
                "version": 1,
                "grouping_key": "u",
                "terminal_stage": "agg",
                "fields": [],
            },
        }
        try:
            wda = self._make_program_scoped_wda()
            wda.create_definition = MagicMock(
                return_value=_make_definition_record(definition_id=11, opportunity_id=None)
            )
            wda.save_render_code = MagicMock()

            with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
                mock_instance = MagicMock()
                mock_pipeline = MagicMock()
                mock_pipeline.id = 556
                mock_instance.create_definition.return_value = mock_pipeline
                MockPipelineAccess.return_value = mock_instance

                create_workflow_from_template(wda, "__test_program_owned_no_opps__", request=None)

                call_kwargs = MockPipelineAccess.call_args.kwargs
                assert call_kwargs["opportunity_id"] is None
                assert call_kwargs["program_id"] == 176
        finally:
            del TEMPLATES["__test_program_owned_no_opps__"]

    def test_opportunity_owned_workflow_unaffected(self, workflow_data_access):
        """Sanity check: the ordinary opportunity-owned path (data_access
        already has an opportunity_id) is untouched by this fix."""
        wda, _ = workflow_data_access  # opportunity_id=700, per the fixture
        from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template

        TEMPLATES["__test_opp_owned_pipeline__"] = {
            "key": "__test_opp_owned_pipeline__",
            "name": "T",
            "description": "d",
            "definition": {"name": "T", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "pipeline_schema": {
                "name": "P",
                "description": "p",
                "version": 1,
                "grouping_key": "u",
                "terminal_stage": "agg",
                "fields": [],
            },
        }
        try:
            wda.create_definition = MagicMock(return_value=_make_definition_record(definition_id=12))
            wda.save_render_code = MagicMock()

            with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
                mock_instance = MagicMock()
                mock_pipeline = MagicMock()
                mock_pipeline.id = 557
                mock_instance.create_definition.return_value = mock_pipeline
                MockPipelineAccess.return_value = mock_instance

                create_workflow_from_template(wda, "__test_opp_owned_pipeline__", request=None)

                call_kwargs = MockPipelineAccess.call_args.kwargs
                assert call_kwargs["opportunity_id"] == 700
                assert call_kwargs["program_id"] is None
        finally:
            del TEMPLATES["__test_opp_owned_pipeline__"]


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

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.execute_pipeline.return_value = {
                "rows": [{"username": "a"}],
                "metadata": {"row_count": 1},
            }

            result = wda.get_pipeline_data(definition_id=1, opportunity_id=700)

            # Only primary opp used. config kwarg is the JOIN-resolved config
            # the orchestrator pre-built; we just check pipeline_id + opp_id here.
            assert mock_instance.execute_pipeline.call_count == 1
            call_args = mock_instance.execute_pipeline.call_args
            assert call_args.args == (101, 700)
            assert result["visits"]["metadata"]["opportunity_ids"] == [700]
            assert result["visits"]["rows"][0]["opportunity_id"] == 700

    def test_iterates_all_opps_and_tags_rows(self, workflow_data_access):
        wda, _ = workflow_data_access
        definition = self._make_definition(opportunity_ids=[700, 825])
        wda.get_definition = MagicMock(return_value=definition)

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance

            def fake_execute(pipeline_id, opp_id, config=None):
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

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance

            def fake_execute(pipeline_id, opp_id, config=None):
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

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance

            def fake_execute(pipeline_id, opp_id, config=None):
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


class TestGetPipelineDataDoesNotLeakProgramScope:
    """Pipeline records are opportunity-owned regardless of who owns the
    workflow. A program-owned WorkflowDataAccess (self.program_id set) must
    NOT forward that program_id into the per-pipeline PipelineDataAccess —
    the production API AND-filters every scope param it's given, so an
    opportunity-owned pipeline silently comes back "not found" once a
    program_id is added alongside a perfectly correct opportunity_id. This
    was the root cause of "no completed audit reports" on program-owned
    workflow 5181 despite the underlying pipelines having real data."""

    def _make_definition(self, opportunity_ids=None, pipeline_sources=None):
        data = {
            "name": "WF",
            "description": "d",
            "pipeline_sources": pipeline_sources or [{"pipeline_id": 101, "alias": "visits"}],
            "opportunity_ids": opportunity_ids or [],
        }
        return _make_definition_record(definition_id=1, data=data)

    def _program_owned_wda(self):
        with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
            MockAPI.return_value = MagicMock()
            with patch("connect_labs.workflow.data_access.settings") as mock_settings:
                mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
                from connect_labs.workflow.data_access import WorkflowDataAccess

                return WorkflowDataAccess(program_id=176, access_token="fake")

    def test_get_pipeline_data_scopes_by_opportunity_only(self):
        wda = self._program_owned_wda()
        definition = self._make_definition(opportunity_ids=[1973, 1976])
        wda.get_definition = MagicMock(return_value=definition)

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.execute_pipeline.return_value = {"rows": [], "metadata": {}}

            wda.get_pipeline_data(definition_id=1, opportunity_id=1973)

            _, kwargs = MockPipelineAccess.call_args
            assert kwargs.get("opportunity_id") == 1973
            assert kwargs.get("program_id") is None
            assert kwargs.get("organization_id") is None

    def test_get_cached_pipeline_data_scopes_by_opportunity_only(self):
        wda = self._program_owned_wda()
        definition = self._make_definition(
            opportunity_ids=[1973, 1976],
            pipeline_sources=[{"pipeline_id": 101, "alias": "visits"}],
        )
        wda.get_definition = MagicMock(return_value=definition)

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.get_cached_pipeline_result.return_value = {"rows": [], "metadata": {}}

            try:
                wda.get_cached_pipeline_data(definition_id=1, opportunity_id=1973, aliases=["visits"])
            except Exception:
                # This test only cares about how PipelineDataAccess was scoped,
                # not the full cache-read behavior (which needs a more elaborate
                # mock of the cache-hit path).
                pass

            _, kwargs = MockPipelineAccess.call_args
            assert kwargs.get("opportunity_id") == 1973
            assert kwargs.get("program_id") is None
            assert kwargs.get("organization_id") is None


class TestBaseDataAccessScopePrecedence:
    """BaseDataAccess.__init__ auto-populates scope from request.labs_context
    ONLY when the caller supplied no explicit scope at all — an explicit
    opportunity_id must never get a session's program_id/organization_id
    blended in alongside it. See TestGetPipelineDataDoesNotLeakProgramScope
    for why that combination breaks record lookups."""

    def _wda(self, **kwargs):
        with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
            MockAPI.return_value = MagicMock()
            with patch("connect_labs.workflow.data_access.settings") as mock_settings:
                mock_settings.CONNECT_PRODUCTION_URL = "https://example.com"
                from connect_labs.workflow.data_access import WorkflowDataAccess

                return WorkflowDataAccess(access_token="fake", **kwargs)

    def test_explicit_opportunity_id_does_not_pick_up_session_program_id(self):
        request = SimpleNamespace(labs_context={"program_id": 176})
        wda = self._wda(request=request, opportunity_id=1973)

        assert wda.opportunity_id == 1973
        assert wda.program_id is None

    def test_no_explicit_scope_falls_back_to_full_session_context(self):
        request = SimpleNamespace(labs_context={"program_id": 176})
        wda = self._wda(request=request)

        assert wda.program_id == 176
        assert wda.opportunity_id is None


class TestGetCachedPipelineData:
    """Cache-only pipeline read used by run completion: never executes,
    scopes to the manifest's aliases, and raises PipelineCacheMiss instead of
    silently snapshotting partial data."""

    def _make_definition(self, pipeline_sources=None, opportunity_ids=None):
        data = {
            "name": "WF",
            "description": "d",
            "pipeline_sources": pipeline_sources
            or [
                {"pipeline_id": 101, "alias": "visits"},
                {"pipeline_id": 102, "alias": "registrations"},
            ],
            "opportunity_ids": opportunity_ids or [],
        }
        return _make_definition_record(definition_id=1, data=data)

    def test_reads_only_wanted_aliases_and_never_executes(self, workflow_data_access):
        wda, _ = workflow_data_access
        wda.get_definition = MagicMock(return_value=self._make_definition())

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.get_cached_pipeline_result.return_value = {
                "rows": [{"username": "a"}],
                "metadata": {"row_count": 1, "from_cache": True},
            }

            result = wda.get_cached_pipeline_data(definition_id=1, opportunity_id=700, aliases=["visits"])

            mock_instance.execute_pipeline.assert_not_called()
            assert mock_instance.get_cached_pipeline_result.call_count == 1
            assert mock_instance.get_cached_pipeline_result.call_args.args == (101, 700)
            assert set(result.keys()) == {"visits"}
            assert result["visits"]["rows"][0]["opportunity_id"] == 700

    def test_none_aliases_reads_all_sources(self, workflow_data_access):
        wda, _ = workflow_data_access
        wda.get_definition = MagicMock(return_value=self._make_definition())

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.get_cached_pipeline_result.return_value = {"rows": [], "metadata": {}}

            result = wda.get_cached_pipeline_data(definition_id=1, opportunity_id=700, aliases=None)

            assert mock_instance.get_cached_pipeline_result.call_count == 2
            assert set(result.keys()) == {"visits", "registrations"}

    def test_cache_miss_raises_with_alias_and_opp(self, workflow_data_access):
        from connect_labs.workflow.data_access import PipelineCacheMiss

        wda, _ = workflow_data_access
        wda.get_definition = MagicMock(return_value=self._make_definition())

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            mock_instance = MagicMock()
            MockPipelineAccess.return_value = mock_instance
            mock_instance.get_cached_pipeline_result.return_value = None

            with pytest.raises(PipelineCacheMiss) as exc:
                wda.get_cached_pipeline_data(definition_id=1, opportunity_id=700, aliases=["visits"])

            assert exc.value.alias == "visits"
            assert exc.value.opportunity_id == 700

    def test_no_matching_aliases_returns_empty_without_touching_pipelines(self, workflow_data_access):
        wda, _ = workflow_data_access
        wda.get_definition = MagicMock(return_value=self._make_definition())

        with patch("connect_labs.workflow.data_access.PipelineDataAccess") as MockPipelineAccess:
            result = wda.get_cached_pipeline_data(definition_id=1, opportunity_id=700, aliases=["nonexistent"])

            assert result == {}
            MockPipelineAccess.assert_not_called()
