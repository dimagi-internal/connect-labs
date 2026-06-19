import pytest

from commcare_connect.campaign.models import CampaignUser
from commcare_connect.campaign.services import seed
from commcare_connect.campaign.tests.factories import CampaignUserFactory


@pytest.mark.django_db
def test_seed_demo_users_idempotent_and_preserves_existing():
    # an existing real admin must not be clobbered
    CampaignUserFactory(
        commcare_username="ace@dimagi-ai.com",
        email="ace@dimagi-ai.com",
        name="ACE",
        role="campaign_admin",
    )
    seed.seed_campaign(fresh=True)
    seed.seed_campaign()  # idempotent
    assert CampaignUser.objects.filter(commcare_username="ace@dimagi-ai.com").count() == 1
    assert CampaignUser.objects.get(commcare_username="ace@dimagi-ai.com").role == "campaign_admin"
    assert CampaignUser.objects.count() >= 6  # demo users added
