"""Tests for the workflow_run_default MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

import connect_labs.mcp.tools.workflow_run_default  # noqa: F401  -- triggers @register
from connect_labs.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_workflow_run_default_happy_path_opp_owned(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.access_token = "tok"

    monkeypatch.setattr(wrd, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    fake_run_default = MagicMock(return_value={"run_id": 5001, "sessions_created": 3, "status": "ready"})
    monkeypatch.setattr("connect_labs.workflow.templates.run_default_for_definition", fake_run_default)

    tool = get_tool("workflow_run_default")
    result = tool.handler(user=user, definition_id=100, opportunity_id=4242, cadence="daily")

    assert result == {"run_id": 5001, "sessions_created": 3, "status": "ready"}
    fake_run_default.assert_called_once_with(fake_definition, access_token="tok", request=None, cadence="daily")
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_run_default_happy_path_program_owned(user, monkeypatch):
    """The whole point of this tool: reach a program-owned workflow the
    browser's opp-scoped 'Run Now' action can't."""
    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.access_token = "tok"

    captured_scope = {}

    def _fake_wda_for_user(u, opportunity_id=None, program_id=None):
        captured_scope["opportunity_id"] = opportunity_id
        captured_scope["program_id"] = program_id
        return fake_wda

    monkeypatch.setattr(wrd, "_wda_for_user", _fake_wda_for_user)
    fake_run_default = MagicMock(return_value={"run_id": 9001, "sessions_created": 12, "status": "ready"})
    monkeypatch.setattr("connect_labs.workflow.templates.run_default_for_definition", fake_run_default)

    tool = get_tool("workflow_run_default")
    result = tool.handler(user=user, definition_id=12705, program_id=217, cadence="daily")

    assert result == {"run_id": 9001, "sessions_created": 12, "status": "ready"}
    assert captured_scope == {"opportunity_id": None, "program_id": 217}
    fake_run_default.assert_called_once_with(fake_definition, access_token="tok", request=None, cadence="daily")


@pytest.mark.django_db
def test_workflow_run_default_explicit_window_overrides_cadence(user, monkeypatch):
    from datetime import datetime, timezone

    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.access_token = "tok"

    monkeypatch.setattr(wrd, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    fake_run_default = MagicMock(return_value={"run_id": 1, "sessions_created": 0, "status": "ready"})
    monkeypatch.setattr("connect_labs.workflow.templates.run_default_for_definition", fake_run_default)

    tool = get_tool("workflow_run_default")
    tool.handler(
        user=user,
        definition_id=100,
        opportunity_id=4242,
        cadence="weekly",
        window_start="2026-08-10",
        window_end="2026-08-10",
    )

    # window_start/window_end are parsed into real datetimes -- every template's
    # run_default does `window_start <= dt < window_end`, so raw strings would
    # crash with a TypeError deep in template code (this was a real bug: see
    # PR fixing flw_daily_summary_report backfill). window_end is inclusive of
    # the given calendar date, so a same-day start/end covers that whole day.
    fake_run_default.assert_called_once_with(
        fake_definition,
        access_token="tok",
        request=None,
        window=(
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 11, tzinfo=timezone.utc),
        ),
    )


@pytest.mark.django_db
def test_workflow_run_default_rejects_neither_scope(user):
    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=100)
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_workflow_run_default_rejects_both_scopes(user):
    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=100, opportunity_id=4242, program_id=176)
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_workflow_run_default_rejects_partial_window(user):
    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=100, opportunity_id=4242, window_start="2026-08-10")
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_workflow_run_default_rejects_unparseable_window_date(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.access_token = "tok"

    monkeypatch.setattr(wrd, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            definition_id=100,
            opportunity_id=4242,
            window_start="not-a-date",
            window_end="2026-08-10",
        )
    assert exc.value.code == "INVALID_SCHEMA"
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_run_default_404s_when_definition_missing(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = None

    monkeypatch.setattr(wrd, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=999, opportunity_id=4242)
    assert exc.value.code == "NOT_FOUND"
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_run_default_maps_unsupported_template_to_invalid_schema(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.access_token = "tok"

    monkeypatch.setattr(wrd, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    monkeypatch.setattr(
        "connect_labs.workflow.templates.run_default_for_definition",
        MagicMock(side_effect=ValueError("Workflow 100 (template 'bulk_image_audit') does not support default-run.")),
    )

    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=100, opportunity_id=4242)
    assert exc.value.code == "INVALID_SCHEMA"
    assert "does not support default-run" in exc.value.message
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_run_default_rejects_non_dict_result(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_run_default as wrd

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.access_token = "tok"

    monkeypatch.setattr(wrd, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    monkeypatch.setattr("connect_labs.workflow.templates.run_default_for_definition", MagicMock(return_value=None))

    tool = get_tool("workflow_run_default")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=100, opportunity_id=4242)
    assert exc.value.code == "UPSTREAM_ERROR"


def test_workflow_run_default_is_registered():
    from connect_labs.mcp.tool_registry import _REGISTRY

    assert "workflow_run_default" in _REGISTRY
