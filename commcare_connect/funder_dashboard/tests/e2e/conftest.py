"""
E2E test infrastructure for the funder dashboard and award flow.

Reuses the same pattern as workflow/tests/e2e/:
- Starts Django dev server on port 8001
- Authenticates via /labs/test-auth/ (uses CLI token)
- Provides auth_page fixture with valid session

Prerequisites:
    1. Valid CLI token: python manage.py get_cli_token
    2. Docker services running: inv up
    3. Playwright installed: playwright install chromium

Usage:
    pytest commcare_connect/funder_dashboard/tests/e2e/ \
        --ds=config.settings.local -o "addopts=" -v

    # With specific org (default auto-detects from token):
    pytest commcare_connect/funder_dashboard/tests/e2e/ \
        --ds=config.settings.local -o "addopts=" -v --org-id=42
"""

import os
import socket
import subprocess
import sys
import time

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(scope="session")
def django_db_setup():
    """No-op: E2E tests hit a running server, no test database needed."""
    pass


E2E_PORT = 8001
E2E_HOST = "127.0.0.1"


def pytest_addoption(parser):
    parser.addoption(
        "--org-id",
        action="store",
        default=None,
        help="Organization ID to use for E2E tests (auto-detected if not set)",
    )
    parser.addoption(
        "--program-id",
        action="store",
        default=None,
        help="Program ID to use for E2E tests (auto-detected if not set)",
    )
    parser.addoption(
        "--profile",
        action="store",
        default="test-user",
        help="TokenManager profile name for auth (default: test-user)",
    )


@pytest.fixture(scope="session")
def live_server_url():
    """Start Django runserver as a subprocess on port 8001."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((E2E_HOST, E2E_PORT))
    sock.close()
    if result == 0:
        yield "http://{}:{}".format(E2E_HOST, E2E_PORT)
        return

    server_log = open("e2e_django_server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "{}:{}".format(E2E_HOST, E2E_PORT), "--noreload"],
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

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
        raise RuntimeError("Django server failed to start on {}:{}".format(E2E_HOST, E2E_PORT))

    yield "http://{}:{}".format(E2E_HOST, E2E_PORT)

    proc.terminate()
    proc.wait(timeout=10)
    server_log.close()


@pytest.fixture(scope="session")
def authenticated_context(request, browser, live_server_url):
    """Create a browser context with a valid OAuth session using the specified profile."""
    profile = request.config.getoption("--profile")
    context = browser.new_context()
    page = context.new_page()

    auth_url = f"{live_server_url}/labs/test-auth/"
    if profile:
        auth_url += f"?profile={profile}"

    response = page.goto(auth_url)
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


@pytest.fixture(scope="session")
def org_id(request, authenticated_context, live_server_url):
    """Get or auto-detect the organization ID."""
    explicit = request.config.getoption("--org-id")
    if explicit:
        return explicit

    # Auto-detect: hit the labs context API to find the first org
    page = authenticated_context.new_page()
    page.goto(f"{live_server_url}/labs/api/context/")
    page.wait_for_load_state("networkidle")
    try:
        data = page.evaluate("() => JSON.parse(document.body.innerText)")
        orgs = data.get("organizations", [])
        if orgs:
            return str(orgs[0].get("id", ""))
    except Exception:
        pass
    finally:
        page.close()

    pytest.skip("No organization found — pass --org-id or ensure account has orgs")


@pytest.fixture(scope="session")
def program_id(request, authenticated_context, live_server_url):
    """Get or auto-detect the program ID."""
    explicit = request.config.getoption("--program-id")
    if explicit:
        return explicit

    # Auto-detect from context API
    page = authenticated_context.new_page()
    page.goto(f"{live_server_url}/labs/api/context/")
    page.wait_for_load_state("networkidle")
    try:
        data = page.evaluate("() => JSON.parse(document.body.innerText)")
        programs = data.get("programs", [])
        if programs:
            return str(programs[0].get("id", ""))
    except Exception:
        pass
    finally:
        page.close()

    pytest.skip("No program found — pass --program-id or ensure account has programs")


SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "screenshots", "e2e")


@pytest.fixture(autouse=True)
def screenshot_on_failure(request, auth_page):
    """Capture a screenshot on test failure for debugging."""
    yield
    if request.node.rep_call and request.node.rep_call.failed:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        name = request.node.name.replace("[", "_").replace("]", "")
        auth_page.screenshot(path=os.path.join(SCREENSHOTS_DIR, f"FAIL_{name}.png"), full_page=True)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
