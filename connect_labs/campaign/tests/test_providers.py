"""Data-source seam tests.

The CommCare-HQ-owned roster is read through a CampaignDataProvider. A domain-less
campaign uses the legacy SyntheticProvider (local ORM); a campaign bound to a CommCare
project space uses the CommCareProvider, which reads workers as cases through the Case
API (served in-app from WorkerCase for a synthetic domain). These tests pin provider
selection, the synthetic ORM read, and that the CommCareProvider reads worker cases.
"""
from __future__ import annotations

import pytest
from django.test import override_settings

from connect_labs.campaign.models import HouseholdStat, SyntheticCommCareDomain, WorkerCase
from connect_labs.campaign.services import providers, seed, serializers
from connect_labs.campaign.tests.factories import CampaignFactory


def _domain_campaign(domain="campaign-synthetic-test", workers=3):
    SyntheticCommCareDomain.objects.create(domain=domain, label="Test", enabled=True)
    campaign = CampaignFactory(commcare_domain=domain)
    HouseholdStat.objects.create(campaign=campaign, registered=1, visited=1, members=1, members_reached=1, coverage=[])
    for i in range(workers):
        WorkerCase.objects.create(
            campaign=campaign,
            case_id=f"wc-{i}",
            case_type="campaign_worker",
            worker_id=f"W{1000 + i}",
            region_id="st-0",
            lga="Kano LGA 1",
            properties={
                "worker_id": f"W{1000 + i}",
                "name": f"Worker {i}",
                "first": "W",
                "last": str(i),
                "gender": "F",
                "phone": "+234800",
                "region_id": "st-0",
                "lga": "Kano LGA 1",
                "role_id": "vaccinator",
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
    return campaign


@pytest.mark.django_db
def test_get_provider_defaults_to_synthetic():
    campaign = seed.seed_campaign()
    assert isinstance(providers.get_provider(campaign), providers.SyntheticProvider)


@pytest.mark.django_db
@override_settings(CAMPAIGN_DATA_PROVIDER="commcare")
def test_get_provider_respects_setting():
    campaign = seed.seed_campaign()
    assert isinstance(providers.get_provider(campaign), providers.CommCareProvider)


@pytest.mark.django_db
@override_settings(CAMPAIGN_DATA_PROVIDER="nope")
def test_get_provider_unknown_raises():
    campaign = seed.seed_campaign()
    with pytest.raises(Exception):
        providers.get_provider(campaign)


@pytest.mark.django_db
def test_synthetic_provider_matches_orm():
    campaign = seed.seed_campaign()
    p = providers.SyntheticProvider(campaign)
    assert p.campaign() == campaign
    assert {r.region_id for r in p.regions()} == {r.region_id for r in campaign.regions.all()}
    assert {d.donor_id for d in p.donors()} == {d.donor_id for d in campaign.donors.all()}
    assert {r.role_id for r in p.worker_roles()} == {r.role_id for r in campaign.worker_roles.all()}
    assert {w.worker_id for w in p.workers()} == {w.worker_id for w in campaign.workers.all()}
    # regions are select_related on plan so _planning() needs no extra query
    region = next(iter(p.regions()))
    assert region.plan is not None


@pytest.mark.django_db
def test_domain_campaign_uses_commcare_provider():
    campaign = _domain_campaign()
    assert isinstance(providers.get_provider(campaign), providers.CommCareProvider)


@pytest.mark.django_db
def test_commcare_provider_reads_workers_as_cases():
    campaign = _domain_campaign(workers=4)
    p = providers.CommCareProvider(campaign)
    workers = p.workers()
    assert len(workers) == 4
    # worker objects expose the case properties as attributes (what _worker reads)
    assert {w.worker_id for w in workers} == {"W1000", "W1001", "W1002", "W1003"}
    assert all(w.kyc == "approved" and w.amount == 45000 for w in workers)


@pytest.mark.django_db
def test_bootstrap_payload_reads_workers_via_case_api_for_domain_campaign():
    campaign = _domain_campaign(workers=5)
    payload = serializers.bootstrap_payload(campaign)
    # WORKERS came from WorkerCase via the Case API, not the (empty) Worker ORM table
    assert campaign.workers.count() == 0
    assert len(payload["WORKERS"]) == 5


@pytest.mark.django_db
def test_bootstrap_payload_unchanged_under_synthetic():
    """The seam must not change the synthetic payload — full key set is identical."""
    campaign = seed.seed_campaign()
    payload = serializers.bootstrap_payload(campaign)
    assert payload["CAMPAIGN"]["name"] == campaign.name
    assert len(payload["WORKERS"]) == campaign.workers.count()
    assert len(payload["REGIONS"]) == campaign.regions.count()
    assert len(payload["DONORS"]) == campaign.donors.count()
    assert len(payload["ROLES"]) == campaign.worker_roles.count()
    # tool-owned entities still present (not behind the seam)
    assert len(payload["MICROPLANS"]) == campaign.microplans.count()
    assert len(payload["ACTIVITIES"]) == campaign.activities.count()


@pytest.mark.django_db
def test_synthetic_domain_short_circuits_case_api():
    """A registered synthetic domain is served in-app (no real CommCare call)."""
    from connect_labs.campaign.services import commcare_api, commcare_cases_backend

    campaign = _domain_campaign(workers=2)
    assert commcare_cases_backend.is_synthetic_domain(campaign.commcare_domain) is True
    cases = commcare_api.fetch_cases(campaign.commcare_domain, "campaign_worker")
    assert len(cases) == 2
    # Case-API-v2 shape
    assert {"case_id", "case_type", "case_name", "properties"} <= set(cases[0].keys())
    assert cases[0]["case_type"] == "campaign_worker"
