"""Report export tests (the 'Export data' button + custom-report builder)."""
from __future__ import annotations

import pytest
from django.urls import reverse

from connect_labs.campaign.models import (
    Activity,
    Campaign,
    HouseholdStat,
    ReportDay,
    SyntheticCommCareDomain,
    WorkerCase,
    WorkerRole,
    Workspace,
)
from connect_labs.campaign.services import reports

pytestmark = pytest.mark.django_db


@pytest.fixture
def campaign():
    domain = "campaign-synthetic-rep"
    SyntheticCommCareDomain.objects.create(domain=domain, enabled=True)
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    c = Campaign.objects.create(workspace=ws, name="Rep", code="REP", commcare_domain=domain)
    WorkerRole.objects.create(campaign=c, role_id="vaccinator", name="Vaccinator", rate=4500)
    HouseholdStat.objects.create(
        campaign=c,
        registered=100,
        visited=60,
        members=400,
        members_reached=250,
        coverage=[{"name": "Kano", "hh": 100, "visited": 60}],
    )
    ReportDay.objects.create(campaign=c, day="D1", enrolled=10, attended=9, paid=7, order=0)
    Activity.objects.create(
        campaign=c,
        activity_id="ACT-01",
        name="Round 2",
        donor="Gavi",
        region="Kano",
        status="Active",
        target=1000,
        reached=500,
        workers=20,
    )
    for i in range(3):
        WorkerCase.objects.create(
            campaign=c,
            case_id=f"wc-{i}",
            case_type="campaign_worker",
            worker_id=f"W{i}",
            region_id="st-0",
            lga="L",
            properties={
                "worker_id": f"W{i}",
                "name": f"Worker {i}",
                "first": "W",
                "last": str(i),
                "region_id": "st-0",
                "lga": "L",
                "role_id": "vaccinator",
                "gender": "F",
                "phone": "+234800",
                "rate": 4500,
                "days_worked": 10,
                "days_approved": 9,
                "amount": 45000,
                "kyc": "approved",
                "pay": "approved",
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
            },
        )
    return c


def test_worker_report_honors_columns_and_group(campaign):
    rows = reports.build_report(
        campaign, report_type="worker_payments", columns=["Worker ID", "Amount"], group_by="Region"
    )
    assert rows[0] == ["Region", "Worker ID", "Amount"]  # group col prepended
    assert len(rows) == 4  # header + 3 workers
    assert rows[1][1].startswith("W")


def test_household_and_activity_and_summary_reports(campaign):
    hh = reports.build_report(campaign, report_type="household_coverage")
    assert hh[0] == ["Region", "Households", "Visited", "Coverage %"]
    assert hh[1] == ["Kano", 100, 60, 60.0]
    act = reports.build_report(campaign, report_type="activity_performance")
    assert act[1][0] == "ACT-01" and act[1][-1] == 50.0
    summ = reports.build_report(campaign, report_type="reporting_summary")
    assert summ[1] == ["D1", 10, 9, 7]


def test_export_endpoint_streams_csv(client, login_as, campaign):
    login_as(client)
    url = reverse("campaign:report_export") + "?campaign=REP&type=worker_payments&columns=Worker ID,Name"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert "attachment" in resp["Content-Disposition"]
    body = b"".join(resp.streaming_content).decode()
    assert body.splitlines()[0] == "Worker ID,Name"
    assert "Worker 0" in body


def test_export_rejects_bad_type(client, login_as, campaign):
    login_as(client)
    resp = client.get(reverse("campaign:report_export") + "?campaign=REP&type=bogus")
    assert resp.status_code == 400
