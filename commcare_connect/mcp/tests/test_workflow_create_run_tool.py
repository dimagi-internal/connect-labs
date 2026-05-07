"""Tests for the workflow_create_run MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

import commcare_connect.mcp.tools.workflow_create_run  # noqa: F401  -- triggers @register
from commcare_connect.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_workflow_create_run_happy_path(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_create_run as wcr

    fake_definition = MagicMock()
    fake_definition.id = 100

    fake_run = MagicMock()
    fake_run.id = 5001

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.create_run.return_value = fake_run

    monkeypatch.setattr(wcr, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)

    tool = get_tool("workflow_create_run")
    result = tool.handler(
        user=user,
        definition_id=100,
        opportunity_id=4242,
        period_start="2026-02-01",
        period_end="2026-02-07",
    )

    assert result == {
        "run_id": 5001,
        "definition_id": 100,
        "opportunity_id": 4242,
        "period_start": "2026-02-01",
        "period_end": "2026-02-07",
    }
    fake_wda.create_run.assert_called_once_with(
        definition_id=100,
        opportunity_id=4242,
        period_start="2026-02-01",
        period_end="2026-02-07",
        initial_state=None,
    )
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_create_run_404s_when_definition_missing(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_create_run as wcr

    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = None

    monkeypatch.setattr(wcr, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)

    tool = get_tool("workflow_create_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=999, opportunity_id=4242)
    assert exc.value.code == "NOT_FOUND"
    fake_wda.create_run.assert_not_called()
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_create_run_defaults_period_dates_to_today(user, monkeypatch):
    import datetime as dt

    from commcare_connect.mcp.tools import workflow_create_run as wcr

    fake_definition = MagicMock()
    fake_run = MagicMock()
    fake_run.id = 1
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.create_run.return_value = fake_run

    monkeypatch.setattr(wcr, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)

    tool = get_tool("workflow_create_run")
    result = tool.handler(user=user, definition_id=10, opportunity_id=20)

    today = dt.date.today().isoformat()
    assert result["period_start"] == today
    assert result["period_end"] == today


@pytest.mark.django_db
def test_workflow_create_run_propagates_upstream_permission_failures(user, monkeypatch):
    """If the upstream Connect API rejects the create (e.g. user not in org),
    the underlying exception bubbles. Mirrors the rest of the workflow tools."""
    from commcare_connect.mcp.tools import workflow_create_run as wcr

    fake_definition = MagicMock()
    fake_wda = MagicMock()
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.create_run.side_effect = MCPToolError("PERMISSION_DENIED", "user not a member of the opportunity's org")

    monkeypatch.setattr(wcr, "_wda_for_user", lambda u, opportunity_id=None: fake_wda)

    tool = get_tool("workflow_create_run")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, definition_id=10, opportunity_id=20)
    assert exc.value.code == "PERMISSION_DENIED"
    fake_wda.close.assert_called_once()


def test_workflow_create_run_is_registered():
    from commcare_connect.mcp.tool_registry import _REGISTRY

    assert "workflow_create_run" in _REGISTRY
