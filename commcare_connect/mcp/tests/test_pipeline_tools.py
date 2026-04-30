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
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_update_schema_accepts_documented_aggregations(mock_pda_cls, client, auth_user):
    """count_unique and list are both documented in WORKFLOW_REFERENCE.md and
    supported by the SQL builder — the allow-list used to miss them, forcing
    callers to work around a false-positive rejection."""
    _, raw = auth_user
    mock_pda = mock_pda_cls.return_value
    existing = MagicMock()
    existing.version = 1
    mock_pda.get_definition.return_value = existing
    mock_pda.update_definition.return_value = MagicMock(version=2)

    for agg in ["count_unique", "list"]:
        schema = {"fields": [{"name": "x", "path": "form.x", "aggregation": agg}]}
        data = _call_tool(
            client,
            raw,
            "pipeline_update_schema",
            {
                "pipeline_id": 42,
                "opportunity_id": 100,
                "schema": schema,
                "expected_version": 1,
            },
        )
        assert data["result"]["isError"] is False, (agg, data)


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


# --- pipeline_preview multi-opp fan-out -------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_fans_out_across_opportunity_ids(mock_pda_cls, client, auth_user):
    """opportunity_ids triggers per-opp execution; rows get tagged with
    opportunity_id and the totals reflect all merged rows."""
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"fields": [{"name": "f", "aggregation": "count"}]}},
    )

    def _exec(pipeline_id, oid):
        return {"rows": [{"username": f"u{oid}"}], "metadata": {"row_count": 1}}

    mock_pda_cls.return_value.execute_pipeline.side_effect = _exec

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {
            "pipeline_id": 1,
            "opportunity_id": 100,
            "opportunity_ids": [100, 200, 300],
            "sample_size": 10,
        },
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    # 3 opps × 1 row each
    assert content["row_count_before_sample"] == 3
    assert {r["opportunity_id"] for r in content["rows"]} == {100, 200, 300}
    assert content["opportunity_ids"] == [100, 200, 300]
    assert set(content["per_opp_metadata"].keys()) == {"100", "200", "300"}


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_partial_failure_returns_successful_rows(mock_pda_cls, client, auth_user):
    """If one opp errors but others succeed, we return the successful rows
    and tag the erroring opp in opps_with_errors."""
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"fields": []}},
    )

    def _exec(pipeline_id, oid):
        if oid == 200:
            return {"rows": [], "metadata": {"error": "boom"}}
        return {"rows": [{"username": f"u{oid}"}], "metadata": {"row_count": 1}}

    mock_pda_cls.return_value.execute_pipeline.side_effect = _exec

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {
            "pipeline_id": 1,
            "opportunity_id": 100,
            "opportunity_ids": [100, 200, 300],
        },
    )
    assert data["result"]["isError"] is False
    content = data["result"]["structuredContent"]
    # 2 rows from 100 + 300, 200 errored
    assert content["row_count_before_sample"] == 2
    assert content["metadata"]["opps_with_errors"] == ["200"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_error_hint_for_ungrouped_column(mock_pda_cls, client, auth_user):
    """When every opp errors with a known-pattern SQL message, we surface a
    hint pointing at the likely-offending fields (first/last aggregations)."""
    _, raw = auth_user
    mock_def = MagicMock()
    mock_def.data = {
        "schema": {
            "fields": [
                {"name": "c", "path": "x", "aggregation": "count"},
                {"name": "last_v", "path": "x", "aggregation": "last"},
            ],
        },
    }
    mock_pda_cls.return_value.get_definition.return_value = mock_def
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": [],
        "metadata": {
            "error": 'subquery uses ungrouped column "labs_raw_visit_cache.opportunity_id" from outer query',
        },
    }
    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 1, "opportunity_id": 100},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "UPSTREAM_ERROR"
    assert err["details"]["hint"] is not None
    assert "last_v" in err["details"]["hint"]


