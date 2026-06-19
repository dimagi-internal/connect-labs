import pytest
from django.urls import reverse

from commcare_connect.campaign.models import CampaignUser
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_expired_campaign_session_logs_out_on_app(client):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    CampaignUser.objects.create(
        commcare_username="a@dimagi.com", email="a@dimagi.com", name="A", role="campaign_admin"
    )
    client.force_login(u)
    s = client.session
    s["campaign_oauth"] = {"access_token": "AT", "expires_at": 1.0}  # long expired
    s.save()
    client.get(reverse("campaign:app"))
    # Logged out -> app view's own login gate (Task 8) will redirect; here we
    # assert the middleware cleared auth by checking the session flushed user.
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_valid_campaign_session_passes_through(client, settings):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    CampaignUser.objects.create(
        commcare_username="a@dimagi.com", email="a@dimagi.com", name="A", role="campaign_admin"
    )
    client.force_login(u)
    s = client.session
    s["campaign_oauth"] = {"access_token": "AT", "expires_at": 9_999_999_999.0}
    s.save()
    resp = client.get(reverse("campaign:ping"))  # excluded path, always 200
    assert resp.status_code == 200
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_non_campaign_path_is_untouched(client):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    client.force_login(u)
    # No campaign_oauth at all; hitting a non-/campaign/ path must not log out.
    client.get("/health/")
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_valid_session_passes_through_guarded_app_page(client):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    CampaignUser.objects.create(
        commcare_username="a@dimagi.com", email="a@dimagi.com", name="A", role="campaign_admin"
    )
    client.force_login(u)
    s = client.session
    s["campaign_oauth"] = {
        "access_token": "AT",
        "expires_at": 9_999_999_999.0,
        "identity": {"username": "a@dimagi.com"},
    }
    s.save()
    resp = client.get(reverse("campaign:app"))
    assert resp.status_code == 200
    assert "_auth_user_id" in client.session
