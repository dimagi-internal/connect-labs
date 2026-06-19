import pytest

from commcare_connect.campaign.services import seed, serializers


@pytest.mark.django_db
def test_bootstrap_payload_shape():
    c = seed.seed_campaign(fresh=True)
    p = serializers.bootstrap_payload(c)

    assert set(p.keys()) == {
        "CAMPAIGN",
        "DONORS",
        "REGIONS",
        "ROLES",
        "ACTIVITIES",
        "PLANNING",
        "MICROPLANS",
        "REPORT_DAYS",
        "HOUSEHOLDS",
        "WORKERS",
        "KYC_STATES",
        "PAY_STATES",
        "sharedLabel",
    }
    assert p["CAMPAIGN"]["name"] == "Measles–Rubella Vaccination Campaign"
    assert p["CAMPAIGN"]["daysElapsed"] == 16 and p["CAMPAIGN"]["targetPop"] == 4280000
    assert p["DONORS"][0]["short"] == "Gavi" and p["DONORS"][0]["committed"] == 2400000
    assert p["REGIONS"][0]["lgas"] == ["Dala", "Fagge", "Gwale", "Nassarawa", "Tarauni"]
    assert p["KYC_STATES"] == ["approved", "pending", "rejected", "review"]
    assert p["PAY_STATES"] == ["paid", "approved", "pending", "rejected", "hold"]
    assert p["sharedLabel"]["nin"] == "National ID (NIN)"
    assert len(p["ACTIVITIES"]) == 6 and len(p["MICROPLANS"]) == 18 and len(p["REPORT_DAYS"]) == 16
    # PLANNING: lgas is a COUNT, derived metrics present
    plan0 = next(x for x in p["PLANNING"] if x["id"] == "kano")
    assert plan0["lgas"] == 5 and plan0["plannedWf"] == 820 and plan0["vaccineAlloc"] == 980000
    # WORKERS shape
    assert len(p["WORKERS"]) == 64
    w = p["WORKERS"][0]
    for k in (
        "id",
        "name",
        "gender",
        "regionId",
        "roleId",
        "daysWorked",
        "amount",
        "kyc",
        "pay",
        "fraudRules",
        "linked",
        "documents",
        "priorCampaigns",
    ):
        assert k in w
    assert p["HOUSEHOLDS"]["membersReached"] == 1386000
    assert p["HOUSEHOLDS"]["coverage"][0]["name"] == "Kano"


@pytest.mark.django_db
def test_workers_have_role_and_region_display_names():
    c = seed.seed_campaign(fresh=True)
    p = serializers.bootstrap_payload(c)
    role_names = {r.role_id: r.name for r in c.worker_roles.all()}
    region_names = {r.region_id: r.name for r in c.regions.all()}
    for w in p["WORKERS"]:
        assert w["role"] and w["role"] == role_names[w["roleId"]]
        assert w["region"] and w["region"] == region_names[w["regionId"]]
