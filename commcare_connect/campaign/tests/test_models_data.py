import pytest

from commcare_connect.campaign.models import Campaign, Donor, HouseholdStat, Region, RegionPlan, WorkerRole, Workspace


@pytest.mark.django_db
def test_core_models_roundtrip():
    ws = Workspace.objects.create(country="Nigeria", name="Nigeria", slug="nigeria")
    c = Campaign.objects.create(
        workspace=ws,
        name="Measles–Rubella Vaccination Campaign",
        code="MR-2026-R2",
        round="Round 2",
        country="Nigeria",
        period="May 18 – Jun 14, 2026",
        status="Active",
        days_elapsed=16,
        days_total=28,
        target_pop=4280000,
    )
    Donor.objects.create(
        campaign=c,
        donor_id="gavi",
        name="Gavi, the Vaccine Alliance",
        short="Gavi",
        committed=2400000,
        color="#5D70D2",
        order=0,
    )
    r = Region.objects.create(
        campaign=c, region_id="kano", name="Kano", lgas=["Dala", "Fagge", "Gwale", "Nassarawa", "Tarauni"], order=0
    )
    RegionPlan.objects.create(
        region=r,
        planned_wf=820,
        actual_wf=795,
        budget=1850000,
        spent=1128500,
        target=920000,
        reached=607200,
        vaccine_alloc=980000,
        vaccine_used=627200,
    )
    WorkerRole.objects.create(campaign=c, role_id="vaccinator", name="Vaccinator", rate=4500, order=0)
    HouseholdStat.objects.create(
        campaign=c,
        registered=486200,
        visited=312800,
        members=2140000,
        members_reached=1386000,
        coverage=[{"name": "Kano", "hh": 142000, "visited": 100820}],
    )
    assert c.donors.count() == 1
    assert c.regions.first().lgas[0] == "Dala"
    assert r.plan.budget == 1850000
    assert c.household_stat.registered == 486200
