"""User Management API tests — invite / set-role / set-status.

Uses the shared conftest fixtures (login_as, seeded_campaign) introduced in
PR #668. Each test adapts the brief's assertions to the fixture conventions:
- `login_as(client, role, username)` provisions Django User + ACTIVE CampaignUser
  and primes `campaign_oauth`; default username is "member@dimagi.com".
- `seeded_campaign` is the full prototype-shaped demo dataset.
- Target users are created via CampaignUserFactory (not the seeded dataset) so
  tests can control exactly which user objects exist.
"""
from __future__ import annotations

import json

import pytest
from django.urls import reverse

from connect_labs.campaign.models import CampaignUser
from connect_labs.campaign.tests.factories import CampaignUserFactory


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


@pytest.mark.django_db
def test_invite_creates_whitelisted_user(client, login_as, seeded_campaign):
    login_as(client)  # default campaign_admin, username="member@dimagi.com"
    resp = _post(
        client,
        reverse("campaign:user_invite"),
        {"name": "Chidi Nwosu", "email": "chidi@partner.org", "role": "reporting", "scope": "Borno"},
    )
    assert resp.status_code == 200
    u = resp.json()["user"]
    assert u["id"] == "chidi@partner.org"
    assert u["role"] == "reporting"
    assert u["status"] == "active"
    assert CampaignUser.objects.get(commcare_username="chidi@partner.org").role == "reporting_user"


@pytest.mark.django_db
def test_invite_existing_user_re_roles_and_reactivates(client, login_as, seeded_campaign):
    login_as(client)  # default campaign_admin
    CampaignUserFactory(
        commcare_username="dana@partner.org",
        email="dana@partner.org",
        name="Dana",
        role="reporting_user",
        status="deactivated",
    )
    resp = _post(
        client,
        reverse("campaign:user_invite"),
        {"name": "Dana O.", "email": "dana@partner.org", "role": "operations", "scope": "Yobe"},
    )
    assert resp.status_code == 200
    u = resp.json()["user"]
    assert u["role"] == "operations"
    assert u["status"] == "active"  # re-inviting reactivates
    row = CampaignUser.objects.get(commcare_username="dana@partner.org")
    assert row.role == "operations_manager"
    assert row.scope == "Yobe"
    assert row.name == "Dana O."
    # still exactly one row — invite is an upsert, not a duplicate
    assert CampaignUser.objects.filter(commcare_username="dana@partner.org").count() == 1


@pytest.mark.django_db
def test_set_role_maps_short_to_key(client, login_as, seeded_campaign):
    login_as(client)  # default campaign_admin, username="member@dimagi.com"
    CampaignUserFactory(commcare_username="t@x.org", email="t@x.org", name="T", role="reporting_user")
    resp = _post(client, reverse("campaign:user_set_role", args=["t@x.org"]), {"role": "operations"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "operations"
    assert CampaignUser.objects.get(commcare_username="t@x.org").role == "operations_manager"


@pytest.mark.django_db
def test_set_status_and_no_self_modify(client, login_as, seeded_campaign):
    login_as(client)  # default campaign_admin, username="member@dimagi.com"
    CampaignUserFactory(commcare_username="t@x.org", email="t@x.org", name="T", role="reporting_user")
    # set status on a different user — should succeed
    resp = _post(client, reverse("campaign:user_set_status", args=["t@x.org"]), {"status": "deactivated"})
    assert resp.status_code == 200
    assert CampaignUser.objects.get(commcare_username="t@x.org").status == "deactivated"
    # cannot change your own status
    assert (
        _post(
            client,
            reverse("campaign:user_set_status", args=["member@dimagi.com"]),
            {"status": "deactivated"},
        ).status_code
        == 400
    )
    # cannot change your own role
    assert (
        _post(
            client,
            reverse("campaign:user_set_role", args=["member@dimagi.com"]),
            {"role": "reporting"},
        ).status_code
        == 400
    )


@pytest.mark.django_db
def test_users_rbac_non_admin_403(client, login_as, seeded_campaign):
    login_as(client, role="reporting_user", username="r@x.org")
    assert (
        _post(
            client,
            reverse("campaign:user_invite"),
            {"name": "X", "email": "x@x.org", "role": "reporting"},
        ).status_code
        == 403
    )
