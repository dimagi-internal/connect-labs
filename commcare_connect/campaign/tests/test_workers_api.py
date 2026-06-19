import json

import pytest
from django.urls import reverse

from commcare_connect.campaign.models import CampaignUser
from commcare_connect.campaign.services import seed
from commcare_connect.users.models import User


@pytest.fixture
def campaign(db):
    return seed.seed_campaign(fresh=True)


def _login(client, role="campaign_admin"):
    u = User.objects.create(username="a@dimagi.com", email="a@dimagi.com", name="Amara")
    CampaignUser.objects.create(commcare_username="a@dimagi.com", email="a@dimagi.com", name="Amara", role=role)
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
def test_set_status_approves_clean_blocks_flagged(client, campaign):
    _login(client)
    clean = campaign.workers.filter(fraud_rules=[]).exclude(kyc="rejected").first()
    flagged = campaign.workers.exclude(fraud_rules=[]).first()
    resp = _post(
        client,
        reverse("campaign:pay_set_status"),
        {"worker_ids": [clean.worker_id, flagged.worker_id], "status": "approved"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert flagged.worker_id in body["blocked"]
    assert any(w["id"] == clean.worker_id and w["pay"] == "approved" for w in body["workers"])
    clean.refresh_from_db()
    assert clean.pay == "approved"


@pytest.mark.django_db
def test_queue_persists_days(client, campaign):
    _login(client)
    w = campaign.workers.filter(fraud_rules=[]).exclude(kyc="rejected").first()
    resp = _post(client, reverse("campaign:pay_queue", args=[w.worker_id]), {"approved_count": 3})
    assert resp.status_code == 200
    w.refresh_from_db()
    assert w.pay == "approved" and w.days_approved == 3


@pytest.mark.django_db
def test_queue_blocked_returns_400(client, campaign):
    _login(client)
    w = campaign.workers.exclude(fraud_rules=[]).first()
    resp = _post(client, reverse("campaign:pay_queue", args=[w.worker_id]), {"approved_count": 3})
    assert resp.status_code == 400


@pytest.mark.django_db
def test_kyc_status_and_guard(client, campaign):
    _login(client)
    clean = campaign.workers.filter(fraud_rules=[]).first()
    resp = _post(client, reverse("campaign:kyc_status", args=[clean.worker_id]), {"status": "review"})
    assert resp.status_code == 200 and resp.json()["worker"]["kyc"] == "review"
    flagged = campaign.workers.exclude(fraud_rules=[]).first()
    resp2 = _post(client, reverse("campaign:kyc_status", args=[flagged.worker_id]), {"status": "approved"})
    assert resp2.status_code == 400


@pytest.mark.django_db
def test_resolve_duplicate_and_investigation(client, campaign):
    _login(client)
    flagged = campaign.workers.exclude(fraud_rules=[]).first()
    r1 = _post(client, reverse("campaign:kyc_resolve_dupe", args=[flagged.worker_id]), {"keep": True})
    assert r1.status_code == 200 and r1.json()["worker"]["fraudRules"] == []
    r2 = _post(
        client,
        reverse("campaign:kyc_investigation", args=[flagged.worker_id]),
        {"status": "Resolved", "outcome": "false positive", "note": "cleared"},
    )
    assert r2.status_code == 200
    inv = r2.json()["worker"]["investigation"]
    assert inv["status"] == "Resolved" and inv["notes"][0]["by"] == "Amara"


@pytest.mark.django_db
def test_rbac_reporting_user_cannot_write(client, campaign):
    _login(client, role="reporting_user")
    w = campaign.workers.first()
    resp = _post(client, reverse("campaign:pay_set_status"), {"worker_ids": [w.worker_id], "status": "approved"})
    assert resp.status_code == 403
    resp2 = _post(client, reverse("campaign:kyc_status", args=[w.worker_id]), {"status": "review"})
    assert resp2.status_code == 403
