"""
E2E test for the audit_with_ai_review workflow template.

Tests the full happy path:
1. Navigate to workflow list, create workflow from audit template
2. Create a new run
3. Verify React UI renders (Babel transpilation)
4. Select "Last N Visits" mode with a small count (10)
5. Trigger audit creation and capture task_id
6. Poll task status until completion
7. Verify sessions appear (if any were created)
8. Clean up

Run:
    pytest commcare_connect/workflow/tests/e2e/ --ds=config.settings.local -o "addopts=" -v --opportunity-id=874
"""

import re
import time

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestAuditWithAIReviewWorkflow:
    """E2E test for the audit_with_ai_review workflow template."""

    def test_full_audit_workflow(self, auth_page, live_server_url, opportunity_id, celery_worker):
        """Test creating and running an audit workflow end-to-end."""
        page = auth_page
        page.set_default_timeout(600_000)  # 10min — covers API fetch + AI review of ~10 visits

        # --- Step 1: Navigate to workflow list ---
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # Click "Create Workflow" to open the template modal
        create_btn = page.get_by_role("button", name="Create Workflow")
        expect(create_btn).to_be_visible(timeout=10_000)
        create_btn.click()

        # Wait for the template modal to appear
        page.get_by_text("Choose a Template").wait_for(timeout=10_000)

        # Select the audit template — it's a submit button inside a <form> in the modal.
        # Scope to the modal (z-50 overlay) to avoid matching the workflow name button on the card.
        modal = page.locator(".fixed.inset-0.z-50")
        audit_template_btn = modal.locator("button[type='submit']").filter(has_text="Weekly Audit with AI Review")
        expect(audit_template_btn).to_be_visible()

        # Get CSRF token from the form before submitting
        csrf_token = modal.locator("input[name='csrfmiddlewaretoken']").first.input_value()

        # Submit via API to avoid Playwright navigation timeout (server-side creates via prod API)
        response = page.request.post(
            f"{live_server_url}/labs/workflow/create/",
            form={"csrfmiddlewaretoken": csrf_token, "template": "audit_with_ai_review"},
            timeout=60_000,
        )
        assert response.ok or response.status == 302, f"create_from_template failed: {response.status}"

        # Reload the workflow list to pick up the new workflow
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("domcontentloaded")

        # --- Step 2: Create a new run ---
        # Find the "Create Run" link on the newly created workflow card
        create_run_link = page.get_by_role("link", name="Create Run").first
        expect(create_run_link).to_be_visible(timeout=10_000)
        create_run_link.click()
        page.wait_for_load_state("domcontentloaded")

        # --- Step 3: Verify React UI renders ---
        # Wait for Babel to transpile and the WorkflowUI component to mount
        # The audit UI shows "Visit Selection" heading when rendered
        page.get_by_text("Visit Selection").wait_for(timeout=30_000)

        # Verify the mode buttons are visible (Date Range is default/active)
        expect(page.get_by_role("button", name="Date Range")).to_be_visible()
        expect(page.get_by_role("button", name="Last N Visits")).to_be_visible()

        # --- Step 4: Select "Last N Visits" mode with a small count ---
        # Switch to "Last N Visits" mode to keep the test fast (~10 visits = ~2 min AI review)
        last_n_btn = page.get_by_role("button", name="Last N Visits")
        last_n_btn.click()

        # Set the count to 10 visits
        count_input = page.locator("input[type='number'][min='1']")
        expect(count_input).to_be_visible(timeout=5_000)
        count_input.fill("10")

        # --- Step 5: Trigger audit creation and capture task_id ---
        create_audit_btn = page.get_by_role("button", name=re.compile("Create Weekly Audit"))
        expect(create_audit_btn).to_be_visible()
        expect(create_audit_btn).to_be_enabled()

        # Intercept the API response to capture the task_id
        with page.expect_response("**/audit/api/audit/create-async/") as response_info:
            create_audit_btn.click()

        api_response = response_info.value
        assert api_response.ok, f"create-async failed: {api_response.status}"
        api_body = api_response.json()
        assert api_body.get("success"), f"create-async returned: {api_body}"
        task_id = api_body["task_id"]

        # --- Step 6: Poll task status until completion ---
        # Poll the status API directly to verify the Celery task completes,
        # independent of the React UI's SSE/polling behavior.
        # 10 visits with images: ~2 min API fetch + ~2 min AI review = ~4 min total.
        for attempt in range(60):
            status_resp = page.request.get(
                f"{live_server_url}/audit/api/audit/task/{task_id}/status/" f"?opportunity_id={opportunity_id}",
            )
            assert status_resp.ok, f"status check failed: {status_resp.status}"
            status = status_resp.json()

            if status["status"] in ("completed", "failed"):
                break
            time.sleep(5)
        else:
            pytest.fail(f"Task {task_id} did not complete within 5 minutes. Last status: {status}")

        assert status["status"] == "completed", f"Task failed: {status.get('error', status)}"

        # --- Step 7: Verify sessions appear (if any were created) ---
        # Reload the page to pick up the completed state.
        # After a successful audit with sessions, the UI shows "Audit Sessions Created"
        # instead of the config form ("Visit Selection"). Wait for either.
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        # Wait for either the sessions view or the config form to render
        page.get_by_text("Audit Sessions Created").or_(page.get_by_text("Visit Selection")).first.wait_for(
            timeout=30_000
        )

        # Check if sessions were created (depends on opportunity having image data)
        sessions_header = page.get_by_text("Audit Sessions Created")
        if sessions_header.is_visible(timeout=5_000):
            # Sessions exist — verify the full results
            sessions_text = page.get_by_text(re.compile(r"\d+ sessions? linked to this workflow run"))
            expect(sessions_text).to_be_visible(timeout=5_000)

            review_links = page.get_by_role("link", name="Review")
            assert review_links.count() > 0, "Expected at least one audit session with a Review link"

        # --- Step 8: Cleanup ---
        # Delete the workflow run to avoid polluting production labs records
        current_url = page.url
        run_id_match = re.search(r"run_id=(\d+)", current_url)
        if run_id_match:
            run_id = run_id_match.group(1)
            # Get CSRF token from the page
            csrf_token = page.evaluate("document.querySelector('#workflow-root')?.dataset?.csrfToken || ''")
            if csrf_token:
                page.request.post(
                    f"{live_server_url}/labs/workflow/api/run/{run_id}/delete/?opportunity_id={opportunity_id}",
                    headers={"X-CSRFToken": csrf_token},
                )
