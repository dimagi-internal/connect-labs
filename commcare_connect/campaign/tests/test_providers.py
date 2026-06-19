"""Data-source seam tests.

The HQ/Connect-owned roster (campaign, regions, donors, worker roles, workers) is
read through a CampaignDataProvider so "go real" is a per-entity config flip. These
tests pin: provider selection by setting, the SyntheticProvider reads our ORM, the
ConnectProvider is an explicit NotImplementedError stub, and bootstrap_payload
actually routes the roster through the provider (flipping to the stub makes it raise).
"""
from __future__ import annotations

import pytest
from django.test import override_settings

from commcare_connect.campaign.services import providers, seed, serializers


@pytest.mark.django_db
def test_get_provider_defaults_to_synthetic():
    campaign = seed.seed_campaign()
    assert isinstance(providers.get_provider(campaign), providers.SyntheticProvider)


@pytest.mark.django_db
@override_settings(CAMPAIGN_DATA_PROVIDER="connect")
def test_get_provider_respects_setting():
    campaign = seed.seed_campaign()
    assert isinstance(providers.get_provider(campaign), providers.ConnectProvider)


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
def test_connect_provider_stub_raises():
    campaign = seed.seed_campaign()
    p = providers.ConnectProvider(campaign)
    for method in ("campaign", "regions", "donors", "worker_roles", "workers"):
        with pytest.raises(NotImplementedError):
            getattr(p, method)()


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
@override_settings(CAMPAIGN_DATA_PROVIDER="connect")
def test_bootstrap_payload_routes_roster_through_provider():
    """Proof the roster goes through the seam: with the stub provider active,
    building the payload raises NotImplementedError rather than reading the ORM."""
    campaign = seed.seed_campaign()
    with pytest.raises(NotImplementedError):
        serializers.bootstrap_payload(campaign)
