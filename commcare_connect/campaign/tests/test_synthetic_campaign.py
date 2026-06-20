"""Full synthetic-campaign pipeline tests.

build_synthetic_campaign() assembles a coherent campaign from CommCare-shaped
worker cases (a registered synthetic CommCare project space) + real AdminBoundary
geography. The tool reads workers via the Case API (CommCareProvider), so
bootstrap_payload renders them without any local Worker ORM copy. These tests run
at small scale against a factory-built NGA hierarchy.
"""
from __future__ import annotations

import pytest
from django.contrib.gis.geos import GEOSGeometry

from commcare_connect.campaign.models import Microplan, Region, SyntheticCommCareDomain, Worker, WorkerCase
from commcare_connect.campaign.services import serializers, synthetic_campaign
from commcare_connect.labs.admin_boundaries.models import AdminBoundary

pytestmark = pytest.mark.django_db


def _box(lon, lat, d=0.05):
    poly = GEOSGeometry(
        f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))",
        srid=4326,
    )
    return GEOSGeometry(f"MULTIPOLYGON({poly.wkt[len('POLYGON'):]})", srid=4326)


def _bnd(level, name, bid, parent="", lon=3.4, pop=10000):
    return AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=level,
        name=name,
        boundary_id=bid,
        parent_boundary_id=parent,
        geometry=_box(lon, 6.5),
        source="geopode",
        population=pop,
    )


@pytest.fixture
def nga_geo():
    _bnd(0, "Nigeria", "nga")
    for si, sname in enumerate(["Kano", "Lagos", "Kaduna"]):
        s = _bnd(1, sname, f"st-{si}", "nga", lon=3.4 + si, pop=8_000_000)
        for li in range(3):
            lga = _bnd(2, f"{sname} LGA {li}", f"lga-{si}-{li}", s.boundary_id, lon=3.4 + si, pop=300_000)
            for wi in range(3):
                _bnd(3, f"{sname} Ward {li}-{wi}", f"ward-{si}-{li}-{wi}", lga.boundary_id, lon=3.4 + si, pop=20_000)


def test_builds_coherent_campaign_from_real_geography(nga_geo):
    c = synthetic_campaign.build_synthetic_campaign(worker_count=300, seed_value=11)
    # workers are CommCare cases (no local Worker ORM copy on this path)
    assert WorkerCase.objects.filter(campaign=c).count() == 300
    assert Worker.objects.filter(campaign=c).count() == 0
    # the campaign is bound to a registered synthetic CommCare project space
    assert c.commcare_domain
    assert SyntheticCommCareDomain.objects.filter(domain=c.commcare_domain, enabled=True).exists()
    # regions are the real states (region_id == AdminBoundary boundary_id)
    region_ids = set(Region.objects.filter(campaign=c).values_list("region_id", flat=True))
    assert region_ids == set(AdminBoundary.objects.filter(admin_level=1).values_list("boundary_id", flat=True))
    # every worker case's region_id resolves to a real region
    assert set(WorkerCase.objects.filter(campaign=c).values_list("region_id", flat=True)) <= region_ids
    # microplans exist per populated LGA
    assert Microplan.objects.filter(campaign=c).count() >= 1


def test_bootstrap_payload_renders_the_national_campaign(nga_geo):
    c = synthetic_campaign.build_synthetic_campaign(worker_count=200, seed_value=12)
    payload = serializers.bootstrap_payload(c)
    # WORKERS are read via the CommCare Case API (from WorkerCase), not the Worker ORM
    assert len(payload["WORKERS"]) == 200
    assert len(payload["REGIONS"]) == 3
    assert payload["CAMPAIGN"]["name"].startswith("Measles")
    # worker region names resolve (not blank) — proves region_id join works
    assert all(w["region"] for w in payload["WORKERS"])
    assert len(payload["MICROPLANS"]) >= 1
    assert len(payload["REPORT_DAYS"]) == 16
    assert payload["HOUSEHOLDS"]["registered"] > 0


def test_rebuild_is_idempotent_by_code(nga_geo):
    synthetic_campaign.build_synthetic_campaign(worker_count=50, seed_value=1)
    synthetic_campaign.build_synthetic_campaign(worker_count=50, seed_value=1)
    from commcare_connect.campaign.models import Campaign

    assert Campaign.objects.filter(code=synthetic_campaign.DEFAULT_CODE).count() == 1
    assert WorkerCase.objects.count() == 50  # replaced, not duplicated


def test_raises_without_geography():
    with pytest.raises(synthetic_campaign.geography.GeographyUnavailable):
        synthetic_campaign.build_synthetic_campaign(worker_count=10)
