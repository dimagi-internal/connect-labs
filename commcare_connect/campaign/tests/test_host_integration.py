"""Portable reproduction of the host-middleware interaction class (PR #661).

In connect-labs, `LabsOAuthSessionMiddleware` ran on every authenticated request
and logged out anyone lacking `session["labs_oauth"]`. Campaign users authenticate
via CommCare OAuth and never have `labs_oauth`, so `/campaign/` users were logged
out into an infinite redirect. The fix lived in LABS (it added `/campaign/` to its
skip list) — the campaign app cannot unilaterally stop a hostile upstream.

These tests are app-owned and host-independent: they stand up a generic "hostile
upstream" OAuth-session middleware (mimicking the labs failure mode without importing
labs) and lock in the contract that any host must satisfy — *any upstream
OAuth-session middleware MUST skip `/campaign/`* — plus verify the campaign
middleware is itself a good citizen. The lesson survives even after migration when
labs is gone.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.test import override_settings

CAMPAIGN_MW = "commcare_connect.campaign.middleware.CampaignOAuthSessionMiddleware"
HOSTILE_MW = "commcare_connect.campaign.tests.test_host_integration.HostileUpstreamOAuthMiddleware"


class HostileUpstreamOAuthMiddleware:
    """Stand-in for ANY upstream OAuth-session middleware that logs out an
    authenticated request lacking ITS own session key. Mirrors the labs failure mode.

    Set ``skip_campaign`` on the class to model the production fix (skip `/campaign/`).
    """

    skip_campaign = False

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        skipping = self.skip_campaign and path.startswith("/campaign/")
        if request.user.is_authenticated and "labs_oauth" not in request.session and not skipping:
            logout(request)
            return redirect("/accounts/login/")
        return self.get_response(request)


def _middleware_with_hostile_upstream():
    """Production-shaped stack: hostile upstream inserted right after auth, campaign MW after it."""
    mw = list(settings.MIDDLEWARE)
    auth_idx = mw.index("django.contrib.auth.middleware.AuthenticationMiddleware")
    # Hostile upstream sits ahead of the campaign middleware, like labs did.
    mw.insert(auth_idx + 1, HOSTILE_MW)
    if CAMPAIGN_MW not in mw:
        mw.insert(auth_idx + 2, CAMPAIGN_MW)
    return mw


@pytest.fixture(autouse=True)
def _reset_hostile_flag():
    HostileUpstreamOAuthMiddleware.skip_campaign = False
    yield
    HostileUpstreamOAuthMiddleware.skip_campaign = False


@pytest.mark.django_db
def test_hostile_upstream_that_does_not_skip_campaign_breaks_login(client, login_as):
    """Documents the FAILURE MODE: an upstream that doesn't skip `/campaign/` logs the
    campaign user out, so the gated endpoint bounces to login (the PR #661 bug)."""
    HostileUpstreamOAuthMiddleware.skip_campaign = False
    login_as(client, "campaign_admin")
    with override_settings(MIDDLEWARE=_middleware_with_hostile_upstream()):
        resp = client.get("/campaign/api/bootstrap/")
    assert resp.status_code in (302, 301)
    assert "/login" in resp.headers.get("Location", "")


@pytest.mark.django_db
def test_hostile_upstream_that_skips_campaign_is_safe(client, login_as):
    """Documents the REQUIRED HOST CONTRACT (the PR #661 fix): an upstream that skips
    `/campaign/` leaves campaign users authenticated and the endpoint returns 200."""
    HostileUpstreamOAuthMiddleware.skip_campaign = True
    login_as(client, "campaign_admin")
    with override_settings(MIDDLEWARE=_middleware_with_hostile_upstream()):
        resp = client.get("/campaign/api/bootstrap/")
    assert resp.status_code == 200
    assert "campaign" in resp.json()


@pytest.mark.django_db
def test_campaign_middleware_is_a_good_citizen_on_foreign_paths(client, login_as):
    """The campaign middleware must NOT touch sessions on non-`/campaign/` paths, so it
    can never log out another app's users — the inverse of the bug it suffered."""
    login_as(client, "campaign_admin")
    # A bare authenticated GET to a non-campaign path must not be clobbered by the
    # campaign middleware (it only acts under /campaign/). The campaign session stays.
    client.get("/")
    assert "_auth_user_id" in client.session
    assert client.session.get("campaign_oauth") is not None
