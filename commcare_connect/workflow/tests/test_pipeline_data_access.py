"""Tests for PipelineDataAccess.execute_pipeline and AnalysisPipeline access_token path."""
from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.labs.analysis.models import FLWAnalysisResult, FLWRow

# AnalysisPipeline is lazily imported inside execute_pipeline, so the right
# patch target is the class in its own module.
_PIPELINE_CLS_PATH = "commcare_connect.labs.analysis.pipeline.AnalysisPipeline"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline_da(access_token="test-token", opportunity_id=42):
    """Build a PipelineDataAccess with no request (MCP server path).

    Bypasses __init__ to avoid real API calls and OAuth setup.
    """
    from commcare_connect.workflow.data_access import PipelineDataAccess

    da = PipelineDataAccess.__new__(PipelineDataAccess)
    da.request = None
    da.access_token = access_token
    da.opportunity_id = opportunity_id
    da.organization_id = None
    da.program_id = None
    da.production_url = "https://connect.example.com"
    da.http_client = MagicMock()
    da.labs_api = MagicMock()
    return da


def _stub_definition(schema):
    """Return a mock pipeline definition with the given schema."""
    mock_def = MagicMock()
    mock_def.name = "Test Pipeline"
    mock_def.schema = schema
    mock_def.data = {"schema": schema, "name": "Test Pipeline"}
    return mock_def


def _make_flw_result(opportunity_id=42):
    row = FLWRow(
        username="alice",
        total_visits=5,
        approved_visits=5,
        pending_visits=0,
        rejected_visits=0,
        flagged_visits=0,
    )
    row.custom_fields = {"metric_a": 3}
    return FLWAnalysisResult(opportunity_id=opportunity_id, rows=[row], metadata={"total_visits": 5})


_SIMPLE_SCHEMA = {
    "fields": [{"name": "metric_a", "path": "form.metric_a", "aggregation": "sum"}],
    "grouping_key": "username",
    "terminal_stage": "aggregated",
}

# ---------------------------------------------------------------------------
# AnalysisPipeline — access_token constructor path
# ---------------------------------------------------------------------------


def test_analysis_pipeline_accepts_access_token_kwarg():
    """AnalysisPipeline can be constructed with only access_token (no request)."""
    from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline(access_token="my-token")

    assert pipeline.access_token == "my-token"
    assert pipeline.request is None
    assert pipeline.labs_context == {}
    assert pipeline.cchq_access_token is None


def test_analysis_pipeline_raises_without_token():
    """AnalysisPipeline raises ValueError when neither request nor token is given."""
    from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

    with pytest.raises(ValueError, match="requires either a request"):
        AnalysisPipeline()


def test_analysis_pipeline_request_path_still_works():
    """AnalysisPipeline still works with a request object (web UI path unchanged)."""
    from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

    mock_request = MagicMock()
    mock_request.session = {"labs_oauth": {"access_token": "req-token"}, "commcare_oauth": {}}
    mock_request.labs_context = {"opportunity_id": 10, "visit_count": 100}

    pipeline = AnalysisPipeline(mock_request)

    assert pipeline.access_token == "req-token"
    assert pipeline.request is mock_request


# ---------------------------------------------------------------------------
# PipelineDataAccess.execute_pipeline — access_token path (MCP server)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch(_PIPELINE_CLS_PATH)
def test_execute_pipeline_works_without_request(mock_pipeline_cls):
    """
    PipelineDataAccess.execute_pipeline must work when constructed with only an
    access_token (MCP server path).  It must NOT return the old
    "Request object required" error.
    """
    mock_pipeline_instance = MagicMock()
    mock_pipeline_instance.stream_analysis_ignore_events.return_value = _make_flw_result()
    mock_pipeline_cls.return_value = mock_pipeline_instance

    da = _make_pipeline_da(access_token="mcp-token", opportunity_id=42)
    da.labs_api.get_record_by_id.return_value = _stub_definition(_SIMPLE_SCHEMA)

    result = da.execute_pipeline(definition_id=99, opportunity_id=42)

    # Must NOT be the old gated error
    assert result.get("metadata", {}).get("error") != "Request object required for pipeline execution"
    assert "rows" in result

    # AnalysisPipeline was constructed with access_token kwarg, no positional request
    mock_pipeline_cls.assert_called_once_with(access_token="mcp-token")


@pytest.mark.django_db
@patch(_PIPELINE_CLS_PATH)
def test_execute_pipeline_uses_request_when_available(mock_pipeline_cls):
    """
    When PipelineDataAccess has a request object (web UI path), AnalysisPipeline
    should be constructed with the request, not the bare token.
    """
    mock_pipeline_instance = MagicMock()
    mock_pipeline_instance.stream_analysis_ignore_events.return_value = _make_flw_result(opportunity_id=10)
    mock_pipeline_cls.return_value = mock_pipeline_instance

    da = _make_pipeline_da(access_token="web-token", opportunity_id=10)
    da.request = MagicMock()  # Simulate a web request being present
    da.labs_api.get_record_by_id.return_value = _stub_definition(_SIMPLE_SCHEMA)

    da.execute_pipeline(definition_id=88, opportunity_id=10)

    # Should be called with the request object, not the bare token
    mock_pipeline_cls.assert_called_once_with(da.request)
