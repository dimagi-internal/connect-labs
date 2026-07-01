import pytest

from connect_labs.campaign.services import serializers
from connect_labs.campaign.tests.factories import CampaignUserFactory


@pytest.mark.django_db
def test_users_serialized_with_short_role_and_you_flag(seeded_campaign):
    CampaignUserFactory(
        commcare_username="me@dimagi.com",
        email="me@dimagi.com",
        name="Me",
        role="payment_admin",
        scope="Kano",
    )
    p = serializers.bootstrap_payload(seeded_campaign, current_username="me@dimagi.com")
    me = next(u for u in p["USERS"] if u["id"] == "me@dimagi.com")
    assert me["role"] == "payment"  # short id
    assert me["you"] is True and me["scope"] == "Kano"
    assert set(me.keys()) == {"id", "name", "email", "role", "scope", "status", "last", "you"}
    others = [u for u in p["USERS"] if u["id"] != "me@dimagi.com"]
    assert all(u["you"] is False for u in others)
