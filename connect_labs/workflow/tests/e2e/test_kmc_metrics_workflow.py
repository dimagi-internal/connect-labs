"""
E2E test for the kmc_project_metrics workflow template.

Tests the full happy path:
1. Navigate to workflow list, create workflow from KMC Project Metrics template
2. Create a new run
3. Verify Overview renders (KPI cards visible)
4. Navigate to Outcomes & Outputs tab
5. Verify charts section renders
6. Navigate to Indicators Table tab
7. Verify table renders with indicator rows

Run:
    pytest connect_labs/workflow/tests/e2e/test_kmc_metrics_workflow.py \
        --ds=config.settings.local -o "addopts=" -v --opportunity-id=874
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestKMCProjectMetricsWorkflow:
    """E2E test for the kmc_project_metrics workflow template."""

    def test_full_kmc_metrics_workflow(self, auth_page, live_server_url, opportunity_id):
        """Test creating and navigating a KMC project metrics workflow end-to-end."""
        page = auth_page
        page.set_default_timeout(120_000)

        # --- Step 1: Navigate to workflow list ---
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        create_btn = page.get_by_role("button", name="Create Workflow")
        expect(create_btn).to_be_visible(timeout=10_000)
        create_btn.click()

        page.get_by_text("Choose a Template").wait_for(timeout=10_000)

        modal = page.locator(".fixed.inset-0.z-50")
        csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/labs/workflow/create/",
            form={"csrfmiddlewaretoken": csrf_token, "template": "kmc_project_metrics"},
            timeout=60_000,
        )
        assert response.ok or response.status == 302, f"create_from_template failed: {response.status}"

        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # --- Step 2: Create a new run ---
        metrics_cards = page.locator('[data-workflow-template="kmc_project_metrics"]')
        metrics_card = metrics_cards.last
        expect(metrics_card).to_be_visible(timeout=10_000)
        create_run_link = metrics_card.get_by_role("link", name="Create Run")
        expect(create_run_link).to_be_visible(timeout=10_000)
        create_run_link.click()
        page.wait_for_load_state("domcontentloaded")

        # --- Step 3: Verify Overview renders ---
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for dynamic React content to render
        page.locator("text=/SVNs Enrolled/i").wait_for(timeout=60_000)

        # --- Step 4: Navigate to Outcomes & Outputs ---
        outcomes_tab = page.get_by_role("button", name=re.compile(r"Outcomes", re.IGNORECASE))
        expect(outcomes_tab).to_be_visible(timeout=10_000)
        outcomes_tab.click()

        # Verify outcomes content
        page.locator("text=/KMC Practice/i").wait_for(timeout=30_000)

        # --- Step 5: Navigate to Indicators Table ---
        indicators_tab = page.get_by_role("button", name=re.compile(r"Indicators", re.IGNORECASE))
        expect(indicators_tab).to_be_visible(timeout=10_000)
        indicators_tab.click()

        # Verify table renders
        page.locator("table").wait_for(timeout=30_000)

        # --- Step 6: Navigate back to Overview ---
        overview_tab = page.get_by_role("button", name=re.compile(r"Overview", re.IGNORECASE))
        expect(overview_tab).to_be_visible(timeout=10_000)
        overview_tab.click()

        # Check for JS errors
        critical_errors = [e for e in console_errors if "babel" not in e.lower() and "404" not in e]
        assert len(critical_errors) == 0, f"Console errors: {critical_errors}"
