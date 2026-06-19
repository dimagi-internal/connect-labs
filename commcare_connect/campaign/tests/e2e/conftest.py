"""Portable E2E harness for the Campaign Utility Tool.

Deliberately NOT coupled to labs. The labs e2e suites authenticate via
`/labs/test-auth/` (Connect OAuth + CLI token); the campaign app uses CommCare
OAuth and is decoupled from labs, so instead we use **cross-process session
injection**:

  - the pytest process and the `runserver` subprocess both run under
    `config.settings.local`, so they share the same dev Postgres;
  - we mint a logged-in campaign session row (Django auth + `campaign_oauth`)
    directly in that shared DB and inject its `sessionid` cookie into Playwright.

No `/campaign/` auth-bypass endpoint is added (that would be a security surface);
the only requirement is DB access, which the shared dev Postgres already provides.

Prerequisites:
    inv up                       # Postgres/Redis
    playwright install chromium
Run (excluded from the default suite — the dir is in pyproject `--ignore`):
    pytest commcare_connect/campaign/tests/e2e/ --ds=config.settings.local -o "addopts=" -v
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

E2E_HOST = "localhost"
E2E_PORT = 8000

E2E_USERNAME = "e2e-campaign-admin@dimagi.com"


@pytest.fixture(scope="session")
def django_db_setup():
    """No-op: E2E hits a running server against the real dev DB, no test DB."""


@pytest.fixture(scope="session")
def live_server_url():
    """Reuse a running server on the port if present, else start runserver."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    already_up = sock.connect_ex((E2E_HOST, E2E_PORT)) == 0
    sock.close()
    if already_up:
        yield f"http://{E2E_HOST}:{E2E_PORT}"
        return

    log = open("e2e_campaign_server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"{E2E_HOST}:{E2E_PORT}", "--noreload"],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect((E2E_HOST, E2E_PORT))
            s.close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError(f"Django server failed to start on {E2E_HOST}:{E2E_PORT}")
    yield f"http://{E2E_HOST}:{E2E_PORT}"
    proc.terminate()
    proc.wait(timeout=10)
    log.close()


@pytest.fixture(scope="session")
def _campaign_session():
    """Mint a logged-in campaign-admin session in the shared dev DB; clean up after.

    Yields the Django session_key to inject as the `sessionid` cookie.
    """
    from django.contrib.sessions.backends.db import SessionStore
    from django.utils import timezone

    from commcare_connect.campaign.models import CampaignUser
    from commcare_connect.campaign.services import seed
    from commcare_connect.users.models import User

    seed.seed_campaign()  # ensure the demo dataset exists to render

    user, _ = User.objects.get_or_create(username=E2E_USERNAME, defaults={"email": E2E_USERNAME, "name": "E2E Admin"})
    CampaignUser.objects.update_or_create(
        commcare_username=E2E_USERNAME,
        defaults={
            "user": user,
            "email": E2E_USERNAME,
            "name": "E2E Admin",
            "role": "campaign_admin",
            "status": CampaignUser.Status.ACTIVE,
        },
    )

    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store["_auth_user_hash"] = user.get_session_auth_hash()
    store["campaign_oauth"] = {
        "access_token": "E2E",
        "expires_at": timezone.now().timestamp() + 86_400,
        "identity": {"username": E2E_USERNAME, "email": E2E_USERNAME, "name": "E2E Admin"},
    }
    store.create()
    session_key = store.session_key

    yield session_key

    store.delete(session_key)
    CampaignUser.objects.filter(commcare_username=E2E_USERNAME).delete()
    User.objects.filter(username=E2E_USERNAME).delete()


@pytest.fixture
def auth_page(browser, live_server_url, _campaign_session):
    """A Playwright page carrying a valid campaign-admin session cookie."""
    context = browser.new_context()
    context.add_cookies([{"name": "sessionid", "value": _campaign_session, "url": live_server_url}])
    page = context.new_page()
    page.set_default_timeout(30_000)
    yield page
    context.close()


@pytest.fixture
def anon_page(browser, live_server_url):
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    yield page
    context.close()
