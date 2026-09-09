"""Tests for the new funder dashboard features: forecast, filters, AI report, PDF export.

Tests the Python-side components. JS-side (funder-charts.js) functions are tested
via E2E tests.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestSolicitationCrossLink:
    """Test the solicitation-to-fund cross-link (Feature #4)."""

    def test_get_solicitations_by_fund_id(self):
        """SolicitationsDataAccess.get_solicitations_by_fund_id queries with fund_id filter."""
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        with patch.object(SolicitationsDataAccess, "__init__", return_value=None):
            da = SolicitationsDataAccess.__new__(SolicitationsDataAccess)
            da.labs_api = MagicMock()
            da.labs_api.get_records.return_value = []
            da.experiment = "test"

            result = da.get_solicitations_by_fund_id(fund_id=42)
            assert result == []
            da.labs_api.get_records.assert_called_once()
            call_kwargs = da.labs_api.get_records.call_args[1]
            assert call_kwargs["fund_id"] == "42"
            assert call_kwargs["public"] is True

    def test_portfolio_context_carries_a_solicitation_count_per_fund(self):
        """One entry per fund, keyed by pk — the template indexes by fund.pk."""
        from connect_labs.funder_dashboard.views import PortfolioDashboardView

        fund_a, fund_b = MagicMock(pk=1, status="active", program_ids=[]), MagicMock(
            pk=2, status="draft", program_ids=[]
        )
        sol_da = MagicMock()
        sol_da.get_solicitations_by_fund_id.side_effect = lambda pk: ["s"] * pk

        with (
            patch("connect_labs.funder_dashboard.views._get_data_access") as get_da,
            patch("connect_labs.solicitations.data_access.SolicitationsDataAccess", return_value=sol_da),
        ):
            get_da.return_value.get_funds.return_value = [fund_a, fund_b]
            view = PortfolioDashboardView()
            view.request = MagicMock()
            ctx = view.get_context_data()

        assert ctx["solicitation_counts"] == {1: 1, 2: 2}

    def test_portfolio_solicitation_counts_survive_a_failing_solicitations_backend(self):
        """Funds are public and must still render if solicitations are down —
        the count degrades to empty rather than taking the page with it."""
        from connect_labs.funder_dashboard.views import PortfolioDashboardView

        with (
            patch("connect_labs.funder_dashboard.views._get_data_access") as get_da,
            patch(
                "connect_labs.solicitations.data_access.SolicitationsDataAccess",
                side_effect=RuntimeError("solicitations down"),
            ),
        ):
            get_da.return_value.get_funds.return_value = [MagicMock(pk=1, status="active", program_ids=[])]
            view = PortfolioDashboardView()
            view.request = MagicMock()
            ctx = view.get_context_data()

        assert ctx["solicitation_counts"] == {}
        assert len(ctx["funds"]) == 1


class TestFundReportAgent:
    """Test the fund report AI agent (Feature #1)."""

    def test_agent_deps_has_fund_context(self):
        """FundReportAgentDeps stores fund name and description."""
        from connect_labs.ai.agents.fund_report_agent import FundReportAgentDeps
        from connect_labs.ai.types import UserDependencies
        from connect_labs.users.models import User

        user = MagicMock(spec=User)
        deps = FundReportAgentDeps(
            user_deps=UserDependencies(user=user),
            fund_name="Bloomberg Neonatal Fund",
            fund_description="Supporting KMC across West Africa",
        )
        assert deps.fund_name == "Bloomberg Neonatal Fund"
        assert deps.fund_description == "Supporting KMC across West Africa"


class TestSolicitationReviewAgent:
    """Test the solicitation review AI agent (Feature #5)."""

    def test_agent_instructions_mention_comparative(self):
        """The review agent instructions should mention comparative analysis."""
        from connect_labs.ai.agents.solicitation_review_agent import INSTRUCTIONS

        assert "compar" in INSTRUCTIONS.lower()
        assert "shortlist" in INSTRUCTIONS.lower()


class TestApplicationCoachAgent:
    """Test the application coach AI agent (Feature #6)."""

    def test_agent_instructions_are_coaching_not_scoring(self):
        """Coach should help strengthen answers, not score them."""
        from connect_labs.ai.agents.application_coach_agent import INSTRUCTIONS

        assert "coach" in INSTRUCTIONS.lower()
        assert "never write" in INSTRUCTIONS.lower() or "never modify" in INSTRUCTIONS.lower()

    def test_coach_has_no_tool_that_could_reach_another_applicant(self):
        """Security, asserted against the control that actually exists.

        The previous version asserted `"never" in INSTRUCTIONS or "not" in
        INSTRUCTIONS` — "not" occurs in almost any English text, so it passed
        no matter what the prompt said.

        Writing a real one turned up something worth knowing: the module's
        "MUST NOT expose other applicants' responses" line lives in the module
        DOCSTRING, not in INSTRUCTIONS, so it is never sent to the model. That
        is fine, because the binding guarantee was never the prompt — the coach
        is constructed with no tools at all, so there is no code path by which
        it could fetch another applicant's response. That is what this pins:
        adding a tool here should have to be a deliberate, reviewed act.
        """
        from connect_labs.ai.agents.application_coach_agent import create_application_coach_agent_with_model

        agent = create_application_coach_agent_with_model("anthropic:claude-sonnet-4-5-20250929")

        assert agent._function_toolset.tools == {}, (
            f"the coach gained tools it could leak other applicants' data through: "
            f"{sorted(agent._function_toolset.tools)}"
        )


class TestAIStreamViewAgentTypes:
    """Test that AIStreamView accepts the new agent types."""

    @pytest.mark.parametrize("agent_type", ["fund_report", "solicitation_review", "application_coach"])
    def test_post_accepts_the_new_agent_types(self, agent_type, rf):
        """The previous version asserted `hasattr(view, "post")` — true of every
        Django view, and it would still pass if the agent type were dropped from
        the allow-list. Drive the real validation branch instead.

        Two things this had to get right, both learned by getting them wrong:
        an unauthenticated request 401s before validation runs, and the view
        hands `_run_streaming_agent` to a generator that nothing consumes until
        the response is iterated.
        """
        from connect_labs.ai.views import AIStreamView

        request = rf.post(
            "/ai/stream/",
            data=json.dumps({"agent": agent_type, "prompt": "hello"}),
            content_type="application/json",
        )
        request.user = MagicMock(is_authenticated=True)
        request.session = {}

        with patch.object(AIStreamView, "_run_streaming_agent", return_value=iter(())) as run:
            response = AIStreamView().post(request)
            assert response.status_code == 200, f"{agent_type} was rejected by the allow-list"
            b"".join(response.streaming_content)  # the generator is lazy

        assert run.call_args.kwargs["agent_type"] == agent_type

    def test_post_rejects_an_unknown_agent_type(self, rf):
        """The other half of the allow-list — without it, widening the tuple to
        accept anything would still pass every test above."""
        from connect_labs.ai.views import AIStreamView

        request = rf.post(
            "/ai/stream/",
            data=json.dumps({"agent": "not_a_real_agent", "prompt": "hello"}),
            content_type="application/json",
        )
        request.user = MagicMock(is_authenticated=True)
        request.session = {}

        response = AIStreamView().post(request)

        assert response.status_code == 400
        assert "Invalid agent type" in response.content.decode()


@pytest.mark.parametrize(
    "module,factory",
    [
        ("fund_report_agent", "create_fund_report_agent_with_model"),
        ("solicitation_review_agent", "create_solicitation_review_agent_with_model"),
        ("application_coach_agent", "create_application_coach_agent_with_model"),
    ],
)
def test_agent_factory_uses_the_model_it_was_given(module, factory):
    """Replaces three `assert agent is not None` tautologies.

    The thing worth pinning is not that the factory returns an object -- it is
    that the model string reaches the Agent. A factory that ignored its
    argument and fell back to a library default would construct fine, pass any
    `is not None` check, and quietly bill a different model on every call.
    """
    import importlib

    mod = importlib.import_module(f"connect_labs.ai.agents.{module}")
    agent = getattr(mod, factory)("anthropic:claude-sonnet-4-5-20250929")

    assert agent.model.model_name == "claude-sonnet-4-5-20250929"
    assert agent.model.system == "anthropic"
