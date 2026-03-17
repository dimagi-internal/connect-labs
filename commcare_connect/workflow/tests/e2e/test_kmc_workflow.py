"""
E2E test for the kmc_longitudinal workflow template.

Tests the full happy path:
1. Navigate to workflow list, create workflow from KMC template
2. Create a new run
3. Verify React dashboard renders (KPI cards visible)
4. Navigate to child list via a KPI card click
5. Verify child list renders with table
6. Navigate to a child timeline (if children exist)
7. Verify timeline renders (header, visit sidebar, chart, map, detail panel)
8. Navigate back via tabs
9. Clean up

Run:
    pytest commcare_connect/workflow/tests/e2e/test_kmc_workflow.py \
        --ds=config.settings.local -o "addopts=" -v --opportunity-id=874
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestKMCLongitudinalWorkflow:
    """E2E test for the kmc_longitudinal workflow template."""

    def test_full_kmc_workflow(self, auth_page, live_server_url, opportunity_id):
        """Test creating and navigating a KMC longitudinal workflow end-to-end."""
        page = auth_page
        page.set_default_timeout(120_000)  # 2min — pipeline loading can be slow

        # --- Step 1: Navigate to workflow list ---
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click "Create Workflow" to open the template modal
        create_btn = page.get_by_role("button", name="Create Workflow")
        expect(create_btn).to_be_visible(timeout=10_000)
        create_btn.click()

        # Wait for the template modal to appear
        page.get_by_text("Choose a Template").wait_for(timeout=10_000)

        # Select the KMC template — scope to modal to avoid card name conflicts
        modal = page.locator(".fixed.inset-0.z-50")
        kmc_template_btn = modal.locator("button[type='submit']").filter(has_text="KMC Longitudinal Tracking")
        expect(kmc_template_btn).to_be_visible()

        # Get CSRF token and submit via API (avoids Playwright navigation timeout)
        csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/labs/workflow/create/",
            form={"csrfmiddlewaretoken": csrf_token, "template": "kmc_longitudinal"},
            timeout=60_000,
        )
        assert response.ok or response.status == 302, f"create_from_template failed: {response.status}"

        # Reload the workflow list to pick up the new workflow
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # --- Step 2: Create a new run ---
        # Scope to the *last* KMC workflow card (most recently created) to
        # avoid clicking "Create Run" on a stale workflow or different template.
        kmc_cards = page.locator('[data-workflow-template="kmc_longitudinal"]')
        kmc_card = kmc_cards.last
        expect(kmc_card).to_be_visible(timeout=10_000)
        create_run_link = kmc_card.get_by_role("link", name="Create Run")
        expect(create_run_link).to_be_visible(timeout=10_000)
        create_run_link.click()
        page.wait_for_load_state("domcontentloaded")

        # --- Step 3: Verify React dashboard renders ---
        # Capture console errors for debugging transpilation/runtime failures
        console_errors = []
        page.on(
            "console",
            lambda msg: console_errors.append(f"[{msg.type}] {msg.text}")
            if msg.type in ("error", "warning")
            else None,
        )

        # Wait for Babel transpilation and WorkflowUI to mount.
        # The KMC dashboard shows either KPI cards (when data loads) or
        # "Loading visit data..." (while pipeline is loading) or
        # "No KMC visit data found" (if no data for this opportunity).
        # Also check for "Loading workflow..." (initial Babel loading state).
        #
        # Wait for any of these to confirm React rendered successfully.
        dashboard_indicator = (
            page.get_by_text("Total Children")
            .or_(page.get_by_text("Loading visit data..."))
            .or_(page.get_by_text("No KMC visit data found"))
            .or_(page.get_by_text("Loading workflow..."))
        )
        try:
            dashboard_indicator.first.wait_for(timeout=60_000)
        except Exception:
            # Dump debug info before failing
            page.screenshot(path="e2e_kmc_debug.png")
            with open("e2e_kmc_console.txt", "w") as f:
                f.write(f"URL: {page.url}\n\n")
                f.write("=== CONSOLE MESSAGES ===\n")
                for err in console_errors:
                    f.write(err + "\n")
                f.write("\n=== PAGE TEXT ===\n")
                f.write(page.locator("body").inner_text()[:3000])
            raise

        # Check which state we're in
        has_data = page.get_by_text("Total Children").is_visible(timeout=5_000)

        # Scope all assertions to the workflow root to avoid matching nav bar text
        wf_root = page.locator("#workflow-root")

        if has_data:
            # --- Step 4: Verify KPI cards ---
            # All 6 KPI cards should be visible
            expect(wf_root.get_by_text("Total Children")).to_be_visible()
            expect(wf_root.get_by_text("Active", exact=True)).to_be_visible()
            expect(wf_root.get_by_text("Overdue >14 days")).to_be_visible()
            expect(wf_root.get_by_text("Below Avg Gain")).to_be_visible()
            expect(wf_root.get_by_text("Reached 2.5kg")).to_be_visible()
            expect(wf_root.get_by_text("Discharged")).to_be_visible()

            # Verify summary text
            expect(wf_root.get_by_text(re.compile(r"total visits across"))).to_be_visible()

            # Verify navigation tabs are present
            expect(wf_root.get_by_role("button", name="Dashboard", exact=True)).to_be_visible()
            expect(wf_root.get_by_role("button", name=re.compile(r"All Children"))).to_be_visible()

            # --- Step 5: Navigate to child list via KPI card ---
            # Click "Total Children" card to go to child list with 'all' filter
            wf_root.get_by_text("Total Children").click()

            # Verify child list renders with a table
            child_table = wf_root.locator("table")
            expect(child_table).to_be_visible(timeout=10_000)

            # Verify table headers
            expect(wf_root.get_by_text("Child Name").first).to_be_visible()
            expect(wf_root.get_by_text("Current Weight").first).to_be_visible()

            # Verify filter dropdown is present
            filter_select = wf_root.locator("select").first
            expect(filter_select).to_be_visible()

            # --- Step 6: Navigate to a child timeline ---
            # Click the first row in the table to open a child timeline
            first_row = wf_root.locator("tbody tr").first
            if first_row.is_visible(timeout=5_000):
                first_row.click()

                # --- Step 7: Verify timeline renders ---
                # The timeline should show the child header and visit sidebar
                # Wait for the 3-column grid to render
                visit_sidebar_header = wf_root.get_by_text(re.compile(r"Visits \(\d+\)"))
                expect(visit_sidebar_header).to_be_visible(timeout=10_000)

                # Verify Weight Progression chart container
                expect(wf_root.get_by_text("Weight Progression")).to_be_visible()

                # Verify Visit Locations section (map or "No GPS data")
                visit_locations = wf_root.get_by_text("Visit Locations").or_(
                    wf_root.get_by_text("No GPS data available")
                )
                expect(visit_locations.first).to_be_visible()

                # Verify clinical detail panel sections
                expect(wf_root.get_by_text("Anthropometric")).to_be_visible()
                expect(wf_root.get_by_text("KMC Practice")).to_be_visible()
                expect(wf_root.get_by_text("Vital Signs")).to_be_visible()

                # Verify the child name tab appeared in navigation
                child_name_tab = wf_root.locator(".border-b.border-gray-200 button").nth(2)  # Third tab = child name
                expect(child_name_tab).to_be_visible()

                # --- Step 8: Navigate back via tabs ---
                # Click "All Children" tab
                wf_root.get_by_role("button", name=re.compile(r"All Children")).click()
                expect(child_table).to_be_visible(timeout=10_000)

                # Click "Dashboard" tab (exact=True to avoid matching "Back to Dashboard")
                wf_root.get_by_role("button", name="Dashboard", exact=True).click()
                expect(wf_root.get_by_text("Total Children")).to_be_visible(timeout=10_000)

        else:
            # No data — verify the empty/loading state rendered correctly
            no_data = wf_root.get_by_text("No KMC visit data found").or_(wf_root.get_by_text("Loading visit data..."))
            expect(no_data.first).to_be_visible()

        # --- Step 9: Cleanup ---
        # Delete the workflow run AND workflow definition to avoid polluting labs records
        current_url = page.url
        run_id_match = re.search(r"run_id=(\d+)", current_url)
        workflow_id_match = re.search(r"/workflow/(\d+)/run/", current_url)
        csrf_token = page.evaluate("document.querySelector('#workflow-root')?.dataset?.csrfToken || ''")
        if csrf_token:
            if run_id_match:
                run_id = run_id_match.group(1)
                page.request.post(
                    f"{live_server_url}/labs/workflow/api/run/{run_id}/delete/" f"?opportunity_id={opportunity_id}",
                    headers={"X-CSRFToken": csrf_token},
                )
            if workflow_id_match:
                wf_id = workflow_id_match.group(1)
                page.request.post(
                    f"{live_server_url}/labs/workflow/api/{wf_id}/delete/" f"?opportunity_id={opportunity_id}",
                    headers={"X-CSRFToken": csrf_token},
                )
