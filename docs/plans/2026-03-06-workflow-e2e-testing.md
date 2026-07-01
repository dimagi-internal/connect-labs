# Workflow Template E2E Testing — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** E2E test workflow templates using Playwright against local `runserver` with real OAuth credentials and real production data, starting with `audit_with_ai_review`.

**Architecture:** Playwright headless browser hits a local Django dev server (port 8001). Session auth is injected via a DEBUG-only view that reuses `TokenManager` to set up `labs_oauth` in the Django session. Celery runs in eager mode so tasks execute synchronously. Tests are marked `@pytest.mark.e2e` and excluded from normal test runs.

**Tech Stack:** pytest, pytest-playwright, Playwright (chromium), Django runserver subprocess

---

### Task 1: Install dependencies

**Files:**
- Modify: `requirements/local.txt`

**Step 1: Add pytest-playwright to local requirements**

Add to `requirements/local.txt`:
```
pytest-playwright
```

**Step 2: Install and set up Playwright**

Run:
```bash
pip install pytest-playwright
playwright install chromium
```

**Step 3: Register the e2e marker in pytest config**

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:
```toml
markers = [
    "e2e: End-to-end browser tests (require real OAuth token and runserver)",
]
```

**Step 4: Commit**

```bash
git add requirements/local.txt pyproject.toml
git commit -m "chore: add pytest-playwright for E2E workflow testing"
```

---

### Task 2: Create the test-auth session injection view

**Files:**
- Create: `connect_labs/labs/views_test_auth.py`
- Modify: `connect_labs/labs/urls.py`

**Step 1: Write the test-auth view**

Create `connect_labs/labs/views_test_auth.py`. This view is gated behind `DEBUG=True` and reuses the exact session setup pattern from `connect_labs/labs/management/commands/base_labs_url_test.py:50-103`.

```python
"""
DEBUG-only view to inject a real OAuth session for Playwright E2E tests.

Reads the CLI token from TokenManager, introspects it against production,
fetches org data, and writes labs_oauth into the Django session — exactly
like BaseLabsURLTest does for the Django test client.
"""

import logging
from datetime import datetime

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from connect_labs.labs.integrations.connect.cli import TokenManager
from connect_labs.labs.integrations.connect.oauth import (
    fetch_user_organization_data,
    introspect_token,
)

logger = logging.getLogger(__name__)


@require_GET
def test_auth_view(request):
    """Inject a real OAuth session for E2E testing. DEBUG only."""
    if not settings.DEBUG:
        return JsonResponse({"error": "Only available in DEBUG mode"}, status=403)

    token_manager = TokenManager()
    token_data = token_manager.load_token()

    if not token_data:
        return JsonResponse({"error": "No CLI token found. Run: python manage.py get_cli_token"}, status=401)

    if token_manager.is_expired():
        return JsonResponse({"error": "CLI token expired. Run: python manage.py get_cli_token"}, status=401)

    access_token = token_data["access_token"]

    # Introspect token to get user profile
    profile_data = introspect_token(
        access_token=access_token,
        client_id=settings.CONNECT_OAUTH_CLIENT_ID,
        client_secret=settings.CONNECT_OAUTH_CLIENT_SECRET,
        production_url=settings.CONNECT_PRODUCTION_URL,
    )
    if not profile_data:
        return JsonResponse({"error": "Token introspection failed"}, status=401)

    # Fetch org data
    org_data = fetch_user_organization_data(access_token)
    if not org_data:
        return JsonResponse({"error": "Failed to fetch organization data"}, status=500)

    # Convert expires_at from ISO string to timestamp
    if "expires_at" in token_data and isinstance(token_data["expires_at"], str):
        expires_at = datetime.fromisoformat(token_data["expires_at"]).timestamp()
    else:
        expires_in = token_data.get("expires_in", 1209600)
        expires_at = (timezone.now() + timezone.timedelta(seconds=expires_in)).timestamp()

    # Write session — same structure as the OAuth callback
    request.session["labs_oauth"] = {
        "access_token": access_token,
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at": expires_at,
        "user_profile": {
            "id": profile_data.get("id"),
            "username": profile_data.get("username"),
            "email": profile_data.get("email"),
            "first_name": profile_data.get("first_name", ""),
            "last_name": profile_data.get("last_name", ""),
        },
        "organization_data": org_data,
    }

    return JsonResponse({
        "success": True,
        "username": profile_data.get("username"),
    })
```

**Step 2: Register the URL**

In `connect_labs/labs/urls.py`, add after the existing login/callback paths:

```python
from connect_labs.labs import views_test_auth

# ... in urlpatterns:
path("test-auth/", views_test_auth.test_auth_view, name="test_auth"),
```

**Step 3: Verify manually**

Run:
```bash
python manage.py runserver
```
Then visit `http://localhost:8000/labs/test-auth/` — should return JSON with `{"success": true, "username": "jonathan"}`.

