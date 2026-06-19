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


def _roles():
    return [
        {"roleId": "vaccinator", "role": "Vaccinator", "rate": 4500, "planned": 50, "actual": 0},
        {"roleId": "recorder", "role": "Recorder", "rate": 3500, "planned": 20, "actual": 0},
    ]


@pytest.mark.django_db
def test_create_microplan_derivations(client, campaign):
    _login(client)
    resp = _post(
        client,
        reverse("campaign:microplan_create"),
        {
            "region": "Kano",
            "regionId": "kano",
            "lga": "Dala",
            "settlements": 12,
            "wards": 5,
            "target": 100000,
            "goalPct": 95,
            "roles": _roles(),
            "doses": 90000,
            "coldBoxes": 5,
            "vehicles": 2,
            "budget": 500000,
        },
    )
    assert resp.status_code == 200
    m = resp.json()["microplan"]
    assert m["objective"] == 95000  # round(100000*95/100)
    assert m["plannedWf"] == 70  # 50+20
    assert m["plannedToDate"] == round(500000 * campaign.days_elapsed / campaign.days_total)
    assert m["actualWf"] == 0 and m["spent"] == 0 and m["status"] == "Planned"
    assert m["owner"] == "Amara"


@pytest.mark.django_db
def test_edit_target_and_budget(client, campaign):
    _login(client)
    mp = campaign.microplans.first()
    rt = _post(client, reverse("campaign:microplan_target", args=[mp.microplan_id]), {"target": 200000, "goalPct": 90})
    assert rt.status_code == 200 and rt.json()["microplan"]["objective"] == 180000
    rb = _post(client, reverse("campaign:microplan_budget", args=[mp.microplan_id]), {"budget": 999000})
    assert rb.status_code == 200
    mp.refresh_from_db()
    assert mp.budget == 999000 and mp.planned_to_date == round(999000 * campaign.days_elapsed / campaign.days_total)


@pytest.mark.django_db
def test_edit_microplan_preserves_actuals(client, campaign):
    _login(client)
    mp = campaign.microplans.exclude(actual_wf=0).first()
    actual_wf_before, spent_before = mp.actual_wf, mp.spent
    resp = _post(
        client,
        reverse("campaign:microplan_update", args=[mp.microplan_id]),
        {
            "region": mp.region,
            "regionId": mp.region_id,
            "lga": mp.lga,
            "settlements": 99,
            "wards": 9,
            "target": 123456,
            "goalPct": 95,
            "roles": _roles(),
            "doses": 1,
            "coldBoxes": 1,
            "vehicles": 1,
            "budget": 700000,
        },
    )
    assert resp.status_code == 200
    m = resp.json()["microplan"]
    assert m["settlements"] == 99 and m["objective"] == round(123456 * 95 / 100)
    mp.refresh_from_db()
    assert mp.actual_wf == actual_wf_before and mp.spent == spent_before  # actuals preserved


@pytest.mark.django_db
def test_microplan_rbac(client, campaign):
    _login(client, role="reporting_user")
    mp = campaign.microplans.first()
    assert (
        _post(
            client, reverse("campaign:microplan_target", args=[mp.microplan_id]), {"target": 1, "goalPct": 95}
        ).status_code
        == 403
    )
