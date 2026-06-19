import pytest

from commcare_connect.campaign.models import Activity, Campaign, Microplan, Workspace


@pytest.fixture
def campaign(db):
    ws = Workspace.objects.create(country="Nigeria", name="Nigeria", slug="nigeria")
    return Campaign.objects.create(workspace=ws, name="C", code="X", days_elapsed=16, days_total=28)


@pytest.mark.django_db
def test_activity_roundtrip(campaign):
    a = Activity.objects.create(
        campaign=campaign,
        activity_id="ACT-01",
        name="Fixed-post Kano",
        donor="Gavi",
        status="Active",
        start="May 18",
        end="Jun 14",
        requests=1840,
        workers=142,
        region="Kano",
        target=920000,
        reached=612000,
        synced=True,
    )
    assert campaign.activities.count() == 1
    assert a.synced is True
    assert a.reached == 612000


@pytest.mark.django_db
def test_microplan_roundtrip(campaign):
    role_data = {"roleId": "vaccinator", "role": "Vaccinator", "rate": 4500, "planned": 72, "actual": 70}
    m = Microplan.objects.create(
        campaign=campaign,
        microplan_id="MP-101",
        region_id="kano",
        region="Kano",
        lga="Dala",
        settlements=20,
        wards=6,
        planned_wf=180,
        actual_wf=170,
        roles=[role_data],
        budget=400000,
        spent=240000,
        planned_to_date=228000,
        target=200000,
        objective=190000,
        goal_pct=95,
        reached=120000,
        doses=210000,
        doses_used=130000,
        cold_boxes=12,
        vehicles=3,
        status="On track",
        owner="Ngozi Eze",
        updated="Jun 2, 2026",
    )
    assert campaign.microplans.count() == 1
    assert m.roles[0]["roleId"] == "vaccinator"
    assert m.objective == 190000