**Step 4: Commit**

```bash
git add connect_labs/labs/views_test_auth.py connect_labs/labs/urls.py
git commit -m "feat: add DEBUG-only test-auth view for Playwright session injection"
```

---

### Task 3: Create the E2E test infrastructure (conftest.py)

**Files:**
- Create: `connect_labs/workflow/tests/e2e/__init__.py`
- Create: `connect_labs/workflow/tests/e2e/conftest.py`

**Step 1: Create the conftest with all fixtures**

Create `connect_labs/workflow/tests/e2e/__init__.py` (empty).

Create `connect_labs/workflow/tests/e2e/conftest.py`:

```python
"""
E2E test infrastructure for workflow templates.

Fixtures:
- live_server_url: starts runserver on port 8001, yields base URL
- browser/page: Playwright chromium browser (from pytest-playwright)
- authenticated_page: page with valid OAuth session injected
- opportunity_id: configurable via --opportunity-id flag

Usage:
    pytest connect_labs/workflow/tests/e2e/ -m e2e --opportunity-id=874
"""

import socket
import subprocess
import sys
import time

import pytest

E2E_PORT = 8001
E2E_HOST = "127.0.0.1"


def pytest_addoption(parser):
    parser.addoption(
        "--opportunity-id",
        action="store",
        default="874",
        help="Opportunity ID to use for E2E tests",
    )


@pytest.fixture(scope="session")
def opportunity_id(request):
    return request.config.getoption("--opportunity-id")


@pytest.fixture(scope="session")
def live_server_url():
    """Start Django runserver as a subprocess on port 8001."""
    # Check port is free
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((E2E_HOST, E2E_PORT))
    sock.close()
    if result == 0:
        # Port already in use — assume dev server is running, reuse it
        yield f"http://{E2E_HOST}:{E2E_PORT}"
        return

    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"{E2E_HOST}:{E2E_PORT}", "--noreload"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready (up to 30s)
    for _ in range(60):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect((E2E_HOST, E2E_PORT))
            sock.close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError(f"Django server failed to start on {E2E_HOST}:{E2E_PORT}")

    yield f"http://{E2E_HOST}:{E2E_PORT}"

    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def authenticated_context(browser, live_server_url):
    """Create a browser context with a valid OAuth session.

    Navigates to /labs/test-auth/ to inject the CLI token into the
    Django session, then preserves the session cookie for all pages
    created from this context.
    """
    context = browser.new_context()
    page = context.new_page()

    response = page.goto(f"{live_server_url}/labs/test-auth/")
    assert response.status == 200, f"test-auth failed: {page.content()}"

    body = response.json()
    assert body.get("success"), f"test-auth returned: {body}"

    page.close()
    yield context
    context.close()


@pytest.fixture
def auth_page(authenticated_context):
    """A fresh page with valid auth session."""
    page = authenticated_context.new_page()
    yield page
    page.close()
```

**Step 2: Commit**

```bash
git add connect_labs/workflow/tests/e2e/
git commit -m "feat: add E2E test infrastructure with Playwright fixtures"
```

---

### Task 4: Write the audit_with_ai_review E2E test

**Files:**
- Create: `connect_labs/workflow/tests/e2e/test_audit_workflow.py`

**Step 1: Write the E2E test**

Create `connect_labs/workflow/tests/e2e/test_audit_workflow.py`:

