"""Map-data endpoint tests (Reporting 'View map')."""
from __future__ import annotations

import pytest
from django.contrib.gis.geos import GEOSGeometry
from django.urls import reverse

from connect_labs.campaign.models import Campaign, Region, SyntheticCommCareDomain, WorkerCase, Workspace
from connect_labs.labs.admin_boundaries.models import AdminBoundary

pytestmark = pytest.mark.django_db


def _box(lon, lat, d=0.3):
    poly = GEOSGeometry(
        f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))",
        srid=4326,
    )
    return GEOSGeometry(f"MULTIPOLYGON({poly.wkt[len('POLYGON'):]})", srid=4326)


@pytest.fixture
def mapped_campaign():
    AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=1,
        name="Kano",
        boundary_id="st-kano",
        geometry=_box(8.5, 12.0),
        source="geopode",
        population=13_000_000,
    )
    SyntheticCommCareDomain.objects.create(domain="campaign-synthetic-map", enabled=True)
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    c = Campaign.objects.create(workspace=ws, name="Map", code="MAP", commcare_domain="campaign-synthetic-map")
    Region.objects.create(campaign=c, region_id="st-kano", name="Kano", lgas=[], order=0)
    for i in range(5):
        WorkerCase.objects.create(
            campaign=c,
            case_id=f"wc-{i}",
            case_type="campaign_worker",
            worker_id=f"W{i}",
            region_id="st-kano",
            lga="L",
            properties={"kyc": "approved", "location": [8.5 + i * 0.01, 12.0]},
        )
    return c


def test_map_data_returns_boundaries_and_points(client, login_as, mapped_campaign):
    login_as(client)
    resp = client.get(reverse("campaign:map_data") + "?campaign=MAP")
    assert resp.status_code == 200
    d = resp.json()
    assert d["boundaries"]["type"] == "FeatureCollection"
    assert len(d["boundaries"]["features"]) == 1
    feat = d["boundaries"]["features"][0]
    assert feat["properties"]["name"] == "Kano"
    assert feat["properties"]["workers"] == 5
    assert feat["properties"]["intensity"] == 1.0  # the only region -> max
    assert len(d["workers"]["features"]) == 5
    assert d["workers"]["features"][0]["properties"]["color"]  # KYC color
    assert d["total_workers"] == 5
