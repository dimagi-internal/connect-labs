"""MCP campaign tool tests."""
from __future__ import annotations

import pytest

import commcare_connect.mcp.tools.campaign  # noqa: F401 — trigger @register
from commcare_connect.campaign.services import dev_boundaries
from commcare_connect.mcp.tool_registry import MCPToolError, get_tool

pytestmark = pytest.mark.django_db


def test_campaign_build_national_registered_as_write():
    tool = get_tool("campaign_build_national")
    assert tool is not None
    assert tool.is_write is True


def test_campaign_build_national_builds_from_real_geography(user):
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    out = get_tool("campaign_build_national").handler(user=user, worker_count=50, states_limit=3)
    assert out["workers"] == 50
    assert out["states"] == 3
    assert out["commcare_domain"]
    assert out["campaign_code"] == "MR-NAT-2026"


def test_campaign_build_national_errors_without_boundaries(user):
    with pytest.raises(MCPToolError):
        get_tool("campaign_build_national").handler(user=user, worker_count=10)