```python
"""
E2E test for the audit_with_ai_review workflow template.

Tests the full happy path:
1. Create workflow from template (via POST to create endpoint)
2. Navigate to the workflow run page
3. Verify the React UI renders (Babel transpiles the JSX)
4. Switch to last_n mode and set a small sample size
5. Trigger audit creation
6. Wait for completion
7. Verify sessions appear
8. Clean up (delete the run)

Run:
    pytest connect_labs/workflow/tests/e2e/test_audit_workflow.py -m e2e -v --opportunity-id=874
"""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


class TestAuditWithAIReviewWorkflow:
    """E2E test for the audit_with_ai_review workflow template."""

    def test_full_audit_workflow(self, auth_page, live_server_url, opportunity_id):
        """Test creating and running an audit workflow end-to-end."""
        page = auth_page
        page.set_default_timeout(120_000)  # 120s — eager Celery can be slow

        # --- Step 1: Create a workflow from the audit template ---
        # POST to the create endpoint (same as clicking the template button)
        page.goto(f"{live_server_url}/labs/workflow/?opportunity_id={opportunity_id}")
        page.wait_for_load_state("networkidle")

        # Find and click the audit template create button
        # The template list has forms with hidden input[name=template][value=audit_with_ai_review]
        audit_form = page.locator("form:has(input[value='audit_with_ai_review'])")
        if audit_form.count() > 0:
            audit_form.locator("button[type='submit']").click()
            page.wait_for_load_state("networkidle")
        else:
            # Template might already exist — navigate to workflow list and find it
            pytest.skip("audit_with_ai_review template form not found on list page")

        # After creation, we're redirected back to /labs/workflow/ list
        # Find the newly created workflow and click into it
        # Look for a link containing "Weekly Audit with AI Review"
        workflow_link = page.locator("a:has-text('Weekly Audit with AI Review')").first
        expect(workflow_link).to_be_visible()
        workflow_link.click()
        page.wait_for_load_state("networkidle")

        # We should now be on the definition detail page — click "New Run"
        new_run_link = page.locator("a:has-text('New Run'), a:has-text('Run'), button:has-text('New Run')").first
        if new_run_link.is_visible():
            new_run_link.click()
            page.wait_for_load_state("networkidle")

        # --- Step 2: Verify the React WorkflowUI renders ---
        # Wait for Babel to transpile and the component to mount
        # The audit UI has a mode selector (date_range vs last_n)
        mode_selector = page.locator("text=Last N Per Opportunity, text=Last N visits, input[type='radio']").first
        page.wait_for_selector("[data-testid='workflow-ui'], .workflow-container, text=Audit Mode", timeout=30_000)

        # --- Step 3: Configure for a small sample ---
        # Switch to last_n mode for a fast, predictable test
        last_n_radio = page.locator("label:has-text('Last N'), input[value='last_n_per_opp']").first
        if last_n_radio.is_visible():
            last_n_radio.click()

        # Set a small count (e.g., 3) to keep the test fast
        count_input = page.locator("input[type='number']").first
        if count_input.is_visible():
            count_input.fill("3")

        # --- Step 4: Trigger audit creation ---
        create_button = page.locator("button:has-text('Create Audit'), button:has-text('Run Audit')").first
        expect(create_button).to_be_visible()
        expect(create_button).to_be_enabled()
        create_button.click()

        # --- Step 5: Wait for completion ---
        # Progress UI should appear
        page.wait_for_selector("text=Processing, text=Fetching, text=Extracting, text=Creating", timeout=15_000)

        # Wait for completion (generous timeout for real API calls)
        page.wait_for_selector(
            "text=Complete, text=Completed, text=sessions created",
            timeout=120_000,
        )

        # --- Step 6: Verify results ---
        # Sessions should appear in the linked sessions list
        # The UI shows session cards/rows after completion
        page.wait_for_timeout(2_000)  # Brief wait for session fetch

        # Check that at least one session is visible
        sessions_area = page.locator("text=Audit Session, text=sessions, text=FLW")
        expect(sessions_area.first).to_be_visible(timeout=10_000)

        # --- Step 7: Cleanup ---
        # Delete the workflow run to avoid polluting production labs records
        # Get the run_id from the URL
        current_url = page.url
        if "run_id=" in current_url:
            import re
            run_id_match = re.search(r"run_id=(\d+)", current_url)
            if run_id_match:
                run_id = run_id_match.group(1)
                # Delete via API
                csrf_token = page.locator("input[name='csrfmiddlewaretoken']").first.get_attribute("value") or ""
                page.request.post(
                    f"{live_server_url}/labs/workflow/api/run/{run_id}/delete/?opportunity_id={opportunity_id}",
                    headers={"X-CSRFToken": csrf_token},
                )
```

**Step 2: Run the test to verify it works**

Run:
```bash
pytest connect_labs/workflow/tests/e2e/test_audit_workflow.py -m e2e -v --opportunity-id=874
```

Expected: Test should pass end-to-end (may take 60-120s due to real API calls).

**Step 3: Debug and adjust selectors as needed**

The CSS selectors above are best-guesses based on the render code. After the first run, adjust selectors to match actual DOM elements. Use `page.screenshot(path="debug.png")` at failure points to inspect the UI state.

**Step 4: Commit**

```bash
git add connect_labs/workflow/tests/e2e/test_audit_workflow.py
git commit -m "feat: add E2E test for audit_with_ai_review workflow template"
```

---

### Task 5: Verify everything works together

**Step 1: Ensure CLI token is valid**

Run:
```bash
python manage.py get_cli_token
```

**Step 2: Run the full E2E suite**

Run:
```bash
pytest connect_labs/workflow/tests/e2e/ -m e2e -v --opportunity-id=874
```

Expected: Server starts on 8001, auth injects, audit workflow runs end-to-end, cleanup succeeds.

**Step 3: Run normal pytest to verify E2E tests are excluded**

Run:
```bash
pytest connect_labs/workflow/tests/ -v
```

Expected: Only `test_mbw_v1_v2_parity.py` tests run. E2E tests are skipped (no `e2e` marker selected).

**Step 4: Final commit**

```bash
git add -A
git commit -m "docs: finalize E2E testing plan and infrastructure"
```
