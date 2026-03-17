"""
E2E test for the kmc_flw_flags workflow template (KMC FLW Flag Report).

Tests the happy path:
1. Navigate to workflow list, create workflow from FLW Flag Report template
2. Create a new run
3. Verify React UI renders (KPI cards, filter bar, table, action bar)
4. Clean up by deleting the workflow run and definition

Run:
    pytest commcare_connect/workflow/tests/e2e/test_flw_flags_workflow.py \
        --ds=config.settings.local -o "addopts=" -v --opportunity-id=874
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestKMCFLWFlagsWorkflow:
    """E2E test for the kmc_flw_flags workflow template."""

    def test_flag_report_renders(self, auth_page, live_server_url, opportunity_id):
        """Test creating a FLW Flag Report workflow and verifying the UI renders."""
        page = auth_page
        page.set_default_timeout(120_000)  # 2min -- pipeline loading can be slow

        # --- Step 1: Navigate to workflow list ---
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click "Create Workflow" to open the template modal
        create_btn = page.get_by_role("button", name="Create Workflow")
        expect(create_btn).to_be_visible(timeout=10_000)
        create_btn.click()

        # Wait for the template modal to appear
        page.get_by_text("Choose a Template").wait_for(timeout=10_000)

        # Select the FLW Flag Report template -- scope to modal to avoid conflicts
        modal = page.locator(".fixed.inset-0.z-50")
        flw_template_btn = modal.locator("button[type='submit']").filter(has_text="KMC FLW Flag Report")
        expect(flw_template_btn).to_be_visible()

        # Get CSRF token and submit via API (avoids Playwright navigation timeout)
        csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/labs/workflow/create/",
            form={"csrfmiddlewaretoken": csrf_token, "template": "kmc_flw_flags"},
            timeout=60_000,
        )
        assert response.ok or response.status == 302, f"create_from_template failed: {response.status}"

        # Reload the workflow list to pick up the new workflow
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # --- Step 2: Create a new run ---
        # Scope to the last FLW flags workflow card (most recently created)
        flw_cards = page.locator('[data-workflow-template="kmc_flw_flags"]')
        flw_card = flw_cards.last
        expect(flw_card).to_be_visible(timeout=10_000)
        create_run_link = flw_card.get_by_role("link", name="Create Run")
        expect(create_run_link).to_be_visible(timeout=10_000)
        create_run_link.click()
        page.wait_for_load_state("domcontentloaded")

        # --- Step 3: Verify React UI renders ---
        # Capture console errors for debugging transpilation/runtime failures
        console_errors = []
        page.on(
            "console",
            lambda msg: console_errors.append(f"[{msg.type}] {msg.text}")
            if msg.type in ("error", "warning")
            else None,
        )

        # Wait for Babel transpilation and WorkflowUI to mount.
        # The FLW Flag Report shows either KPI cards (when data loads) or
        # "Loading..." (while pipeline is loading) or "No data" style messages.
        dashboard_indicator = (
            page.get_by_text("FLWs Analyzed")
            .or_(page.get_by_text("Loading workflow..."))
            .or_(page.get_by_text("Loading..."))
            .or_(page.get_by_text("No data"))
        )
        try:
            dashboard_indicator.first.wait_for(timeout=120_000)
        except Exception:
            # Dump debug info before failing
            page.screenshot(path="e2e_flw_flags_debug.png")
            with open("e2e_flw_flags_console.txt", "w") as f:
                f.write(f"URL: {page.url}\n\n")
                f.write("=== CONSOLE MESSAGES ===\n")
                for err in console_errors:
                    f.write(err + "\n")
                f.write("\n=== PAGE TEXT ===\n")
                f.write(page.locator("body").inner_text()[:3000])
            raise

        # Check whether pipeline data loaded
        has_data = page.get_by_text("FLWs Analyzed").is_visible(timeout=5_000)

        # Scope all assertions to the workflow root to avoid matching nav bar text
        wf_root = page.locator("#workflow-root")

        if has_data:
            # --- Verify KPI cards ---
            expect(wf_root.get_by_text("FLWs Analyzed")).to_be_visible()
            expect(wf_root.get_by_text("Total Cases")).to_be_visible()
            expect(wf_root.get_by_text("With 2+ Flags")).to_be_visible()

            # --- Verify filter bar ---
            expect(wf_root.get_by_text("All FLWs")).to_be_visible()
            expect(wf_root.get_by_text("Any Flag")).to_be_visible()
            expect(wf_root.get_by_role("button", name=re.compile(r"2\+ Flags"))).to_be_visible()

            # --- Verify table headers ---
            expect(wf_root.get_by_text("Visits/Case").first).to_be_visible()
            expect(wf_root.get_by_text("Flags").first).to_be_visible()

            # --- Screenshot for review ---
            page.screenshot(path="e2e_flw_flags_ui.png", full_page=True)

            # --- Verify action bar ---
            expect(wf_root.get_by_text(re.compile(r"Create Audits with AI Review"))).to_be_visible()
        else:
            # No data or still loading -- just confirm React rendered without error
            loading_or_empty = (
                wf_root.get_by_text("Loading...")
                .or_(wf_root.get_by_text("No data"))
                .or_(wf_root.get_by_text("Loading workflow..."))
            )
            expect(loading_or_empty.first).to_be_visible()

        # --- Step 4: Cleanup ---
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
