"""
E2E test: Solicitation award flow with fund auto-allocation.

Creates a solicitation linked to a fund, submits a response,
awards it, and verifies the allocation appears on the fund.

Run:
    pytest commcare_connect/funder_dashboard/tests/e2e/test_award_flow.py \
        --ds=config.settings.local -o "addopts=" -v
"""
import time

import pytest

pytestmark = pytest.mark.e2e


class TestAwardWithFundAllocation:
    """Award a solicitation response and verify fund allocation is created."""

    def test_full_award_flow(self, auth_page, live_server_url, org_id, program_id):
        """
        End-to-end:
        1. Create a fund
        2. Create a solicitation linked to the fund
        3. Submit a response
        4. Award the response
        5. Verify fund detail shows the auto-allocation
        """
        page = auth_page
        page.set_default_timeout(30_000)
        timestamp = int(time.time())

        # --- Step 1: Create a fund ---
        fund_name = f"E2E Award Fund {timestamp}"
        csrf_url = f"{live_server_url}/funder/fund/create/?organization_id={org_id}"
        page.goto(csrf_url)
        page.wait_for_load_state("domcontentloaded")
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            csrf_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "name": fund_name,
                "description": "E2E award test fund",
                "total_budget": "1000000",
                "currency": "USD",
                "status": "active",
                "program_ids_json": "[]",
                "delivery_types_json": "[]",
                "allocations_json": "[]",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # Find the fund to get its ID
        page.goto(f"{live_server_url}/funder/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")
        fund_link = page.locator(f"a:has-text('{fund_name}')").first  # noqa: E231
        fund_href = fund_link.get_attribute("href")
        fund_id = fund_href.strip("/").split("/")[-1]

        # --- Step 2: Create a solicitation linked to the fund ---
        sol_title = f"E2E Test RFP {timestamp}"
        sol_url = f"{live_server_url}/solicitations_new/create/?program_id={program_id}"
        page.goto(sol_url)
        page.wait_for_load_state("domcontentloaded")
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            sol_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "title": sol_title,
                "description": "E2E test solicitation",
                "solicitation_type": "rfp",
                "status": "active",
                "is_public": "true",
                "questions_json": "[]",
                "fund_id": fund_id,
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # Find the solicitation ID
        page.goto(f"{live_server_url}/solicitations_new/manage/?program_id={program_id}")
        page.wait_for_load_state("domcontentloaded")
        sol_link = page.locator(f"a:has-text('{sol_title}')").first  # noqa: E231
        sol_href = sol_link.get_attribute("href")
        sol_id = sol_href.strip("/").split("/")[-1]

        # --- Step 3: Submit a response ---
        respond_url = f"{live_server_url}/solicitations_new/{sol_id}/respond/?program_id={program_id}"
        page.goto(respond_url)
        page.wait_for_load_state("domcontentloaded")
        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        response = page.request.post(
            respond_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "submit": "true",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # --- Step 4: Find the response and award it ---
        page.goto(f"{live_server_url}/solicitations_new/{sol_id}/responses/?program_id={program_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click the first View link in the responses table
        view_link = page.locator("a:has-text('View')").first
        view_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Click Award button
        award_link = page.locator("a:has-text('Award')").first
        award_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Fill award form
        page.fill("input[name='org_id']", org_id)
        page.fill("input[name='reward_budget']", "250000")

        csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        award_url = page.url
        response = page.request.post(
            award_url,
            form={
                "csrfmiddlewaretoken": csrf_token,
                "org_id": org_id,
                "reward_budget": "250000",
            },
            timeout=60_000,
        )
        assert response.ok or response.status == 302

        # --- Step 5: Verify fund allocation ---
        page.goto(f"{live_server_url}/funder/fund/{fund_id}/?organization_id={org_id}")
        page.wait_for_load_state("domcontentloaded")

        # Verify the auto-allocation appears
        assert page.get_by_text("Award").first.is_visible()
        assert page.get_by_text("250,000").is_visible()
        # KPIs should reflect the allocation
        assert page.get_by_text("Committed").is_visible()
