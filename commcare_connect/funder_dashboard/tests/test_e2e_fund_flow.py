"""
End-to-end integration tests for the funder dashboard flow.

Walks through the full demo scenario using Django's test client
with a mocked LabsRecordAPIClient. Verifies every step a funder
would take: portfolio → create fund → view detail → edit fund.

Run:
    pytest commcare_connect/funder_dashboard/tests/test_e2e_fund_flow.py -v
"""
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from commcare_connect.funder_dashboard.data_access import FUND_TYPE
from commcare_connect.funder_dashboard.models import FundRecord
from commcare_connect.funder_dashboard.views import (
    FundCreateView,
    FundDetailView,
    FundEditView,
    PortfolioDashboardView,
)

# =========================================================================
# Helpers
# =========================================================================


def _make_fund_record(pk=1, name="Baobab Fund", status="active", **data_overrides):
    """Create a FundRecord proxy model instance."""
    data = {
        "name": name,
        "description": "Fund for neonatal care",
        "total_budget": 500000,
        "currency": "USD",
        "status": status,
        "program_ids": [10, 20],
        "delivery_types": ["facility", "community"],
        "org_id": "42",
    }
    data.update(data_overrides)
    return FundRecord(
        {
            "id": pk,
            "experiment": "42",
            "type": FUND_TYPE,
            "data": data,
            "opportunity_id": 0,
        }
    )


def _make_request(path="/funder/", method="GET", data=None, user=None):
    """Build a request with authenticated user and labs context."""
    factory = RequestFactory()
    if method == "POST":
        request = factory.post(path, data=data or {})
    else:
        request = factory.get(path)
    if user is None:
        user = MagicMock(is_authenticated=True, username="funder_user")
        user.id = 1
    request.user = user
    request.labs_context = {"organization_id": 42}
    request.session = {"labs_oauth": {"access_token": "tok", "expires_at": 9999999999}}
    return request


# Patch context processors that need real settings (GTM, chat widget) to
# return plain dicts so base.html renders without errors.
_CONTEXT_PATCH = patch.multiple(
    "commcare_connect.web.context_processors",
    gtm_context=lambda request: {"GTM_VARS_JSON": {}},
    chat_widget_context=lambda request: {
        "chat_widget_enabled": False,
        "chatbot_id": "",
        "chatbot_embed_key": "",
    },
)


# =========================================================================
# Step 1: Portfolio Dashboard
# =========================================================================


class TestStep1Portfolio:
    """Step 1: Funder lands on the portfolio dashboard."""

    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_portfolio_shows_funds(self, MockDA):
        """Portfolio page lists all funds for the org."""
        funds = [
            _make_fund_record(pk=1, name="Baobab Fund"),
            _make_fund_record(pk=2, name="Acacia Fund", status="closed"),
        ]
        MockDA.return_value.get_funds.return_value = funds

        request = _make_request("/funder/")
        view = PortfolioDashboardView()
        view.request = request
        view.kwargs = {}
        ctx = view.get_context_data()

        assert ctx["has_context"] is True
        assert len(ctx["funds"]) == 2
        assert ctx["funds"][0].name == "Baobab Fund"
        assert ctx["funds"][1].name == "Acacia Fund"

    @_CONTEXT_PATCH
    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_portfolio_renders_template(self, MockDA):
        """Portfolio page renders the correct template with fund data."""
        MockDA.return_value.get_funds.return_value = [
            _make_fund_record(pk=1, name="Test Fund"),
        ]

        request = _make_request("/funder/")
        response = PortfolioDashboardView.as_view()(request)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Test Fund" in content
        assert "Funder Dashboard" in content

    def test_portfolio_without_org_context(self):
        """Portfolio page shows empty state when no org selected."""
        request = _make_request("/funder/")
        request.labs_context = {}

        view = PortfolioDashboardView()
        view.request = request
        view.kwargs = {}
        ctx = view.get_context_data()

        assert ctx["has_context"] is False
        assert ctx["funds"] == []


# =========================================================================
# Step 2: Create Fund
# =========================================================================


