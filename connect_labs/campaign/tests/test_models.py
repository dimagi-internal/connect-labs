import pytest

from connect_labs.campaign.models import CampaignUser
from connect_labs.users.models import User


@pytest.mark.django_db
def test_campaign_user_roundtrip():
    u = User.objects.create(username="amara", email="amara@example.org", name="Amara Okafor")
    cu = CampaignUser.objects.create(
        user=u,
        commcare_username="amara",
        email="amara@example.org",
        name="Amara Okafor",
        role="payment_admin",
    )
    assert cu.status == "active"
    assert cu.scope == "All regions"
    assert cu.is_active_member is True


@pytest.mark.django_db
def test_commcare_username_is_unique():
    CampaignUser.objects.create(commcare_username="dup", email="a@x.org", name="A", role="reporting_user")
    with pytest.raises(Exception):
        CampaignUser.objects.create(commcare_username="dup", email="b@x.org", name="B", role="reporting_user")


@pytest.mark.django_db
def test_deactivated_is_not_active_member():
    cu = CampaignUser.objects.create(
        commcare_username="x", email="x@x.org", name="X", role="reporting_user", status="deactivated"
    )
    assert cu.is_active_member is False
