"""Tests for the synthetic MCP tools (Phase 3, Plan A)."""

import pytest

import commcare_connect.mcp.tools.synthetic  # noqa: F401 — trigger @register side effects
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.mcp.tool_registry import get_tool


@pytest.mark.django_db
def test_synthetic_register_creates_row(user):
    tool = get_tool("synthetic_register")
    result = tool.handler(
        user=user,
        opportunity_id=4242,
        gdrive_folder_id="folder-x",
        enabled=True,
        label="My Demo",
    )
    assert result["opportunity_id"] == 4242
    assert result["enabled"] is True
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.gdrive_folder_id == "folder-x"
    assert row.label == "My Demo"


@pytest.mark.django_db
def test_synthetic_register_updates_existing_row(user):
    SyntheticOpportunity.objects.create(
        opportunity_id=4242, gdrive_folder_id="old", enabled=False
    )
    tool = get_tool("synthetic_register")
    tool.handler(
        user=user,
        opportunity_id=4242,
        gdrive_folder_id="new",
        enabled=True,
        label=None,
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.gdrive_folder_id == "new"
    assert row.enabled is True


@pytest.mark.django_db
def test_synthetic_disable_clears_enabled_flag(user):
    SyntheticOpportunity.objects.create(
        opportunity_id=4242, gdrive_folder_id="x", enabled=True
    )
    tool = get_tool("synthetic_disable")
    result = tool.handler(user=user, opportunity_id=4242)
    assert result["enabled"] is False
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.enabled is False
    # folder retained
    assert row.gdrive_folder_id == "x"


@pytest.mark.django_db
def test_synthetic_disable_404s_on_missing_row(user):
    from commcare_connect.mcp.tool_registry import MCPToolError
    tool = get_tool("synthetic_disable")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=99999)
    assert exc.value.code == "NOT_FOUND"