class TestStep2CreateFund:
    """Step 2: Funder creates a new fund."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_create_form_renders(self, MockDA):
        """Create page shows the fund form."""
        request = _make_request("/funder/fund/create/")
        response = FundCreateView.as_view()(request)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Create New Fund" in content
        assert 'name="name"' in content

    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_create_fund_post(self, MockDA):
        """POST with valid data creates a fund and redirects to portfolio."""
        mock_record = _make_fund_record(pk=99, name="New Fund")
        MockDA.return_value.create_fund.return_value = mock_record

        request = _make_request(
            "/funder/fund/create/",
            method="POST",
            data={
                "name": "New Fund",
                "description": "A brand new fund",
                "total_budget": "1000000",
                "currency": "USD",
                "status": "active",
            },
        )
        response = FundCreateView.as_view()(request)

        assert response.status_code == 302
        assert response.url == "/funder/"
        MockDA.return_value.create_fund.assert_called_once()
        call_data = MockDA.return_value.create_fund.call_args[0][0]
        assert call_data["name"] == "New Fund"
        assert call_data["total_budget"] == 1000000

    @_CONTEXT_PATCH
    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_create_fund_validation_error(self, MockDA):
        """POST with missing required field re-renders form with error."""
        request = _make_request(
            "/funder/fund/create/",
            method="POST",
            data={"description": "Missing name", "status": "active"},
        )
        response = FundCreateView.as_view()(request)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Create New Fund" in content


# =========================================================================
# Step 3: View Fund Detail
# =========================================================================


class TestStep3FundDetail:
    """Step 3: Funder views a fund's detail page."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_detail_shows_fund(self, MockDA):
        """Detail page renders fund name, budget, programs."""
        fund = _make_fund_record(
            pk=1,
            name="Baobab Fund",
            total_budget=500000,
            program_ids=[10, 20],
            delivery_types=["facility"],
        )
        MockDA.return_value.get_fund_by_id.return_value = fund

        request = _make_request("/funder/fund/1/")
        response = FundDetailView.as_view()(request, pk=1)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Baobab Fund" in content
        assert "500000" in content or "500,000" in content

    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_detail_not_found(self, MockDA):
        """Detail page returns 404 for missing fund."""
        MockDA.return_value.get_fund_by_id.return_value = None

        request = _make_request("/funder/fund/999/")
        with pytest.raises(Exception):
            FundDetailView.as_view()(request, pk=999)


# =========================================================================
# Step 4: Edit Fund
# =========================================================================


class TestStep4EditFund:
    """Step 4: Funder edits an existing fund."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_edit_form_renders_with_initial(self, MockDA):
        """Edit page pre-populates form with fund data."""
        fund = _make_fund_record(pk=1, name="Baobab Fund")
        MockDA.return_value.get_fund_by_id.return_value = fund

        request = _make_request("/funder/fund/1/edit/")
        response = FundEditView.as_view()(request, pk=1)

        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Edit Fund" in content
        assert "Baobab Fund" in content

    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_edit_fund_post(self, MockDA):
        """POST with valid data updates fund and redirects."""
        fund = _make_fund_record(pk=1, name="Old Name")
        MockDA.return_value.get_fund_by_id.return_value = fund
        MockDA.return_value.update_fund.return_value = _make_fund_record(pk=1, name="Updated Name")

        request = _make_request(
            "/funder/fund/1/edit/",
            method="POST",
            data={
                "name": "Updated Name",
                "description": "Updated description",
                "total_budget": "750000",
                "currency": "USD",
                "status": "active",
            },
        )
        response = FundEditView.as_view()(request, pk=1)

        assert response.status_code == 302
        assert response.url == "/funder/"
        MockDA.return_value.update_fund.assert_called_once()
        call_data = MockDA.return_value.update_fund.call_args[0][1]
        assert call_data["name"] == "Updated Name"
        assert call_data["total_budget"] == 750000


# =========================================================================
# Full Flow: Create → Detail → Edit
# =========================================================================


class TestFullFundFlow:
    """Walk through the full fund lifecycle in sequence."""

    @_CONTEXT_PATCH
    @patch("commcare_connect.funder_dashboard.views.FunderDashboardDataAccess")
    def test_create_view_edit_lifecycle(self, MockDA):
        """Full lifecycle: portfolio → create → detail → edit → portfolio."""
        # -- Step 1: Portfolio starts empty --
        MockDA.return_value.get_funds.return_value = []
        request = _make_request("/funder/")
        response = PortfolioDashboardView.as_view()(request)
        assert response.status_code == 200
        response.render()
        assert "No funds yet" in response.content.decode()

        # -- Step 2: Create a fund --
        created_fund = _make_fund_record(pk=50, name="Lifecycle Fund")
        MockDA.return_value.create_fund.return_value = created_fund

        request = _make_request(
            "/funder/fund/create/",
            method="POST",
            data={"name": "Lifecycle Fund", "status": "active", "currency": "USD"},
        )
        response = FundCreateView.as_view()(request)
        assert response.status_code == 302

        # -- Step 3: View the created fund --
        MockDA.return_value.get_fund_by_id.return_value = created_fund
        request = _make_request("/funder/fund/50/")
        response = FundDetailView.as_view()(request, pk=50)
        assert response.status_code == 200
        response.render()
        assert "Lifecycle Fund" in response.content.decode()

        # -- Step 4: Edit the fund --
        updated_fund = _make_fund_record(pk=50, name="Lifecycle Fund v2")
        MockDA.return_value.update_fund.return_value = updated_fund

        request = _make_request(
            "/funder/fund/50/edit/",
            method="POST",
            data={
                "name": "Lifecycle Fund v2",
                "status": "active",
                "currency": "USD",
            },
        )
        response = FundEditView.as_view()(request, pk=50)
        assert response.status_code == 302

        # -- Step 5: Portfolio now shows the fund --
        MockDA.return_value.get_funds.return_value = [updated_fund]
        request = _make_request("/funder/")
        response = PortfolioDashboardView.as_view()(request)
        assert response.status_code == 200
        response.render()
        content = response.content.decode()
        assert "Lifecycle Fund v2" in content
        assert "No funds yet" not in content
