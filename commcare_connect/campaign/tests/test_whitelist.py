import pytest
from django.test import override_settings

from commcare_connect.campaign.auth import whitelist
from commcare_connect.campaign.models import CampaignUser
from commcare_connect.users.models import User


@override_settings(CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"])
def test_is_bootstrap_admin():
    assert whitelist.is_bootstrap_admin("x@dimagi.com") is True
    assert whitelist.is_bootstrap_admin("X@Dimagi.com") is True
    assert whitelist.is_bootstrap_admin("x@other.org") is False
    assert whitelist.is_bootstrap_admin("") is False


@pytest.mark.django_db
@override_settings(CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"])
def test_dimagi_user_autoprovisioned_as_admin():
    u = User.objects.create(username="staff@dimagi.com", email="staff@dimagi.com", name="Staff")
    identity = {"username": "staff@dimagi.com", "email": "staff@dimagi.com", "name": "Staff", "domains": []}
    cu = whitelist.resolve_campaign_user(identity, u)
    assert cu is not None
    assert cu.role == "campaign_admin"
    assert cu.user_id == u.id
    assert cu.last_login_at is not None


@pytest.mark.django_db
@override_settings(CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"])
def test_non_dimagi_requires_whitelist_row():
    u = User.objects.create(username="ext@other.org", email="ext@other.org", name="Ext")
    identity = {"username": "ext@other.org", "email": "ext@other.org", "name": "Ext", "domains": []}
    assert whitelist.resolve_campaign_user(identity, u) is None


@pytest.mark.django_db
@override_settings(CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"])
def test_whitelisted_non_dimagi_logs_in_with_assigned_role():
    u = User.objects.create(username="ops@other.org", email="ops@other.org", name="Ops")
    CampaignUser.objects.create(
        commcare_username="ops@other.org", email="ops@other.org", name="Ops", role="operations_manager"
    )
    identity = {"username": "ops@other.org", "email": "ops@other.org", "name": "Ops", "domains": []}
    cu = whitelist.resolve_campaign_user(identity, u)
    assert cu is not None
    assert cu.role == "operations_manager"
    assert cu.user_id == u.id


@pytest.mark.django_db
@override_settings(CAMPAIGN_BOOTSTRAP_ADMIN_DOMAINS=["dimagi.com"])
def test_deactivated_whitelist_row_is_denied():
    User.objects.create(username="gone@other.org", email="gone@other.org", name="Gone")
    CampaignUser.objects.create(
        commcare_username="gone@other.org",
        email="gone@other.org",
        name="Gone",
        role="reporting_user",
        status="deactivated",
    )
    u = User.objects.get(username="gone@other.org")
    identity = {"username": "gone@other.org", "email": "gone@other.org", "name": "Gone", "domains": []}
    assert whitelist.resolve_campaign_user(identity, u) is None
