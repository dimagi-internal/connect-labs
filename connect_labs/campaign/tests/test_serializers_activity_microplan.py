import pytest

from connect_labs.campaign.services import seed, serializers


@pytest.mark.django_db
def test_activities_and_microplans_serialized():
    c = seed.seed_campaign(fresh=True)
    p = serializers.bootstrap_payload(c)
    assert len(p["ACTIVITIES"]) == 6
    a = p["ACTIVITIES"][0]
    for k in (
        "id",
        "name",
        "donor",
        "status",
        "start",
        "end",
        "requests",
        "workers",
        "region",
        "target",
        "reached",
        "synced",
    ):
        assert k in a
    assert len(p["MICROPLANS"]) == 18
    m = next(x for x in p["MICROPLANS"])
    for k in (
        "id",
        "regionId",
        "region",
        "lga",
        "settlements",
        "wards",
        "plannedWf",
        "actualWf",
        "roles",
        "budget",
        "spent",
        "plannedToDate",
        "target",
        "objective",
        "goalPct",
        "reached",
        "doses",
        "dosesUsed",
        "coldBoxes",
        "vehicles",
        "status",
        "owner",
        "updated",
    ):
        assert k in m
    assert m["roles"][0]["roleId"]
