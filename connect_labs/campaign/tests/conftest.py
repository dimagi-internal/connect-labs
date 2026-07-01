"""App-scoped pytest fixtures for the Campaign Utility Tool.

Self-contained (no labs fixtures) so the suite travels with the app. The headline
fixture is `csrf_client`: a CSRF-enforcing test client that is the DEFAULT for any
mutation test. Django's plain test client disables CSRF, which is exactly why the
`CSRF_USE_SESSIONS`/no-cookie write failure (PR #662) was invisible to unit tests
until a real browser hit it. New write tests must use `csrf_client` + the rendered
`<meta name="csrf-token">` token so that class of bug can never regress silently.
"""
from __future__ import annotations

import pytest
from django.test import Client

from connect_labs.campaign.tests.factories import CampaignUserFactory, UserFactory


@pytest.fixture
def csrf_client() -> Client:
    """A test client that enforces CSRF — the default for mutation tests."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def login_as(db):
    """Return `login(client, role="campaign_admin", username=...)`.

    Provisions a Django user + an ACTIVE CampaignUser of the given role and primes
    the `campaign_oauth` session so server views see a real authenticated campaign
    user — without round-tripping OAuth. Returns the CampaignUser.
    """

    def _login(client, role="campaign_admin", username="member@dimagi.com"):
        user = UserFactory(username=username, email=username, name="Member")
        cu = CampaignUserFactory(
            user=user,
            commcare_username=username,
            email=username,
            name="Member",
            role=role,
        )
        client.force_login(user)
        session = client.session
        session["campaign_oauth"] = {
            "access_token": "AT",
            "expires_at": 9_999_999_999.0,
            "identity": {"username": username, "email": username, "name": "Member"},
        }
        session.save()
        return cu

    return _login


@pytest.fixture
def seeded_campaign(db):
    """The full, prototype-shaped demo dataset (64 workers, 7 fraud pairs, etc.)."""
    from connect_labs.campaign.services import seed

    return seed.seed_campaign(fresh=True)
