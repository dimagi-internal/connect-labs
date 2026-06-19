"""Worker-case generator tests.

generate_worker_cases() produces synthetic CommCare *cases* (case_type
campaign_worker) whose properties carry the full Worker + KYC-Verification field
set the Data Model marks CommCare-owned, with geography sourced from real
AdminBoundary rows. These tests pin: count + case shape, required property keys,
the amount = days*rate invariant, valid geography refs + in-ward GPS, fraud
clusters, and determinism.
"""
from __future__ import annotations

import pytest
from django.contrib.gis.geos import GEOSGeometry, Point

from commcare_connect.campaign.models import WorkerCase
from commcare_connect.campaign.services import worker_cases
from commcare_connect.campaign.tests.factories import CampaignFactory
from commcare_connect.labs.admin_boundaries.models import AdminBoundary

pytestmark = pytest.mark.django_db

# The exact case-property keys the serializer (_worker) consumes.
REQUIRED_KEYS = {
    "worker_id",
    "first",
    "last",
    "name",
    "gender",
    "phone",
    "region_id",
    "lga",
    "role_id",
    "rate",
    "days_worked",
    "days_approved",
    "amount",
    "kyc",
    "pay",
    "bank",
    "acct",
    "nin",
    "passport",
    "enrolled",
    "attendance",
    "prior_campaigns",
    "duplicate",
    "dup_with",
    "fraud_rules",
    "linked",
    "investigation",
    "documents",
    "location",
}


def _box(lon, lat, d=0.05):
    poly = GEOSGeometry(
        f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))",
        srid=4326,
    )
    return GEOSGeometry(f"MULTIPOLYGON({poly.wkt[len('POLYGON'):]})", srid=4326)


def _bnd(level, name, bid, parent="", lon=3.4, lat=6.5, pop=10000):
    return AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=level,
        name=name,
        boundary_id=bid,
        parent_boundary_id=parent,
        geometry=_box(lon, lat),
        source="geopode",
        population=pop,
    )


@pytest.fixture
def nga_geo():
    _bnd(0, "Nigeria", "nga")
    for si, sname in enumerate(["Kano", "Lagos", "Kaduna"]):
        s = _bnd(1, sname, f"st-{si}", "nga", lon=3.4 + si, pop=10_000_000)
        for li in range(3):
            lga = _bnd(2, f"{sname} LGA {li}", f"lga-{si}-{li}", s.boundary_id, lon=3.4 + si, pop=400_000)
            for wi in range(3):
                _bnd(3, f"{sname} Ward {li}-{wi}", f"ward-{si}-{li}-{wi}", lga.boundary_id, lon=3.4 + si, pop=30_000)


def test_generates_requested_count_as_cases(nga_geo):
    c = CampaignFactory()
    cases = worker_cases.generate_worker_cases(c, count=120, seed=1)
    assert len(cases) == 120
    assert WorkerCase.objects.filter(campaign=c).count() == 120
    assert all(wc.case_type == "campaign_worker" for wc in cases)
    assert len({wc.case_id for wc in cases}) == 120  # unique case ids


def test_case_properties_have_full_field_set(nga_geo):
    c = CampaignFactory()
    worker_cases.generate_worker_cases(c, count=30, seed=2)
    wc = WorkerCase.objects.filter(campaign=c).first()
    assert REQUIRED_KEYS <= set(wc.properties.keys())
    p = wc.properties
    assert p["amount"] == p["days_worked"] * p["rate"]  # invariant
    assert p["kyc"] in {"approved", "pending", "review", "rejected"}
    assert p["pay"] in {"paid", "approved", "pending", "rejected", "hold"}


def test_geography_refs_real_boundaries_and_gps_in_ward(nga_geo):
    c = CampaignFactory()
    worker_cases.generate_worker_cases(c, count=50, seed=3)
    state_ids = set(AdminBoundary.objects.filter(admin_level=1).values_list("boundary_id", flat=True))
    for wc in WorkerCase.objects.filter(campaign=c):
        assert wc.region_id in state_ids
        lon, lat = wc.properties["location"]
        ward = AdminBoundary.objects.get(admin_level=3, name=wc.ward)
        assert ward.geometry.contains(Point(lon, lat, srid=4326))


def test_injects_fraud_clusters(nga_geo):
    c = CampaignFactory()
    worker_cases.generate_worker_cases(c, count=200, seed=4)
    flagged = [wc for wc in WorkerCase.objects.filter(campaign=c) if wc.properties["duplicate"]]
    assert len(flagged) >= 2
    for wc in flagged:
        assert wc.properties["dup_with"]
        assert wc.properties["fraud_rules"]


def test_deterministic_for_same_seed(nga_geo):
    c1 = CampaignFactory()
    c2 = CampaignFactory()
    a = worker_cases.generate_worker_cases(c1, count=40, seed=7)
    b = worker_cases.generate_worker_cases(c2, count=40, seed=7)
    assert [x.properties["name"] for x in a] == [y.properties["name"] for y in b]
    assert [x.properties["kyc"] for x in a] == [y.properties["kyc"] for y in b]


def test_raises_when_geography_absent():
    c = CampaignFactory()
    with pytest.raises(worker_cases.GeographyUnavailable):
        worker_cases.generate_worker_cases(c, count=10, seed=1)
