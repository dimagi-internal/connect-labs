import json

import pytest
from django.urls import reverse

from connect_labs.campaign.models import CampaignUser
from connect_labs.campaign.services import seed
from connect_labs.users.models import User


@pytest.fixture
def campaign(db):
    return seed.seed_campaign(fresh=True)


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


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


@pytest.mark.django_db
def test_create_activity(client, campaign):
    _login(client)
    resp = _post(
        client,
        reverse("campaign:activity_create"),
        {
            "name": "Door-to-door — Bauchi",
            "donor": "Gavi",
            "region": "Bauchi",
            "start": "Jun 3",
            "end": "Jun 14",
            "target": 120000,
            "sync": True,
        },
    )
    assert resp.status_code == 200
    a = resp.json()["activity"]
    assert a["status"] == "Planned" and a["synced"] is True and a["reached"] == 0
    assert campaign.activities.filter(name="Door-to-door — Bauchi").exists()


@pytest.mark.django_db
def test_sync_activity(client, campaign):
    _login(client)
    act = campaign.activities.filter(synced=False).first()
    resp = _post(client, reverse("campaign:activity_sync", args=[act.activity_id]), {})
    assert resp.status_code == 200 and resp.json()["activity"]["synced"] is True
    act.refresh_from_db()
    assert act.synced is True


@pytest.mark.django_db
def test_activity_rbac(client, campaign):
    _login(client, role="reporting_user")
    resp = _post(
        client, reverse("campaign:activity_create"), {"name": "X", "donor": "Gavi", "region": "Kano", "target": 1}
    )
    assert resp.status_code == 403
