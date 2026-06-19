import pytest

from commcare_connect.campaign.services import seed


@pytest.mark.django_db
def test_seed_creates_activities_and_microplans():
    c = seed.seed_campaign(fresh=True)
    assert c.activities.count() == 6
    assert c.microplans.count() == 18  # one per LGA across the 5 regions (5+4+3+3+3)
    # microplans roll up to each region's RegionPlan totals
    for region in c.regions.all():
        mps = c.microplans.filter(region_id=region.region_id)
        assert mps.count() == len(region.lgas)
        assert sum(m.planned_wf for m in mps) == region.plan.planned_wf
        assert sum(m.actual_wf for m in mps) == region.plan.actual_wf
        assert sum(m.budget for m in mps) == region.plan.budget
        assert sum(m.spent for m in mps) == region.plan.spent
        assert sum(m.target for m in mps) == region.plan.target
        assert sum(m.reached for m in mps) == region.plan.reached
        assert sum(m.doses for m in mps) == region.plan.vaccine_alloc
        assert sum(m.doses_used for m in mps) == region.plan.vaccine_used
    # every microplan: objective == round(target*goal_pct/100); plannedWf == sum(role.planned)
    for m in c.microplans.all():
        assert m.objective == round(m.target * m.goal_pct / 100)
        assert m.planned_wf == sum(r["planned"] for r in m.roles)


@pytest.mark.django_db
def test_seed_activities_synced_rule():
    c = seed.seed_campaign(fresh=True)
    for a in c.activities.all():
        expected = a.status == "Completed" or a.activity_id == "ACT-01"
        assert a.synced == expected
