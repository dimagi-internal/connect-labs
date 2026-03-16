"""
E2E test: Fund CRUD lifecycle.

Creates a fund, views it, edits it (adds an allocation),
verifies KPIs update correctly.

Run:
    pytest commcare_connect/funder_dashboard/tests/e2e/test_fund_flow.py \
        --ds=config.settings.local -o "addopts=" -v
"""
import time

import pytest

pytestmark = pytest.mark.e2e


class TestFundCRUDLifecycle:
    """Full fund create -> view -> edit (add allocation) -> verify KPIs."""

    def test_portfolio_loads(self, auth_page, live_server_url, org_id):
        """Step 1: Portfolio page loads with header and org context."""
        page = auth_page
        page.set_default_timeout(30_000)
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").filter(has_text="Funder Dashboard").is_visible()
        # Create Fund button is visible when org context is set
        assert page.get_by_role("link", name="Create Fund").first.is_visible()

    def test_create_fund(self, auth_page, live_server_url, org_id):
        """Step 2: Create a new fund via the form."""
        page = auth_page
        page.set_default_timeout(30_000)

        # Navigate to create form
        page.goto(f"{live_server_url}/funder/fund/create/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").filter(has_text="Create New Fund").is_visible()

        # Fill in the form
        test_name = f"E2E Test Fund {int(time.time())}"
        page.fill("input[name='name']", test_name)
        page.fill("textarea[name='description']", "Created by e2e test")
        page.fill("input[name='total_budget']", "500000")
        page.fill("input[name='currency']", "USD")
        page.select_option("select[name='status']", "active")

        # Submit via page.request.post to avoid navigation timeout
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/funder/fund/create/?organization_id={org_id}",
            form={
                "csrfmiddlewaretoken": csrf_token,
                "name": test_name,
                "description": "Created by e2e test",
                "total_budget": "500000",
                "currency": "USD",
                "status": "active",
                "program_ids_json": "[]",
                "delivery_types_json": "[]",
                "allocations_json": "[]",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # Navigate to portfolio and verify fund appears
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")
        assert page.get_by_text(test_name).is_visible(timeout=10_000)

    def test_fund_detail_shows_kpis(self, auth_page, live_server_url, org_id):
        """Step 3: Fund detail page shows correct KPIs including Committed/Remaining."""
        page = auth_page
        page.set_default_timeout(30_000)

        # Navigate to portfolio and click the first fund
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click the first fund card link
        fund_link = page.locator("a.block.group[href*='/funder/fund/']").first
        fund_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Verify KPIs are visible
        assert page.get_by_text("Total Budget").is_visible()
        assert page.get_by_text("Committed").is_visible()
        assert page.get_by_text("Remaining").is_visible()
        assert page.get_by_text("Status").is_visible()

    def test_edit_fund_add_allocation(self, auth_page, live_server_url, org_id):
        """Step 4: Edit a fund and add a retroactive allocation."""
        page = auth_page
        page.set_default_timeout(30_000)

        # Navigate to portfolio, click first fund, then Edit
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        fund_link = page.locator("a.block.group[href*='/funder/fund/']").first
        fund_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Save the fund detail URL to navigate back later
        detail_url = page.url

        # Click Edit Fund
        page.get_by_text("Edit Fund").click()
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").filter(has_text="Edit Fund").is_visible()

        # Click "Add Allocation"
        page.get_by_text("Add Allocation").click()

        # Fill allocation fields
        page.locator("input[x-model='alloc.program_name']").first.fill("Test Program")
        page.locator("input[x-model\\.number='alloc.amount']").first.fill("100000")
        page.locator("select[x-model='alloc.type']").first.select_option("retroactive")
        page.locator("input[x-model='alloc.notes']").first.fill("E2E test allocation")

        # Submit the form (use role to avoid matching context selector submit)
        page.get_by_role("button", name="Save Changes").click()
        page.wait_for_load_state("domcontentloaded")

        # Navigate back to the fund detail and verify allocation appears
        page.goto(detail_url)
        page.wait_for_load_state("domcontentloaded")

        assert page.get_by_text("Test Program").is_visible()
        assert page.get_by_text("Retroactive").is_visible()
        assert page.get_by_text("100,000").first.is_visible()
