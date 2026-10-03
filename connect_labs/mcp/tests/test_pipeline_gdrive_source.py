"""pipeline_update_schema / pipeline_preview stamp a gdrive source's authorization.

The fetcher refuses an unstamped source (test_gdrive_fetcher.py); these pin the
save side: a Dimagi staff save is stamped for the call's opportunity, anyone
else's save of a NEW Drive target is refused before anything is written.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.gdrive_fetcher import verify_gdrive_authorization
from connect_labs.labs.analysis.config import DataSourceConfig
from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User

DRIVE_SCHEMA = {
    "data_source": {"type": "gdrive", "folder_id": "folderA", "file_pattern": "answers_scored_*.csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": [{"name": "quality", "path": "row.quality_score", "aggregation": "first"}],
}


@pytest.fixture(autouse=True)
def drive_tree(settings, monkeypatch):
    """Everything sits under the allowed root (containment is tested in test_gdrive_fetcher)."""
    from connect_labs.labs.analysis.backends.sql import gdrive_fetcher

    settings.LABS_WORKFLOW_GDRIVE_ROOT_IDS = ["folderA"]
    monkeypatch.setattr(gdrive_fetcher, "_drive", lambda: MagicMock(get_parents=lambda _id: []))


def _user(email):
    user = User.objects.create(username=email.split("@")[0], email=email)
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(user=user, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    return raw


def _update(raw, mock_pda_cls):
    current = MagicMock()
    current.version = 1
    mock_pda_cls.return_value.get_definition.return_value = current
    mock_pda_cls.return_value.update_definition.return_value = MagicMock(version=2)
    return call_tool(
        raw,
        "pipeline_update_schema",
        {"pipeline_id": 7, "opportunity_id": 1251, "schema": DRIVE_SCHEMA, "expected_version": 1},
    )


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_staff_save_is_stamped_for_the_opportunity(mock_pda_cls):
    data = _update(_user("analyst@dimagi.com"), mock_pda_cls)
    assert data["result"]["isError"] is False, data
    saved = mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
    assert saved["authorization"]["authorized_by"] == "analyst@dimagi.com"
    verify_gdrive_authorization(DataSourceConfig(**saved), 1251, pipeline_id=7)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_partner_save_of_a_drive_target_is_refused_and_nothing_is_written(mock_pda_cls):
    data = _update(_user("someone@partner.org"), mock_pda_cls)
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "PERMISSION_DENIED"
    mock_pda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_malformed_drive_source_is_invalid_schema(mock_pda_cls):
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(version=1)
    bad = {**DRIVE_SCHEMA, "data_source": {"type": "gdrive"}}
    data = call_tool(
        _user("analyst@dimagi.com"),
        "pipeline_update_schema",
        {"pipeline_id": 7, "opportunity_id": 1251, "schema": bad, "expected_version": 1},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"
    mock_pda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_staff_field_edit_does_not_stamp_a_planted_target_unless_asked(mock_pda_cls):
    raw = _user("analyst@dimagi.com")
    current = MagicMock()
    current.version = 1
    current.schema = DRIVE_SCHEMA  # stored unstamped, by a path that never authorizes
    mock_pda_cls.return_value.get_definition.return_value = current
    mock_pda_cls.return_value.update_definition.return_value = MagicMock(version=2)
    args = {"pipeline_id": 7, "opportunity_id": 1251, "schema": DRIVE_SCHEMA, "expected_version": 1}

    assert call_tool(raw, "pipeline_update_schema", args)["result"]["isError"] is False
    saved = mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
    assert "authorization" not in saved

    forced = call_tool(raw, "pipeline_update_schema", {**args, "authorize_drive_source": True})
    assert forced["result"]["isError"] is False
    saved = mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
    verify_gdrive_authorization(DataSourceConfig(**saved), 1251, pipeline_id=7)


@pytest.mark.django_db
@pytest.mark.parametrize("email, status", [("analyst@dimagi.com", 200), ("someone@partner.org", 403)])
def test_pipeline_editor_save_endpoint(rf, email, status):
    import json

    from connect_labs.workflow.views import update_pipeline_schema_api

    user = User.objects.create(username=email.split("@")[0], email=email)
    request = rf.post("/x", data=json.dumps({"schema": DRIVE_SCHEMA}), content_type="application/json")
    request.user, request.labs_context, request.session = user, {"opportunity_id": 1251}, {}
    with patch("connect_labs.workflow.data_access.PipelineDataAccess") as pda:
        pda.return_value.get_definition.return_value = MagicMock(schema={})
        pda.return_value.update_definition.return_value = MagicMock(
            id=7, schema={}, version=2, is_shared=False, shared_scope=None
        )
        pda.return_value.update_definition.return_value.name = "p"
        pda.return_value.update_definition.return_value.description = ""
        response = update_pipeline_schema_api(request, 7)
    assert response.status_code == status
    if status == 200:
        saved = pda.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
        verify_gdrive_authorization(DataSourceConfig(**saved), 1251, pipeline_id=7)
    else:
        assert "Traceback" not in response.content.decode()
        pda.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_create_stamps_a_drive_source_for_the_new_pipeline(mock_pda_cls):
    mock_pda_cls.return_value.create_definition.return_value = MagicMock(id=9, version=1)
    mock_pda_cls.return_value.update_definition.return_value = MagicMock(version=2)
    data = call_tool(
        _user("analyst@dimagi.com"),
        "pipeline_create",
        {"opportunity_id": 1251, "name": "answers", "schema": DRIVE_SCHEMA},
    )
    assert data["result"]["structuredContent"] == {"pipeline_id": 9, "version": 2}, data
    stamped = mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
    verify_gdrive_authorization(DataSourceConfig(**stamped), 1251, pipeline_id=9)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_create_refuses_a_partner_drive_source_before_writing(mock_pda_cls):
    data = call_tool(
        _user("someone@partner.org"),
        "pipeline_create",
        {"opportunity_id": 1251, "name": "answers", "schema": DRIVE_SCHEMA},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
    mock_pda_cls.return_value.create_definition.assert_not_called()
