"""Worker write-path on a CommCare-domain campaign.

Per the ownership rule, worker/KYC mutations on a domain-bound campaign land on the
WorkerCase (the CommCare-owned store), NOT a tool-local copy. These tests drive the
real write endpoints against a synthetic-domain campaign and assert the case is
updated (and the fraud guard still holds), with no Worker ORM rows involved.
"""
from __future__ import annotations

import json

import pytest
from django.urls import reverse

from connect_labs.campaign.models import (
    Campaign,
    HouseholdStat,
    SyntheticCommCareDomain,
    Worker,
    WorkerCase,
    WorkerRole,
    Workspace,
)

pytestmark = pytest.mark.django_db


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


def _props(worker_id, **overrides):
    p = {
        "worker_id": worker_id,
        "name": f"Worker {worker_id}",
        "first": "W",
        "last": worker_id,
        "gender": "F",
        "phone": "+234800",
        "region_id": "st-0",
        "lga": "Kano LGA 1",
        "role_id": "vaccinator",
        "rate": 4500,
        "days_worked": 10,
        "days_approved": 5,
        "amount": 45000,
        "kyc": "approved",
        "pay": "pending",
        "bank": "GTBank",
        "acct": "1",
        "nin": "1",
        "passport": None,
        "enrolled": "May 12",
        "attendance": 62,
        "prior_campaigns": 1,
        "duplicate": False,
        "dup_with": None,
        "fraud_rules": [],
        "linked": [],
        "investigation": None,
        "documents": [],
    }
    p.update(overrides)
    return p


@pytest.fixture
def domain_campaign(db):
    domain = "campaign-synthetic-writes"
    SyntheticCommCareDomain.objects.create(domain=domain, enabled=True)
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    c = Campaign.objects.create(workspace=ws, name="Nat", code="NAT", commcare_domain=domain)
    WorkerRole.objects.create(campaign=c, role_id="vaccinator", name="Vaccinator", rate=4500)
    HouseholdStat.objects.create(campaign=c, registered=1, visited=1, members=1, members_reached=1, coverage=[])
    WorkerCase.objects.create(
        campaign=c,
        case_id="wc-clean",
        case_type="campaign_worker",
        worker_id="W1",
        region_id="st-0",
        lga="Kano LGA 1",
        properties=_props("W1"),
    )
    WorkerCase.objects.create(
        campaign=c,
        case_id="wc-flagged",
        case_type="campaign_worker",
        worker_id="W2",
        region_id="st-0",
        lga="Kano LGA 1",
        properties=_props("W2", fraud_rules=["Duplicate NIN"], duplicate=True),
    )
    return c


def test_pay_approval_writes_to_the_case_not_a_worker_row(client, login_as, domain_campaign):
    login_as(client)
    resp = _post(client, reverse("campaign:pay_set_status"), {"status": "approved", "worker_ids": ["W1"]})
    assert resp.status_code == 200
    # persisted on the WorkerCase, and no Worker ORM row was created
    wc = WorkerCase.objects.get(worker_id="W1")
    assert wc.properties["pay"] == "approved"
    assert wc.properties["days_approved"] == wc.properties["days_worked"]
    assert Worker.objects.count() == 0


def test_fraud_guard_blocks_flagged_worker_on_the_case(client, login_as, domain_campaign):
    login_as(client)
    resp = _post(client, reverse("campaign:pay_set_status"), {"status": "approved", "worker_ids": ["W1", "W2"]})
    body = resp.json()
    assert "W2" in body["blocked"]  # flagged worker blocked
    assert {w["id"] for w in body["workers"]} == {"W1"}
    assert WorkerCase.objects.get(worker_id="W2").properties["pay"] == "pending"  # unchanged


def test_kyc_decision_writes_to_the_case(client, login_as, domain_campaign):
    login_as(client)
    resp = _post(client, reverse("campaign:kyc_status", args=["W1"]), {"status": "review"})
    assert resp.status_code == 200
    assert WorkerCase.objects.get(worker_id="W1").properties["kyc"] == "review"


def test_read_after_write_is_consistent_via_bootstrap(client, login_as, domain_campaign):
    """A write lands on the case; the next bootstrap (Case API read) reflects it."""
    from connect_labs.campaign.services import serializers

    login_as(client)
    _post(client, reverse("campaign:kyc_status", args=["W1"]), {"status": "rejected"})
    payload = serializers.bootstrap_payload(domain_campaign)
    w1 = next(w for w in payload["WORKERS"] if w["id"] == "W1")
    assert w1["kyc"] == "rejected"
