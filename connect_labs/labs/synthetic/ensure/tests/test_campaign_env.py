"""The campaign-utility-tool env realizes a national synthetic campaign via the
standard ensure engine (synthetic_env_ensure path)."""
from __future__ import annotations

import pytest

from connect_labs.campaign.models import Campaign, WorkerCase
from connect_labs.campaign.services import dev_boundaries, synthetic_campaign
from connect_labs.labs.synthetic.ensure.engine import ensure_synthetic_data
from connect_labs.labs.synthetic.ensure.registry import get_env_path, list_envs

pytestmark = pytest.mark.django_db


def test_campaign_env_is_discovered():
    keys = {e["env"] for e in list_envs()}
    assert "campaign-utility-tool" in keys


def test_ensure_realizes_the_campaign():
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    path = str(get_env_path("campaign-utility-tool"))
    realized = ensure_synthetic_data(path)
    # the campaign ensurer's marker is present and the campaign is built
    marker = realized["campaign_MR-NAT-2026"]
    assert marker["code"] == "MR-NAT-2026"
    assert marker["commcare_domain"] == "campaign-synthetic-mr-nat-2026"
    assert marker["workers"] == 5000
    assert Campaign.objects.filter(code="MR-NAT-2026").exists()
    assert WorkerCase.objects.filter(campaign__code="MR-NAT-2026").count() == 5000


def test_ensure_is_idempotent():
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    path = str(get_env_path("campaign-utility-tool"))
    ensure_synthetic_data(path)
    ensure_synthetic_data(path)  # re-run — rebuilds in place, no duplicate
    assert Campaign.objects.filter(code="MR-NAT-2026").count() == 1
    assert WorkerCase.objects.filter(campaign__code="MR-NAT-2026").count() == 5000


def test_campaign_data_fixes():
    """Funding reconciles (committed > spent), coverage varies per region, and
    activities + audit logs are seeded (the iter-1 fixes)."""
    from connect_labs.campaign.models import RegionPlan

    dev_boundaries.seed_demo_boundaries(lgas_per_state=2, wards_per_lga=2)
    c = synthetic_campaign.build_synthetic_campaign(worker_count=400, code="TSTFIX", name="Test")
    plans = RegionPlan.objects.filter(region__campaign=c)
    committed = sum(d.committed for d in c.donors.all())
    spent = sum(p.spent for p in plans)
    assert committed > spent, f"funding must reconcile: committed {committed} > spent {spent}"
    cov = [round(x["visited"] / x["hh"] * 100) for x in c.household_stat.coverage]
    assert len(set(cov)) > 3, f"coverage must vary, got {set(cov)}"
    assert c.activities.count() > 0, "activities must be seeded"
    assert c.audit_logs.count() > 0, "audit logs must be seeded"
