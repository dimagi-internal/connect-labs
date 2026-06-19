import time

import pytest
from django.urls import reverse

from commcare_connect.campaign.models import CampaignUser
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_ping_is_wired(client):
    resp = client.get(reverse("campaign:ping"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.django_db
def test_app_redirects_anonymous_to_login(client):
    resp = client.get(reverse("campaign:app"))
    assert resp.status_code == 302
    assert "/campaign/login/" in resp.url


@pytest.mark.django_db
def test_app_renders_for_authorized_user(client):
    u = User.objects.create_user(username="admin@dimagi.com", email="admin@dimagi.com", password="pw", name="Admin")
    CampaignUser.objects.create(
        commcare_username="admin@dimagi.com",
        email="admin@dimagi.com",
        name="Admin",
        role="campaign_admin",
        status=CampaignUser.Status.ACTIVE,
    )
    client.force_login(u)
    session = client.session
    session["campaign_oauth"] = {
        "access_token": "AT",
        "expires_at": time.time() + 3600,
        "identity": {"username": "admin@dimagi.com"},
    }
    session.save()

    resp = client.get(reverse("campaign:app"))
    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="campaign-bootstrap"' in content
    assert 'id="root"' in content
    assert "campaign/app.jsx" in content
    assert b"campaign/data-api.js" in resp.content
    assert b"campaign/tab_overview.jsx" in resp.content
    assert b"campaign/tab_workers.jsx" in resp.content
    assert b"campaign/tab_workers_kyc.jsx" in resp.content
    assert b"campaign/tab_workers_profile.jsx" in resp.content
