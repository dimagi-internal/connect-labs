"""
E2E test infrastructure for the solicitation lifecycle.

Prerequisites:
    1. Valid CLI token: python manage.py get_cli_token --profile test-user
    2. Docker services running: inv up
    3. Dev server running: python manage.py runserver
    4. Playwright installed: playwright install chromium

Usage:
    pytest commcare_connect/solicitations/tests/e2e/ \
        --ds=config.settings.local -o "addopts=" -v
"""
import os
import socket

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

E2E_PORT = 8000
E2E_HOST = "localhost"


@pytest.fixture(scope="session")
def django_db_setup():
    pass


def pytest_addoption(parser):
    # Guard against duplicate registration if funder_dashboard conftest is also loaded
    try:
        parser.addoption("--org-id", action="store", default=None)
    except ValueError:
        pass
    try:
        parser.addoption("--profile", action="store", default="test-user")
    except ValueError:
        pass


@pytest.fixture(scope="session")
def live_server_url():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((E2E_HOST, E2E_PORT))
    sock.close()
    if result == 0:
        yield f"http://{E2E_HOST}:{E2E_PORT}"
        return
    pytest.skip("Dev server not running on port 8000")


@pytest.fixture(scope="session")
def _auth_data(request, browser, live_server_url):
    profile = request.config.getoption("--profile")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    auth_url = f"{live_server_url}/labs/test-auth/"
    if profile:
        auth_url += f"?profile={profile}"
    response = page.goto(auth_url)
    assert response.status == 200
    body = response.json()
    assert body.get("success"), f"Auth failed: {body}"
    page.close()
    yield context, body
    context.close()


@pytest.fixture(scope="session")
def authenticated_context(_auth_data):
    return _auth_data[0]


@pytest.fixture
def auth_page(authenticated_context):
    page = authenticated_context.new_page()
    yield page
    page.close()


@pytest.fixture(scope="session")
def org_id(request, _auth_data):
    explicit = request.config.getoption("--org-id")
    if explicit:
        return explicit
    _, body = _auth_data
    orgs = body.get("organizations", [])
    if orgs:
        return str(orgs[0].get("slug", orgs[0].get("id", "")))
    pytest.skip("No organization found")


SCREENSHOTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "screenshots", "e2e", "solicitation_lifecycle"
)


@pytest.fixture(autouse=True)
def screenshot_on_failure(request, auth_page):
    yield
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        name = request.node.name.replace("[", "_").replace("]", "")
        auth_page.screenshot(path=os.path.join(SCREENSHOTS_DIR, f"FAIL_{name}.png"), full_page=True)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
