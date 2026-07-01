"""Scale cliff-fix tests: server SUMMARY + capped bootstrap + paginated endpoint."""
from __future__ import annotations

import pytest
from django.urls import reverse

from connect_labs.campaign.models import (
    Campaign,
    HouseholdStat,
    SyntheticCommCareDomain,
    WorkerCase,
    WorkerRole,
    Workspace,
)
from connect_labs.campaign.services import serializers

pytestmark = pytest.mark.django_db


def _props(i, kyc="approved", pay="approved", gender="F", fraud=None, role="vaccinator"):
    return {
        "worker_id": f"W{i}",
        "name": f"Worker {i}",
        "first": "W",
        "last": str(i),
        "gender": gender,
        "phone": "+234800",
        "region_id": "st-0",
        "lga": "L",
        "role_id": role,
        "rate": 4500,
        "days_worked": 10,
        "days_approved": 9,
        "amount": 45000,
        "kyc": kyc,
        "pay": pay,
        "bank": "GTBank",
        "acct": "1",
        "nin": f"NIN{i}",
        "passport": None,
        "enrolled": "May 12",
        "attendance": 62,
        "prior_campaigns": 1,
        "duplicate": bool(fraud),
        "dup_with": None,
        "fraud_rules": fraud or [],
        "linked": [],
        "investigation": None,
        "documents": [],
    }


@pytest.fixture
def big_campaign():
    domain = "campaign-synthetic-scale"
    SyntheticCommCareDomain.objects.create(domain=domain, enabled=True)
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    c = Campaign.objects.create(workspace=ws, name="Scale", code="SCALE", commcare_domain=domain)
    WorkerRole.objects.create(campaign=c, role_id="vaccinator", name="Vaccinator", rate=4500)
    HouseholdStat.objects.create(campaign=c, registered=1, visited=1, members=1, members_reached=1, coverage=[])
    cases = []
    for i in range(500):
        kyc = "approved" if i % 2 else "pending"
        cases.append(
            WorkerCase(
                campaign=c,
                case_id=f"wc-{i}",
                case_type="campaign_worker",
                worker_id=f"W{i}",
                region_id="st-0",
                lga="L",
                properties=_props(i, kyc=kyc, fraud=["Duplicate NIN"] if i < 7 else None),
            )
        )
    WorkerCase.objects.bulk_create(cases)
    return c


def test_bootstrap_caps_workers_and_ships_summary(big_campaign):
    payload = serializers.bootstrap_payload(big_campaign)
    # the full list is NOT shipped — capped to the page size — so no 38 MB bootstrap
    assert len(payload["WORKERS"]) == serializers.WORKERS_PAGE_SIZE
    assert payload["WORKERS_TOTAL"] == 500
    s = payload["WORKERS_SUMMARY"]
    assert s["total"] == 500
    assert s["kyc"]["approved"] + s["kyc"]["pending"] == 500
    assert s["flagged"] == 7  # the 7 fraud-flagged
    assert "byRole" in s and s["byRole"]["Vaccinator"]["f"] == 500  # all female in fixture


def test_workers_endpoint_paginates(client, login_as, big_campaign):
    login_as(client)
    r1 = client.get(reverse("campaign:workers_list") + "?campaign=SCALE&page=1&page_size=50")
    body = r1.json()
    assert body["total"] == 500
    assert len(body["workers"]) == 50
    r2 = client.get(reverse("campaign:workers_list") + "?campaign=SCALE&page=2&page_size=50")
    # different page, no overlap
    assert {w["id"] for w in r1.json()["workers"]}.isdisjoint({w["id"] for w in r2.json()["workers"]})


def test_workers_endpoint_filters(client, login_as, big_campaign):
    login_as(client)
    pending = client.get(reverse("campaign:workers_list") + "?campaign=SCALE&kyc=pending&page_size=500").json()
    assert all(w["kyc"] == "pending" for w in pending["workers"])
    assert pending["total"] == 250
    flagged = client.get(reverse("campaign:workers_list") + "?campaign=SCALE&fraud=flagged&page_size=500").json()
    assert flagged["total"] == 7
    found = client.get(reverse("campaign:workers_list") + "?campaign=SCALE&q=W123&page_size=500").json()
    assert any(w["id"] == "W123" for w in found["workers"])
