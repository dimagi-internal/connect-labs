# commcare_connect/campaign/tests/test_bootstrap_api.py
import pytest
from django.urls import reverse

from commcare_connect.campaign.models import CampaignUser
from commcare_connect.users.models import User


def _login(client, role="campaign_admin"):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="A")
    CampaignUser.objects.create(commcare_username="a@dimagi.com", email="a@dimagi.com", name="A", role=role)
    client.force_login(u)
    s = client.session
    s["campaign_oauth"] = {
        "access_token": "AT",
        "expires_at": 9_999_999_999.0,
        "identity": {"username": "a@dimagi.com"},
    }
    s.save()
    return u


@pytest.mark.django_db
def test_bootstrap_requires_login(client):
    resp = client.get(reverse("campaign:bootstrap"))
    assert resp.status_code in (302, 403)
    assert "campaign" not in resp.json() if resp.status_code == 403 else True


@pytest.mark.django_db
def test_bootstrap_returns_seeded_data(client):
    _login(client)
    resp = client.get(reverse("campaign:bootstrap"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "campaign_admin"
    data = body["campaign"]
    assert data["CAMPAIGN"]["code"] == "MR-2026-R2"
    assert len(data["WORKERS"]) == 64
    assert data["DONORS"][0]["short"] == "Gavi"


@pytest.mark.django_db
def test_bootstrap_preserves_unicode(client):
    _login(client)
    resp = client.get(reverse("campaign:bootstrap"))
    # ensure_ascii=False keeps ₦/en-dash as real characters in the body
    assert "Measles–Rubella" in resp.content.decode("utf-8")
