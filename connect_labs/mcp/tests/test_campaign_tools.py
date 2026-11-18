"""MCP campaign tool tests."""

from __future__ import annotations

import pytest

import connect_labs.mcp.tools.campaign  # noqa: F401 — trigger @register
from connect_labs.campaign.services import dev_boundaries
from connect_labs.mcp.tool_registry import MCPToolError, get_tool

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(user):
    user.email = "builder@dimagi.com"
    user.save()
    return user


def test_campaign_build_national_registered_as_write():
    tool = get_tool("campaign_build_national")
    assert tool is not None
    assert tool.is_write is True


def test_campaign_build_national_builds_from_real_geography(staff):
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    out = get_tool("campaign_build_national").handler(user=staff, worker_count=50, states_limit=3)
    assert out["workers"] == 50
    assert out["states"] == 3
    assert out["commcare_domain"]
    assert out["campaign_code"] == "MR-NAT-2026"


def test_campaign_build_national_errors_without_boundaries(staff):
    with pytest.raises(MCPToolError):
        get_tool("campaign_build_national").handler(user=staff, worker_count=10)


def test_campaign_build_national_is_for_dimagi_staff_only(user):
    # It deletes and rebuilds the campaign with this code; an outside MCP user may not.
    user.email = "someone@partner.org"
    user.save()
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    with pytest.raises(MCPToolError) as exc:
        get_tool("campaign_build_national").handler(user=user, worker_count=5, states_limit=1)
    assert exc.value.code == "PERMISSION_DENIED"
