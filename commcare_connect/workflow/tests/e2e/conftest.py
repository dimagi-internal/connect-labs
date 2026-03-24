"""
E2E test infrastructure for workflow templates.

Fixtures:
- live_server_url: starts runserver on port 8001, yields base URL
- browser/page: Playwright chromium browser (from pytest-playwright)
- authenticated_context: browser context with valid OAuth session
- auth_page: fresh page with auth session
- opportunity_id: configurable via --opportunity-id flag

Usage:
    pytest commcare_connect/workflow/tests/e2e/ --ds=config.settings.local -o "addopts=" --opportunity-id=874

Note: --ds=config.settings.local and -o "addopts=" are needed to override
the project-level pytest config which uses production Django settings.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

# Allow Django ORM calls inside Playwright's async event loop.
# E2E tests hit a running server so this only affects pytest-django's
# own database setup machinery, not application code.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Disable eager mode so Celery tasks route to a real worker instead of
# running synchronously in the request thread (which blocks the server).
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "0"


@pytest.fixture(scope="session")
def django_db_setup():
    """No-op: E2E tests hit a running server, no test database needed."""
    pass


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

    server_log = open("e2e_django_server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"{E2E_HOST}:{E2E_PORT}", "--noreload"],
        stdout=server_log,
        stderr=subprocess.STDOUT,
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
        server_log.close()
        raise RuntimeError(f"Django server failed to start on {E2E_HOST}:{E2E_PORT}")

    try:
        yield f"http://{E2E_HOST}:{E2E_PORT}"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        server_log.close()


@pytest.fixture(scope="session")
def celery_worker():
    """Start a Celery worker subprocess for async task execution.

    Uses --pool=solo (safe on Windows, single-threaded) and
    subprocess.DEVNULL to avoid pipe buffer deadlocks.
    """
    # Purge any stale tasks left in Redis from previous runs
    subprocess.run(
        [sys.executable, "-m", "celery", "-A", "config.celery_app", "purge", "-f"],
        capture_output=True,
        timeout=10,
    )

    log_file = open("e2e_celery_worker.log", "w")
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "config.celery_app",
            "worker",
            "--loglevel=info",
            "--pool=solo",
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    # Give the worker a moment to connect to the broker
    time.sleep(5)
    if worker.poll() is not None:
        log_file.close()
        with open("e2e_celery_worker.log") as f:
            logs = f.read()
        raise RuntimeError(f"Celery worker exited immediately with code {worker.returncode}\n{logs}")

    try:
        yield worker
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
        log_file.close()


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
