"""Portable E2E harness for the Campaign Utility Tool.

Deliberately NOT coupled to labs. The labs e2e suites authenticate via
`/labs/test-auth/` (Connect OAuth + CLI token); the campaign app uses CommCare
OAuth and is decoupled from labs. Instead we use pytest-django's `live_server`
(a real HTTP server backed by the freshly-migrated TEST database, so the schema
always matches the current code — no dependency on the dev DB's state) plus
**in-process session injection**: we mint a logged-in campaign session row with
the ORM and hand its `sessionid` cookie to Playwright. No `/campaign/`
auth-bypass endpoint is added.

Prerequisites:
    Postgres reachable for config.settings.test
    playwright install chromium
Run (excluded from the default suite — the dir is in pyproject `--ignore`):
    GDAL_LIBRARY_PATH=/opt/homebrew/lib/libgdal.dylib \
    GEOS_LIBRARY_PATH=/opt/homebrew/lib/libgeos_c.dylib \
    pytest commcare_connect/campaign/tests/e2e/ -o "addopts=" -p no:randomly -v
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

E2E_USERNAME = "e2e-campaign-admin@dimagi.com"


@pytest.fixture
def live_server_url(live_server):
    """Back-compat alias so test modules can ask for a plain URL string."""
    return live_server.url


def _make_session(role: str) -> str:
    """Create a Django user + ACTIVE CampaignUser of `role` and a logged-in
    `campaign_oauth` session; return the session key for cookie injection."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.utils import timezone

    from commcare_connect.campaign.models import CampaignUser
    from commcare_connect.users.models import User

    username = f"{role}.{E2E_USERNAME}"
    user, _ = User.objects.get_or_create(username=username, defaults={"email": username, "name": "E2E"})
    CampaignUser.objects.update_or_create(
        commcare_username=username,
        defaults={
            "user": user,
            "email": username,
            "name": "E2E",
            "role": role,
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
        "identity": {"username": username, "email": username, "name": "E2E"},
    }
    store.create()
    return store.session_key


@pytest.fixture
def seeded(transactional_db):
    """The full demo dataset committed to the test DB so the live server can read it."""
    from commcare_connect.campaign.services import seed

    return seed.seed_campaign(fresh=True)


@pytest.fixture
def session_for(seeded):
    """Return a factory: `session_for(role)` -> session_key (committed to the test DB)."""
    return _make_session


@pytest.fixture
def approvable_worker(seeded):
    """A clean, KYC-approved, payment-pending worker — so a UI approval is
    unambiguously allowed (no fraud flag, KYC done) AND produces a visible change."""
    base = seeded.workers.filter(fraud_rules=[]).exclude(kyc="rejected").exclude(pay__in=["approved", "paid"])
    w = base.filter(kyc="approved", pay="pending").first() or base.first()
    assert w is not None, "seed should contain a clean, not-yet-approved worker"
    return w


@pytest.fixture
def auth_page(browser, live_server, session_for):
    """A Playwright page authenticated as campaign_admin."""
    key = session_for("campaign_admin")
    context = browser.new_context()
    context.add_cookies([{"name": "sessionid", "value": key, "url": live_server.url}])
    page = context.new_page()
    page.set_default_timeout(30_000)
    yield page
    context.close()


@pytest.fixture
def page_as(browser, live_server, session_for):
    """Factory fixture: `page_as(role)` -> an authenticated Playwright page for that role."""
    contexts = []

    def _open(role: str):
        key = session_for(role)
        ctx = browser.new_context()
        ctx.add_cookies([{"name": "sessionid", "value": key, "url": live_server.url}])
        pg = ctx.new_page()
        pg.set_default_timeout(30_000)
        contexts.append(ctx)
        return pg

    yield _open
    for ctx in contexts:
        ctx.close()


@pytest.fixture
def anon_page(browser, live_server):
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    yield page
    context.close()
