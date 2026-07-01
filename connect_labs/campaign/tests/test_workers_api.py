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


@pytest.mark.django_db
def test_resolve_dupe_requires_keep(client, campaign):
    _login(client)
    w = campaign.workers.exclude(fraud_rules=[]).first()
    resp = _post(client, reverse("campaign:kyc_resolve_dupe", args=[w.worker_id]), {})
    assert resp.status_code == 400


@pytest.mark.django_db
def test_investigation_rejects_bad_status(client, campaign):
    _login(client)
    w = campaign.workers.exclude(fraud_rules=[]).first()
    resp = _post(client, reverse("campaign:kyc_investigation", args=[w.worker_id]), {"status": "banana"})
    assert resp.status_code == 400


@pytest.mark.django_db
def test_investigation_note_only_preserves_status(client, campaign):
    _login(client)
    w = campaign.workers.exclude(fraud_rules=[]).first()
    # note-only update (no status) must succeed and not 400
    resp = _post(client, reverse("campaign:kyc_investigation", args=[w.worker_id]), {"note": "checking"})
    assert resp.status_code == 200


@pytest.mark.django_db
def test_csrf_round_trip_via_meta_token(campaign):
    # This project uses CSRF_USE_SESSIONS (no csrftoken cookie); the token is
    # rendered into a <meta> tag the JS reads. Verify the full round-trip under
    # real CSRF enforcement — the default test client disables CSRF, which is why
    # the cookie-based transport silently 403'd on the deployed site.
    import re

    from django.test import Client

    c = Client(enforce_csrf_checks=True)
    _login(c)

    page = c.get(reverse("campaign:app"))
    assert page.status_code == 200
    m = re.search(rb'name="csrf-token" content="([^"]+)"', page.content)
    assert m, "app page must render a csrf-token meta tag"
    token = m.group(1).decode()
    assert len(token) >= 30

    w = campaign.workers.filter(fraud_rules=[]).exclude(kyc="rejected").first()
    body = json.dumps({"worker_ids": [w.worker_id], "status": "approved"})

    # Without the token, CSRF enforcement rejects the write.
    no_tok = c.post(reverse("campaign:pay_set_status"), data=body, content_type="application/json")
    assert no_tok.status_code == 403

    # With the rendered token in X-CSRFToken, the write succeeds.
    ok = c.post(reverse("campaign:pay_set_status"), data=body, content_type="application/json", HTTP_X_CSRFTOKEN=token)
    assert ok.status_code == 200
