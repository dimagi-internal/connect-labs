"""The MCP surface for PROGRAM-scoped Google Drive pipelines.

pipeline_create / pipeline_update_schema take program_id and stamp the Drive source
for the program; pipeline_preview(program_id) reads such a pipeline once, with no
opportunity; workflow_create(program_id) makes a program-owned workflow; and
workflow_add_pipeline_source(program_id) homes a program-owned pipeline in the program.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql import gdrive_fetcher as gf
from connect_labs.labs.analysis.config import DataSourceConfig
from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User

PROGRAM = 121
DRIVE_SCHEMA = {
    "data_source": {"type": "gdrive", "folder_id": "folderA", "file_pattern": "answers_scored_*.csv"},
    "grouping_key": "username",
    "terminal_stage": "entity",
    "linking_field": "qid",
    "fields": [{"name": "qid", "path": "row.qid", "aggregation": "first"}],
}


@pytest.fixture(autouse=True)
def drive_tree(settings, monkeypatch):
    settings.LABS_WORKFLOW_GDRIVE_ROOT_IDS = ["folderA"]
    monkeypatch.setattr(gf, "_drive", lambda: MagicMock(get_parents=lambda _id: []))


def _user(email="analyst@dimagi.com"):
    user = User.objects.create(username=email.split("@")[0], email=email)
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(user=user, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    return raw


def _verify_program(source: dict, pipeline_id: int):
    gf.verify_gdrive_authorization(DataSourceConfig(**source), None, pipeline_id, program_id=PROGRAM)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_create_in_a_program_stamps_for_the_program(mock_pda_cls):
    mock_pda_cls.return_value.create_definition.return_value = MagicMock(id=9, version=1)
    mock_pda_cls.return_value.update_definition.return_value = MagicMock(version=2)
    data = call_tool(_user(), "pipeline_create", {"program_id": PROGRAM, "name": "answers", "schema": DRIVE_SCHEMA})
    assert data["result"]["isError"] is False, data
    assert mock_pda_cls.call_args.kwargs["program_id"] == PROGRAM
    assert "opportunity_id" not in mock_pda_cls.call_args.kwargs
    stamped = mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
    assert stamped["authorization"]["program_id"] == PROGRAM
    _verify_program(stamped, 9)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_create_in_a_program_refuses_a_partner_before_writing(mock_pda_cls):
    data = call_tool(
        _user("someone@partner.org"),
        "pipeline_create",
        {"program_id": PROGRAM, "name": "answers", "schema": DRIVE_SCHEMA},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
    mock_pda_cls.return_value.create_definition.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("scope", [{}, {"opportunity_id": 1251, "program_id": PROGRAM}])
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_tools_need_exactly_one_scope(mock_pda_cls, scope):
    raw = _user()
    for tool, args in [
        ("pipeline_create", {"name": "a", "schema": DRIVE_SCHEMA}),
        ("pipeline_update_schema", {"pipeline_id": 7, "schema": DRIVE_SCHEMA, "expected_version": 1}),
        ("pipeline_get", {"pipeline_id": 7}),
        ("pipeline_preview", {"pipeline_id": 7}),
    ]:
        data = call_tool(raw, tool, {**args, **scope})
        assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA", (tool, data)
    mock_pda_cls.return_value.create_definition.assert_not_called()
    mock_pda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_update_schema_in_a_program_stamps_for_the_program(mock_pda_cls):
    current = MagicMock(version=1, schema={})
    mock_pda_cls.return_value.get_definition.return_value = current
    mock_pda_cls.return_value.update_definition.return_value = MagicMock(version=2)
    data = call_tool(
        _user(),
        "pipeline_update_schema",
        {"pipeline_id": 7, "program_id": PROGRAM, "schema": DRIVE_SCHEMA, "expected_version": 1},
    )
    assert data["result"]["isError"] is False, data
    assert mock_pda_cls.call_args.kwargs["program_id"] == PROGRAM
    _verify_program(mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"], 7)


def _program_config(pipeline_id=7):
    from connect_labs.workflow.data_access import PipelineDataAccess

    stamped = gf.authorize_gdrive_source(
        DRIVE_SCHEMA["data_source"], None, MagicMock(email="analyst@dimagi.com"), pipeline_id, program_id=PROGRAM
    )
    return PipelineDataAccess._schema_to_config(MagicMock(), {**DRIVE_SCHEMA, "data_source": stamped}, pipeline_id)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_reads_a_program_pipeline_once_with_no_opportunity(mock_pda_cls):
    pda = mock_pda_cls.return_value
    pda.get_definition.return_value = MagicMock(data={"schema": DRIVE_SCHEMA}, schema=DRIVE_SCHEMA)
    pda._schema_to_config.return_value = _program_config()
    pda.execute_pipeline.return_value = {"rows": [{"qid": "4.12"}, {"qid": "4.13"}], "metadata": {}}
    data = call_tool(_user(), "pipeline_preview", {"pipeline_id": 7, "program_id": PROGRAM})
    assert data["result"]["isError"] is False, data
    out = data["result"]["structuredContent"]
    pda.execute_pipeline.assert_called_once_with(7, None)
    assert out["row_count_before_sample"] == 2 and out["read_once_for_program"] is True
    assert {r["opportunity_id"] for r in out["rows"]} == {None}
    assert list(out["per_opp_metadata"]) == [f"program:{PROGRAM}"]


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_a_program_pipeline_previewed_across_opportunities_still_reads_once(mock_pda_cls):
    pda = mock_pda_cls.return_value
    pda.get_definition.return_value = MagicMock(data={"schema": DRIVE_SCHEMA}, schema=DRIVE_SCHEMA)
    pda._schema_to_config.return_value = _program_config()
    pda.execute_pipeline.return_value = {"rows": [{"qid": "4.12"}], "metadata": {}}
    data = call_tool(
        _user(), "pipeline_preview", {"pipeline_id": 7, "opportunity_id": 1251, "opportunity_ids": [1252, 1253]}
    )
    assert data["result"]["isError"] is False, data
    pda.execute_pipeline.assert_called_once_with(7, None)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_in_a_program_refuses_a_per_opportunity_pipeline(mock_pda_cls):
    pda = mock_pda_cls.return_value
    schema = {**DRIVE_SCHEMA, "data_source": {"type": "connect_csv"}}
    pda.get_definition.return_value = MagicMock(data={"schema": schema}, schema=schema)
    pda._schema_to_config.return_value = MagicMock(data_source=DataSourceConfig(type="connect_csv"))
    data = call_tool(_user(), "pipeline_preview", {"pipeline_id": 7, "program_id": PROGRAM})
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"
    pda.execute_pipeline.assert_not_called()


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_create_in_a_program(mock_wda_cls):
    mock_wda_cls.return_value.create_definition.return_value = MagicMock(id=600)
    data = call_tool(_user(), "workflow_create", {"program_id": PROGRAM, "name": "Interviews"})
    assert data["result"]["isError"] is False, data
    assert data["result"]["structuredContent"]["program_id"] == PROGRAM
    assert mock_wda_cls.call_args.kwargs["program_id"] == PROGRAM
    assert mock_wda_cls.call_args.kwargs["opportunity_id"] is None


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.workflows.WorkflowDataAccess")
def test_workflow_create_needs_exactly_one_scope(mock_wda_cls):
    data = call_tool(_user(), "workflow_create", {"name": "Interviews"})
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"
    mock_wda_cls.return_value.create_definition.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("program_owned, expected_home", [(True, {"program_id": PROGRAM}), (False, None)])
@patch("connect_labs.mcp.tools.workflows.WorkflowDataAccess")
def test_add_pipeline_source_in_a_program_homes_a_program_owned_pipeline(mock_wda_cls, program_owned, expected_home):
    mock_wda_cls.return_value.add_pipeline_source.return_value = MagicMock(pipeline_sources=[])
    with patch(
        "connect_labs.workflow.data_access.PipelineDataAccess.get_definition",
        return_value=MagicMock() if program_owned else None,
    ):
        data = call_tool(
            _user(),
            "workflow_add_pipeline_source",
            {"workflow_id": 600, "program_id": PROGRAM, "pipeline_id": 7, "alias": "answers", "load": "on_demand"},
        )
    assert data["result"]["isError"] is False, data
    kwargs = mock_wda_cls.return_value.add_pipeline_source.call_args.kwargs
    assert kwargs["home_scope"] == expected_home and kwargs["load"] == "on_demand"


@pytest.mark.django_db
def test_pipeline_editor_save_in_a_program_context_stamps_for_the_program(rf):
    import json

    from connect_labs.workflow.views import update_pipeline_schema_api

    user = User.objects.create(username="analyst", email="analyst@dimagi.com")
    request = rf.post("/x", data=json.dumps({"schema": DRIVE_SCHEMA}), content_type="application/json")
    request.user, request.labs_context, request.session = user, {"program_id": PROGRAM}, {}
    with patch("connect_labs.workflow.data_access.PipelineDataAccess") as pda:
        pda.return_value.get_definition.return_value = MagicMock(schema={})
        updated = MagicMock(id=7, schema={}, version=2)
        updated.name, updated.description = "p", ""
        pda.return_value.update_definition.return_value = updated
        response = update_pipeline_schema_api(request, 7)
    assert response.status_code == 200, response.content
    _verify_program(pda.return_value.update_definition.call_args.kwargs["schema"]["data_source"], 7)
