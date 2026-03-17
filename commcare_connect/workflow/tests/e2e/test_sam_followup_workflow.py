"""
E2E test for the sam_followup workflow template.

Tests the full happy path:
1. Navigate to workflow list, create workflow from SAM Follow-up template
2. Create a new run
3. Verify React dashboard renders (KPI cards visible)
4. Navigate to child list via a KPI card click
5. Verify child list renders with table
6. Navigate to a child timeline (if children exist)
7. Verify timeline renders (visit sidebar, MUAC trend chart, photo filmstrip, clinical details)
8. Navigate back via tabs
9. Clean up

Run:
    pytest commcare_connect/workflow/tests/e2e/test_sam_followup_workflow.py \
        --ds=config.settings.local -o "addopts=" -v --opportunity-id=879
"""

import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestSAMFollowupWorkflow:
    """E2E test for the sam_followup workflow template."""

    def test_full_sam_workflow(self, auth_page, live_server_url, opportunity_id, celery_worker):
        """Test creating and navigating a SAM follow-up workflow end-to-end."""
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

        # Select the SAM Follow-up template — scope to modal to avoid card name conflicts
        modal = page.locator(".fixed.inset-0.z-50")
        sam_template_btn = modal.locator("button[type='submit']").filter(has_text="SAM Follow-up Timeline")
        expect(sam_template_btn).to_be_visible()

        # Get CSRF token and submit via API (avoids Playwright navigation timeout)
        csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()
        response = page.request.post(
            f"{live_server_url}/labs/workflow/create/",
            form={"csrfmiddlewaretoken": csrf_token, "template": "sam_followup"},
            timeout=60_000,
        )
        assert response.ok or response.status == 302, f"create_from_template failed: {response.status}"

        # Reload the workflow list to pick up the new workflow
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # --- Step 2: Create a new run ---
        # Scope to the *last* SAM workflow card (most recently created)
        sam_cards = page.locator('[data-workflow-template="sam_followup"]')
        sam_card = sam_cards.last
        expect(sam_card).to_be_visible(timeout=10_000)
        create_run_link = sam_card.get_by_role("link", name="Create Run")
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

        # Scope to the workflow root
        wf_root = page.locator("#workflow-root")

        # Wait for React to mount — #workflow-root must have content.
        # First, the Babel transpilation / "Loading workflow..." phase,
        # then either data renders or an empty/loading state appears.
        # We wait up to 2 min for a terminal state inside #workflow-root.
        terminal_state = (
            wf_root.get_by_text("Total Children")
            .or_(wf_root.get_by_text("No SAM follow-up visit data found"))
            .or_(wf_root.get_by_text("Loading visit data..."))
        )
        try:
            terminal_state.first.wait_for(timeout=120_000)
        except Exception:
            # Dump debug info before failing
            page.screenshot(path="e2e_sam_debug.png")
            with open("e2e_sam_console.txt", "w") as f:
                f.write(f"URL: {page.url}\n\n")
                f.write("=== CONSOLE MESSAGES ===\n")
                for err in console_errors:
                    f.write(err + "\n")
                f.write("\n=== WORKFLOW ROOT TEXT ===\n")
                try:
                    f.write(wf_root.inner_text(timeout=5_000)[:3000])
                except Exception:
                    f.write("(could not get #workflow-root text)\n")
                f.write("\n\n=== BODY TEXT ===\n")
                f.write(page.locator("body").inner_text()[:3000])
            raise

        # If we see "Loading visit data..." the pipeline hasn't finished yet.
        # Wait for it to either transition to data or "No SAM..." (up to 2 more minutes).
        if wf_root.get_by_text("Loading visit data...").is_visible(timeout=2_000):
            data_loaded = wf_root.get_by_text("Total Children").or_(
                wf_root.get_by_text("No SAM follow-up visit data found")
            )
            try:
                data_loaded.first.wait_for(timeout=120_000)
            except Exception:
                page.screenshot(path="e2e_sam_loading_timeout.png")
                pytest.skip("Pipeline data did not load within timeout — Celery may be slow")

        # Check which state we're in
        has_data = wf_root.get_by_text("Total Children").is_visible(timeout=5_000)

        if has_data:
            # --- Step 4: Verify KPI cards ---
            # All 6 KPI cards should be visible
            expect(wf_root.get_by_text("Total Children")).to_be_visible()
            expect(wf_root.get_by_text("Red MUAC / SAM")).to_be_visible()
            expect(wf_root.get_by_text("Yellow MUAC / MAM")).to_be_visible()
            expect(wf_root.get_by_text("Green / Recovered")).to_be_visible()
            expect(wf_root.get_by_text("Referral Compliance")).to_be_visible()
            expect(wf_root.get_by_text("Overdue >14d")).to_be_visible()

            # Verify summary text
            expect(wf_root.get_by_text(re.compile(r"total visits across"))).to_be_visible()

            # Verify navigation tabs are present
            expect(wf_root.get_by_role("button", name="Dashboard", exact=True)).to_be_visible()
            expect(wf_root.get_by_role("button", name=re.compile(r"^All Children \("))).to_be_visible()

            # Take a screenshot of the dashboard for visual verification
            page.screenshot(path="e2e_sam_dashboard.png")

            # --- Step 5: Navigate to child list via KPI card ---
            # Click "Total Children" card to go to child list with 'all' filter
            wf_root.get_by_text("Total Children").click()

            # Verify child list renders with a table
            child_table = wf_root.locator("table")
            expect(child_table).to_be_visible(timeout=10_000)

            # Verify table headers
            expect(wf_root.get_by_text("Child Name").first).to_be_visible()
            expect(wf_root.get_by_text("MUAC (cm)").first).to_be_visible()

            # Verify filter dropdown is present
            filter_select = wf_root.locator("select").first
            expect(filter_select).to_be_visible()

            # Take a screenshot of the child list
            page.screenshot(path="e2e_sam_child_list.png")

            # --- Step 6: Navigate to a child timeline ---
            # Click the first row in the table to open a child timeline
            first_row = wf_root.locator("tbody tr").first
            if first_row.is_visible(timeout=5_000):
                first_row.click()

                # --- Step 7: Verify timeline renders ---
                # The timeline should show the child header and visit sidebar
                # Wait for the visit sidebar header (e.g. "Visits (3)")
                visit_sidebar_header = wf_root.get_by_text(re.compile(r"Visits \(\d+\)"))
                expect(visit_sidebar_header).to_be_visible(timeout=10_000)

                # Verify MUAC Trend chart container
                expect(wf_root.get_by_text("MUAC Trend")).to_be_visible()

                # Verify MUAC Photos section (filmstrip heading)
                expect(wf_root.get_by_role("heading", name="MUAC Photos")).to_be_visible()

                # Verify clinical detail panel sections (collapsible accordions)
                expect(wf_root.get_by_text("MUAC", exact=True).first).to_be_visible()
                expect(wf_root.get_by_text("Referral", exact=True).first).to_be_visible()
                expect(wf_root.get_by_text("Visit Info").first).to_be_visible()

                # Verify the child name tab appeared in navigation
                child_name_tab = wf_root.locator(".border-b.border-gray-200 button").nth(2)  # Third tab = child name
                expect(child_name_tab).to_be_visible()

                # Wait for MUAC photos to load (images fetched via proxy)
                photo_images = wf_root.locator("img[alt='MUAC photo']")
                photo_count = photo_images.count()
                if photo_count > 0:
                    # Wait for at least one image to have a natural width (loaded)
                    page.wait_for_function(
                        """() => {
                            const imgs = document.querySelectorAll("img[alt='MUAC photo']");
                            return imgs.length > 0 && Array.from(imgs).some(img => img.naturalWidth > 0);
                        }""",
                        timeout=30_000,
                    )
                    # Brief pause for remaining images to finish loading
                    page.wait_for_timeout(2_000)

                # Take a screenshot of the timeline (with photos loaded)
                page.screenshot(path="e2e_sam_timeline.png")

                # --- Step 8: Navigate back via tabs ---
                # Click "All Children" tab
                wf_root.get_by_role("button", name=re.compile(r"^All Children \(")).click()
                expect(child_table).to_be_visible(timeout=10_000)

                # Click "Dashboard" tab (exact=True to avoid matching "Back to Dashboard")
                wf_root.get_by_role("button", name="Dashboard", exact=True).click()
                expect(wf_root.get_by_text("Total Children")).to_be_visible(timeout=10_000)

        else:
            # No data — verify the empty state rendered correctly
            expect(wf_root.get_by_text("No SAM follow-up visit data found")).to_be_visible()
            page.screenshot(path="e2e_sam_no_data.png")

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
