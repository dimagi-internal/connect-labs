"""program_admin_demo_seed names its opportunities inside opps[], out of the
dispatcher's labs-only gate, so the tool checks each one itself."""

from unittest.mock import patch

import pytest

import connect_labs.mcp.tools.program_admin_demo  # noqa: F401 — trigger @register
from connect_labs.mcp.tool_registry import MCPToolError, get_tool


def test_each_nested_opportunity_passes_the_labs_only_gate():
    opps = [{"opportunity_id": 10050, "label": "A", "network_manager": "nm", "flws": [{}]}]

    def deny(user, *, opportunity_id=None, program_id=None):
        return "labs-only opportunity 10050 is not accessible to your account" if opportunity_id == 10050 else None

    with (
        patch("connect_labs.labs.synthetic.access.labs_only_scope_denied_reason", side_effect=deny),
        patch("connect_labs.mcp.tools.program_admin_demo._seed") as seed,
    ):
        with pytest.raises(MCPToolError) as exc:
            get_tool("program_admin_demo_seed").handler(user=object(), weeks=["2026-11-02"], opps=opps)
    assert exc.value.code == "PERMISSION_DENIED"
    seed.assert_not_called()
