"""`workflow_coaching_cases`: the case finder, for an agent planning case coaching."""

from unittest.mock import MagicMock

import pytest

import connect_labs.mcp.tools.workflow_run as wa
from connect_labs.labs.canopy import SCOPE_TOOLS
from connect_labs.mcp import token_scopes
from connect_labs.mcp.tests.test_workflow_run_tools import _call, user, wda  # noqa: F401
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.visit_access import RESOLVERS

pytestmark = pytest.mark.django_db


def test_it_is_a_read_tool_for_the_panel_and_gated_like_the_other_run_readers():
    assert "workflow_coaching_cases" in SCOPE_TOOLS["workflow:read"]
    assert "workflow_coaching_cases" in token_scopes.GENERATED_ONLY_TOOLS
    assert "workflow_coaching_cases" in token_scopes.USERVISIT_DATA_TOOLS
    assert "workflow_coaching_cases" in RESOLVERS


def test_it_returns_the_finder_view_for_the_run(user, wda, monkeypatch):  # noqa: F811
    seen = {}

    def fake(u, **kw):
        seen.update(kw)
        return {"workers": [{"key": "10::asha", "eligible": {}}], "counts": {}}

    monkeypatch.setattr("connect_labs.workflow.case_finder.coaching_cases", fake)
    out = _call("workflow_coaching_cases", user, run_id=70, program_id=25, window_days=14, per_story=2)
    assert out["run_id"] == 70 and out["workers"][0]["key"] == "10::asha"
    assert (seen["window_days"], seen["per_story"], seen["access_token"]) == (14, 2, "tok")


def test_through_canopy_it_reads_only_synthetic_opportunities(user, wda, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wa, "_delegated_token", lambda: MagicMock(client_id="canopy"))
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: False)
    with pytest.raises(MCPToolError, match="only on synthetic"):
        _call("workflow_coaching_cases", user, run_id=70, program_id=25)


def test_a_workflow_without_case_coaching_says_so(user, wda):  # noqa: F811
    with pytest.raises(MCPToolError, match="no case coaching"):
        _call("workflow_coaching_cases", user, run_id=70, program_id=25)
