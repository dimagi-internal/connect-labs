# commcare_connect/mcp/tests/test_pipeline_tools.py
"""Tests for pipeline_* MCP tools.

Mirror the workflow tool test pattern: mock PipelineDataAccess, avoid
hitting the real API.
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.fixture
def auth_user(db):
    """User with a PAT AND a UserConnectToken (fully set up)."""
    user = User.objects.create(username="pltest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    resp = client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_pat}",
    )
    return resp.json()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_list_returns_pipelines_for_opportunity(mock_pda_cls, client, auth_user):
    _, raw = auth_user

    mock_def = MagicMock(
        id=7,
        description="Test",
        version=2,
        data={"version": 2, "schema": {"fields": [{"name": "a"}]}, "updated_at": None},
    )
    mock_def.name = "MyPipeline"
    mock_pda_cls.return_value.list_definitions.return_value = [mock_def]

    data = _call_tool(client, raw, "pipeline_list", {"opportunity_id": 100})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert len(content["pipelines"]) == 1
    assert content["pipelines"][0]["id"] == 7
    assert content["pipelines"][0]["name"] == "MyPipeline"
    assert content["pipelines"][0]["version"] == 2


@pytest.mark.django_db
def test_pipeline_list_rejects_missing_scope(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "pipeline_list", {})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_pipeline_list_rejects_multiple_scopes(client, auth_user):
    _, raw = auth_user
    data = _call_tool(client, raw, "pipeline_list", {"opportunity_id": 1, "program_id": 2})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_get_returns_full_schema(mock_pda_cls, client, auth_user):
    _, raw = auth_user

    mock_def = MagicMock(
        id=7,
        description="Test",
        version=3,
        schema={
            "fields": [
                {"name": "visits", "aggregation": "count"},
                {"name": "flw", "aggregation": "count_distinct"},
            ]
        },
        data={
            "version": 3,
            "schema": {
                "fields": [
                    {"name": "visits", "aggregation": "count"},
                    {"name": "flw", "aggregation": "count_distinct"},
                ]
            },
        },
    )
    mock_def.name = "Perf Pipeline"
    mock_pda_cls.return_value.get_definition.return_value = mock_def

    data = _call_tool(client, raw, "pipeline_get", {"pipeline_id": 7, "opportunity_id": 100})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 7
    assert content["version"] == 3
    assert len(content["schema"]["fields"]) == 2


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_get_not_found(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = None
    data = _call_tool(client, raw, "pipeline_get", {"pipeline_id": 999, "opportunity_id": 100})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_pipeline_tools_reject_user_without_connect_token(client, db):
    user = User.objects.create(username="no-connect-pl")
    _, raw = MCPAccessToken.create_token(user, name="t")
    data = _call_tool(client, raw, "pipeline_list", {"opportunity_id": 1})
    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"


VALID_SCHEMA = {
    "fields": [
        {"name": "visits", "aggregation": "count"},
        {"name": "flw_id", "aggregation": "count_distinct"},
    ],
}


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_update_schema_happy_path(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    current = MagicMock()
    current.version = 3
    mock_pda_cls.return_value.get_definition.return_value = current
    updated = MagicMock()
    updated.version = 4
    mock_pda_cls.return_value.update_definition.return_value = updated

    data = _call_tool(
        client,
        raw,
        "pipeline_update_schema",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema": VALID_SCHEMA,
            "expected_version": 3,
        },
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["new_version"] == 4
    assert "_version_before" not in content


@pytest.mark.django_db
def test_pipeline_update_schema_rejects_unknown_aggregation(client, auth_user):
    _, raw = auth_user
    bad = {"fields": [{"name": "x", "aggregation": "median_of_medians"}]}
    data = _call_tool(
        client,
        raw,
        "pipeline_update_schema",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema": bad,
            "expected_version": 1,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    assert "median_of_medians" in err["message"]


@pytest.mark.django_db
def test_pipeline_update_schema_rejects_malformed_schema(client, auth_user):
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "pipeline_update_schema",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema": {"fields": "not a list"},
            "expected_version": 1,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_update_schema_version_conflict(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    current = MagicMock()
    current.version = 5
    mock_pda_cls.return_value.get_definition.return_value = current

    data = _call_tool(
        client,
        raw,
        "pipeline_update_schema",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema": VALID_SCHEMA,
            "expected_version": 3,
        },
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "VERSION_CONFLICT"
    assert err["details"]["server_version"] == 5


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_update_schema_not_found(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = None
    data = _call_tool(
        client,
        raw,
        "pipeline_update_schema",
        {
            "pipeline_id": 999,
            "opportunity_id": 100,
            "schema": VALID_SCHEMA,
            "expected_version": 1,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_update_schema_audits_version_transition(mock_pda_cls, client, auth_user):
    """Transport audit captures _version_before / _version_after."""
    from commcare_connect.mcp.models import MCPAuditLog

    user, raw = auth_user
    current = MagicMock()
    current.version = 7
    mock_pda_cls.return_value.get_definition.return_value = current
    updated = MagicMock()
    updated.version = 8
    mock_pda_cls.return_value.update_definition.return_value = updated

    _call_tool(
        client,
        raw,
        "pipeline_update_schema",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema": VALID_SCHEMA,
            "expected_version": 7,
        },
    )
    log = MCPAuditLog.objects.get(user=user, tool_name="pipeline_update_schema")
    assert log.success is True
    assert log.is_write is True
    assert log.version_before == 7
    assert log.version_after == 8


# =============================================================================
# pipeline_preview tests
# =============================================================================

SAMPLE_ROWS = [
    {"flw_id": "a", "visits": 12},
    {"flw_id": "b", "visits": 7},
    {"flw_id": "c", "visits": 3},
]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_happy_path(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"fields": [{"name": "flw_id"}]}},
    )
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": SAMPLE_ROWS,
        "metadata": {"row_count": 3, "from_cache": False},
    }

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 42, "opportunity_id": 100, "sample_size": 2},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert len(content["rows"]) == 2  # sample_size cap applied
    assert content["row_count_before_sample"] == 3
    assert content["used_schema_override"] is False


@pytest.mark.django_db
@patch("commcare_connect.labs.analysis.pipeline.AnalysisPipeline")
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_schema_override_does_not_persist(mock_pda_cls, mock_pipeline_cls, client, auth_user):
    """schema_override is previewed but update_definition is never called.

    With schema_override, the tool bypasses execute_pipeline entirely and calls
    AnalysisPipeline directly (lower-level path), so the definition record is
    never mutated.
    """
    _, raw = auth_user
    mock_defn = MagicMock(data={"schema": {"fields": [{"name": "original"}]}})
    mock_defn.name = "TestPipeline"
    mock_pda_cls.return_value.get_definition.return_value = mock_defn
    # _schema_to_config is called on the pda instance (a MagicMock), so it
    # returns a MagicMock config automatically.
    mock_raw_result = MagicMock()
    mock_raw_result.rows = []
    mock_raw_result.from_cache = False
    mock_pipeline_cls.return_value.stream_analysis_ignore_events.return_value = mock_raw_result

    override = {"fields": [{"name": "new_field", "aggregation": "sum"}]}
    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema_override": override,
        },
    )
    assert data["result"]["isError"] is False, data
    assert data["result"]["structuredContent"]["used_schema_override"] is True

    # CRITICAL: update_definition must NOT have been called — override is preview-only
    mock_pda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
def test_pipeline_preview_rejects_invalid_override(client, auth_user):
    _, raw = auth_user
    bad = {"fields": [{"name": "x", "aggregation": "not_a_real_agg"}]}
    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {
            "pipeline_id": 42,
            "opportunity_id": 100,
            "schema_override": bad,
        },
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_surfaces_executor_error(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"fields": []}},
    )
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": [],
        "metadata": {"error": "Table 'oh_no' does not exist"},
    }

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 42, "opportunity_id": 100},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "UPSTREAM_ERROR"
    assert "oh_no" in err["message"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_not_found(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = None
    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 999, "opportunity_id": 100},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# =============================================================================
# pipeline_sql tests
# =============================================================================


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.generate_sql_preview")
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_sql_happy_path(mock_pda_cls, mock_sql, client, auth_user):
    """generate_sql_preview is imported at module-top, so patch here."""
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        schema={"fields": []},
        data={"schema": {"fields": []}},
    )
    expected_sql_info = {
        "visit_extraction_sql": "SELECT 1",
        "flw_aggregation_sql": None,
        "terminal_stage": "visit_level",
        "field_expressions": {},
        "histogram_expressions": {},
        "computed_fields": [],
    }
    mock_sql.return_value = expected_sql_info

    data = _call_tool(
        client,
        raw,
        "pipeline_sql",
        {"pipeline_id": 42, "opportunity_id": 100},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["sql"] == expected_sql_info
    assert content["used_schema_override"] is False
