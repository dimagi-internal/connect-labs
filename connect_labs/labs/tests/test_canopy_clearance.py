"""Over real (non-generated) visit data no canopy agent is offered unless labs and canopy have cleared it.

The rule lives in ``labs/canopy_panel.py`` and is held in two places that must agree:
whether a page draws the panel at all, and whether labs' MCP answers canopy's calls.
"""

import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

from connect_labs.labs import canopy, canopy_panel

REPO_ROOT = pathlib.Path(canopy_panel.__file__).resolve().parents[1]


@pytest.fixture
def cleared(settings, monkeypatch):
    """Every clearance condition met, as it will be once canopy signs its statement."""
    settings.CANOPY_AGENT_SLUG = "workflow-agent"
    settings.CANOPY_PHI_AGENTS = ["workflow-agent"]
    settings.CANOPY_RUNNER_REQUIREMENTS = ["zdr"]
    monkeypatch.setattr(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", True)


def _generated(value: bool):
    return patch("connect_labs.labs.synthetic.provenance.all_generated", return_value=value)


# ---------------------------------------------------------------------------
# Clearance
# ---------------------------------------------------------------------------


def _here(reads, agent=""):
    return canopy_panel.availability(agent, reads)


def test_no_visit_data_is_always_offered(settings):
    settings.CANOPY_PHI_AGENTS = []
    assert _here(canopy_panel.NO_VISIT_DATA).ok


def test_generated_data_is_offered_to_any_agent(settings):
    settings.CANOPY_PHI_AGENTS = []
    with _generated(True):
        assert _here(canopy_panel.visits([10_101])).ok


def test_real_data_is_refused_without_canopys_per_call_statement(cleared, monkeypatch):
    # Without an SDK that verifies canopy's per-call statement, even a fully
    # configured labs refuses: the switch is a constant, not a setting.
    monkeypatch.setattr(canopy_panel, "CANOPY_EXECUTION_STATEMENTS", False)
    with _generated(False):
        here = _here(canopy_panel.visits([501]))
    assert not here.ok
    assert "canopy does not yet vouch" in here.reasons[0]


@pytest.mark.parametrize(
    "setting, value, reason",
    [
        ("CANOPY_PHI_AGENTS", [], 'tick "Real data"'),
        ("CANOPY_PHI_AGENTS", ["some-other-agent"], 'tick "Real data"'),
    ],
)
def test_each_condition_fails_closed_on_its_own(cleared, settings, setting, value, reason):
    setattr(settings, setting, value)
    with _generated(False):
        here = _here(canopy_panel.visits([501]))
    assert not here.ok
    assert reason in here.reasons[0]


def test_canopys_word_is_a_condition_too(cleared, fake_canopy):
    from connect_labs.testing.fake_canopy import agent, listing

    for said, why in [
        (None, "canopy has not said"),
        (listing(), "canopy does not allow workflow-agent"),
        (listing(agent("workflow-agent", phi_safe=False)), "not declared PHI-safe"),
        (listing(agent("workflow-agent", phi_safe=True, zdr=False)), "no ready one"),
        (
            listing(agent("workflow-agent", phi_safe=True, ready=False, problems=["no GitHub token"])),
            "no GitHub token",
        ),
    ]:
        fake_canopy.set(said)
        with _generated(False):
            here = _here(canopy_panel.visits([501]))
        assert not here.ok and why in here.reasons[0], (said, here)


def test_every_condition_met_clears_real_data(cleared):
    with _generated(False):
        here = _here(canopy_panel.visits([501]))
    assert here.ok and here.read_only and here.page_agent.runner_requirements == ("zdr",)


@pytest.mark.parametrize("ids", [None, [], ["not-an-id"]])
def test_a_page_that_cannot_tell_what_it_reads_counts_as_real(settings, ids):
    settings.CANOPY_PHI_AGENTS = []
    with _generated(True):
        assert not _here(canopy_panel.visits(ids)).ok


def test_refused_means_no_panel_and_no_page_token():
    with patch("canopy_sdk.django.pages.panel_context") as sdk, _generated(False):
        assert canopy_panel.panel(MagicMock(), reads=canopy_panel.visits([501]), resource="x") is None
    sdk.assert_not_called()


def test_the_host_signs_labs_runner_requirements(settings):
    settings.CANOPY_RUNNER_REQUIREMENTS = ["zdr"]
    assert canopy.host_settings()["RUNNER_REQUIREMENTS"] == ["zdr"]
    settings.CANOPY_RUNNER_REQUIREMENTS = []
    assert canopy.host_settings()["RUNNER_REQUIREMENTS"] == []


# ---------------------------------------------------------------------------
# The one door
# ---------------------------------------------------------------------------


def test_no_view_reaches_the_sdk_panel_except_through_canopy_panel():
    # A view that called the SDK directly would skip the data rule.
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        if "/tests/" in str(path) or path.name == "canopy_panel.py":
            continue
        if re.search(r"canopy_sdk\.django\.pages\s+import[^\n]*\bpanel_context\b", path.read_text()):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


# ---------------------------------------------------------------------------
# Pages: what their workflow sources read
# ---------------------------------------------------------------------------


def _definition(data):
    d = MagicMock()
    d.data = data
    d.opportunity_ids = data.get("opportunity_ids")
    return d


def test_a_page_without_workflow_sources_reads_no_visit_data():
    assert canopy_panel.page_reads(_definition({"kind": "page"}), "t") is canopy_panel.NO_VISIT_DATA


def test_a_page_reads_every_opportunity_its_sources_span():
    page = _definition({"workflow_sources": [{"alias": "r", "workflow": 8, "opportunity_id": 10_113}]})
    with patch("connect_labs.workflow.data_access.WorkflowDataAccess") as WDA:
        WDA.return_value.get_definition.return_value = _definition({"opportunity_ids": [10_113, 501]})
        reads = canopy_panel.page_reads(page, "t")
    assert reads.opportunity_ids == (501, 10_113)


def test_a_source_that_cannot_be_read_makes_the_page_real():
    page = _definition({"workflow_sources": [{"alias": "r", "workflow": 8, "opportunity_id": 10_113}]})
    with patch("connect_labs.workflow.data_access.WorkflowDataAccess") as WDA:
        WDA.return_value.get_definition.return_value = None
        assert canopy_panel.page_reads(page, "t").opportunity_ids is None


# ---------------------------------------------------------------------------
# Labs' MCP: canopy's calls are held to the same rule
# ---------------------------------------------------------------------------


def test_every_tool_a_visit_scope_reaches_says_what_it_reads():
    from connect_labs.mcp.visit_access import DELEGATED_READS

    for scope in canopy.VISIT_SCOPES:
        missing = canopy.SCOPE_TOOLS[scope] - set(DELEGATED_READS)
        assert not missing, f"{scope}: {sorted(missing)} have no entry in DELEGATED_READS"


def test_every_delegated_tool_that_reads_visits_is_judged_or_returns_no_rows():
    from connect_labs.mcp.token_scopes import USERVISIT_DATA_TOOLS
    from connect_labs.mcp.visit_access import DELEGATED_READS

    # Labs data judges itself (labsdata/service.py, `agent_sees_real_rows`): through
    # canopy it returns real rows only to an agent labs has cleared for real data, and
    # otherwise structure only, running queries only on generated opps.
    returns_no_rows = {"explorer_describe", "explorer_query", "data_describe", "data_query", "data_visits"}
    reachable = set().union(*canopy.SCOPE_TOOLS.values())
    unjudged = (reachable & USERVISIT_DATA_TOOLS) - set(DELEGATED_READS) - returns_no_rows
    assert not unjudged, sorted(unjudged)


def _delegated_reads(ids):
    return patch.dict(
        "connect_labs.mcp.visit_access.DELEGATED_READS",
        {"workflow_run_indicators": lambda user, args: canopy_panel.visits(ids)},
    )


def test_canopy_is_refused_a_run_over_real_data():
    from connect_labs.mcp.server import policy_denial

    with _delegated_reads([501]), _generated(False):
        reason = policy_denial("workflow_run_indicators", {"run_id": 1}, MagicMock(), False, delegated=True)
    assert reason and "Through canopy" in reason


def test_canopy_may_read_a_run_over_generated_data():
    from connect_labs.mcp.server import policy_denial

    with (
        _delegated_reads([10_101]),
        _generated(True),
        patch("connect_labs.labs.synthetic.access.labs_only_scope_denied_reason", return_value=None),
    ):
        assert policy_denial("workflow_run_indicators", {"run_id": 1}, MagicMock(), False, delegated=True) is None


class _Vouched:
    """A verified Canopy-Execution statement that clears the call (mcp/execution.py)."""

    agent = "workflow-agent"
    requirements = ("zdr",)

    def phi_clearance(self, *, agents, require=("zdr",)):
        return None if self.agent in set(agents) else "agent_not_allowed"


def test_a_cleared_agent_may_read_real_data_on_a_call_canopy_vouched_for(cleared):
    from connect_labs.mcp import execution
    from connect_labs.mcp.server import policy_denial

    with (
        _delegated_reads([501]),
        _generated(False),
        patch("connect_labs.labs.synthetic.access.labs_only_scope_denied_reason", return_value=None),
    ):
        with execution.bound(execution.CallExecution(execution=_Vouched())):
            assert policy_denial("workflow_run_indicators", {"run_id": 1}, MagicMock(), False, delegated=True) is None
        # The same agent, labs fully configured, but no statement on this call: refused.
        reason = policy_denial("workflow_run_indicators", {"run_id": 1}, MagicMock(), False, delegated=True)
    assert reason and "did not vouch" in reason


def test_a_persons_own_agent_is_not_judged_by_the_canopy_rule():
    from connect_labs.mcp.server import policy_denial

    with (
        _delegated_reads([501]),
        _generated(False),
        patch("connect_labs.labs.synthetic.access.labs_only_scope_denied_reason", return_value=None),
    ):
        assert policy_denial("workflow_run_indicators", {"run_id": 1}, MagicMock(), False) is None


def test_page_context_refuses_canopy_over_real_sources(monkeypatch):
    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools import page_context as tool
    from connect_labs.mcp.tools import workflow_run
    from connect_labs.scope_config import service
    from connect_labs.workflow.tests.test_page_mode import PAGE, _definition

    page = _definition({**PAGE, "config": {"agent": {"share": True}}})
    monkeypatch.setattr(tool, "require_connect_token", lambda u: "t")
    monkeypatch.setattr(service, "refusal", lambda caller, scope: None)
    monkeypatch.setattr("connect_labs.labs.synthetic.access.labs_only_scope_denied_reason", lambda *a, **k: None)
    monkeypatch.setattr(workflow_run, "_delegated_token", lambda: object())
    monkeypatch.setattr(canopy_panel, "page_reads", lambda definition, token: canopy_panel.visits([501]))
    with (
        patch("connect_labs.workflow.data_access.WorkflowDataAccess") as WDA,
        _generated(False),
        pytest.raises(MCPToolError) as refused,
    ):
        WDA.return_value.get_definition.return_value = page
        tool.page_context(MagicMock(), definition_id=47, scope_type="program", scope_key="25", program_id=25)
    assert refused.value.code == "PERMISSION_DENIED" and "Through canopy" in refused.value.message