# --- pipeline_delete ---------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_delete_happy_path(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(id=1)
    data = _call_tool(
        client,
        raw,
        "pipeline_delete",
        {"pipeline_id": 1, "opportunity_id": 100},
    )
    assert data["result"]["isError"] is False, data
    assert data["result"]["structuredContent"]["deleted"] is True
    mock_pda_cls.return_value.delete_definition.assert_called_once_with(1)


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_delete_not_found(mock_pda_cls, client, auth_user):
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = None
    data = _call_tool(
        client,
        raw,
        "pipeline_delete",
        {"pipeline_id": 999, "opportunity_id": 100},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "NOT_FOUND"
    mock_pda_cls.return_value.delete_definition.assert_not_called()


# --- pipeline_preview fields_all_null diagnostic ---------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_flags_fields_that_extracted_null_in_every_row(mock_pda_cls, client, auth_user):
    """fields_all_null is the canonical signal that a field.path is wrong —
    SQL succeeded, but every row came back null for that field. The response
    names the offending fields and hints at get_form_json_paths for fixing
    them. The hint is ONLY present when there are actual offenders — we don't
    spam the response when everything is healthy."""
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={
            "schema": {
                "fields": [
                    {"name": "visit_count", "aggregation": "count"},
                    {"name": "bad_path_field", "aggregation": "last"},
                ],
            },
        },
    )
    # visit_count looks fine; bad_path_field is null across all rows — classic
    # symptom of a wrong field.path.
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": [
            {"username": "a", "visit_count": 5, "bad_path_field": None},
            {"username": "b", "visit_count": 3, "bad_path_field": None},
        ],
        "metadata": {"row_count": 2},
    }

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 1, "opportunity_id": 100},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["fields_all_null"] == ["bad_path_field"]
    assert content["fields_all_null_hint"] is not None
    assert "get_form_json_paths" in content["fields_all_null_hint"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_clean_response_has_no_null_hint(mock_pda_cls, client, auth_user):
    """When every custom field has at least one non-null value, the hint
    field is null — the response stays lean for healthy pipelines."""
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"fields": [{"name": "visit_count", "aggregation": "count"}]}},
    )
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": [{"username": "a", "visit_count": 5}],
        "metadata": {"row_count": 1},
    }
    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 1, "opportunity_id": 100},
    )
    content = data["result"]["structuredContent"]
    assert content["fields_all_null"] == []
    assert content["fields_all_null_hint"] is None


# =============================================================================
# Regression: pipeline_preview must not crash on cchq_forms pipelines
# =============================================================================
#
# Bug: when an MCP caller previewed a pipeline whose data_source.type is
# "cchq_forms", the request reached AnalysisPipeline → fetch_cchq_forms →
# CommCareDataAccess(request=None, ...) which did `request.session.get(...)`
# and crashed with `'NoneType' object has no attribute 'session'`. The agent
# saw an opaque error and abandoned the tool for the rest of the session.
#
# Fix: surface the structural limitation as a typed CCHQHeadlessError
# upstream, and translate it into an MCPToolError with `headless_cchq_forms`
# in details so callers can distinguish it from a transient SQL failure.


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_cchq_forms_in_mcp_context_returns_clean_error(mock_pda_cls, client, auth_user):
    """cchq_forms data sources cannot run via MCP (no web session OAuth).

    The handler must surface a clean MCPToolError with details that explain
    the structural limitation — NOT a NoneType traceback or a generic
    upstream error that looks transient.
    """
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"data_source": {"type": "cchq_forms"}}},
    )
    # Simulate the inner pipeline failing because we're in headless mode.
    # In production this comes from CCHQHeadlessError → stream_analysis
    # yields EVENT_ERROR → stream_analysis_ignore_events raises RuntimeError
    # → execute_pipeline catches and stuffs into metadata.error.
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": [],
        "metadata": {
            "error": (
                "Pipeline data_source.type is 'cchq_forms', which requires a "
                "CommCare HQ OAuth token from the user's web session. This call "
                "is running in a headless context (no request) so no token is "
                "available."
            ),
        },
    }

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 42, "opportunity_id": 100},
    )
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "UPSTREAM_ERROR"
    # The actionable parts must reach the caller
    assert "headless" in err["message"].lower() or "cchq_forms" in err["message"].lower()
    details = err.get("details") or {}
    assert details.get("headless_cchq_forms") is True
    assert "remediation" in details
    # No NoneType traceback leaked through
    assert "NoneType" not in err["message"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.pipelines.PipelineDataAccess")
def test_pipeline_preview_connect_csv_pipeline_works_without_request(mock_pda_cls, client, auth_user):
    """connect_csv pipelines have no Django-session dependency and must work
    cleanly when invoked via MCP (no request object). This is the canonical
    "does my schema fix extract data" tool — it has to work for non-cchq
    pipelines via the MCP entry point."""
    _, raw = auth_user
    mock_pda_cls.return_value.get_definition.return_value = MagicMock(
        data={"schema": {"data_source": {"type": "connect_csv"}}},
    )
    mock_pda_cls.return_value.execute_pipeline.return_value = {
        "rows": [{"flw_id": "a", "visits": 5}],
        "metadata": {"row_count": 1, "from_cache": False},
    }

    data = _call_tool(
        client,
        raw,
        "pipeline_preview",
        {"pipeline_id": 42, "opportunity_id": 100, "sample_size": 10},
    )
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["row_count_before_sample"] == 1
    assert content["rows"][0]["flw_id"] == "a"
