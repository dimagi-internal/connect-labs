"""`workflow_run_cases`: a run's cases by case state, for an agent planning case coaching."""

from unittest.mock import MagicMock

import pytest

import connect_labs.mcp.tools.workflow_run as wa
from connect_labs.labs.canopy import SCOPE_TOOLS
from connect_labs.mcp import token_scopes
from connect_labs.mcp.tests.test_workflow_run_tools import _call, user, wda  # noqa: F401
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.visit_access import RESOLVERS
from connect_labs.workflow import case_briefing

pytestmark = pytest.mark.django_db


def test_it_is_a_read_tool_for_the_panel_and_gated_like_the_other_run_readers():
    assert "workflow_run_cases" in SCOPE_TOOLS["workflow:read"]
    assert "workflow_run_cases" in token_scopes.GENERATED_ONLY_TOOLS
    assert "workflow_run_cases" in token_scopes.USERVISIT_DATA_TOOLS
    assert "workflow_run_cases" in RESOLVERS


def test_it_returns_the_runs_cases_by_case_state(user, wda, monkeypatch):  # noqa: F811
    seen = {}

    def fake(u, wda_, run, definition, **kw):
        seen.update(kw)
        return {
            "as_of": "2026-06-10",
            "source": "live",
            "case_states": [],
            "counts": {},
            "workers": [{"key": "10::asha"}],
        }

    monkeypatch.setattr(case_briefing, "run_cases", fake)
    out = _call("workflow_run_cases", user, run_id=70, program_id=25, case_state="case_state_faltering", per_worker=2)
    assert out["run_id"] == 70 and out["workers"][0]["key"] == "10::asha" and out["source"] == "live"
    assert (seen["case_state"], seen["per_worker"], seen["worker_keys"]) == ("case_state_faltering", 2, None)


def test_through_canopy_it_reads_only_synthetic_opportunities(user, wda, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wa, "_delegated_token", lambda: MagicMock(client_id="canopy"))
    monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: False)
    with pytest.raises(MCPToolError, match="only on synthetic"):
        _call("workflow_run_cases", user, run_id=70, program_id=25)


def test_a_registry_without_case_states_says_so(user, wda, monkeypatch):  # noqa: F811
    def fake(*a, **kw):
        raise case_briefing.CaseBriefingError("no_case_states", "This workflow's registry declares no case states.")

    monkeypatch.setattr(case_briefing, "run_cases", fake)
    with pytest.raises(MCPToolError, match="declares no case states"):
        _call("workflow_run_cases", user, run_id=70, program_id=25)
