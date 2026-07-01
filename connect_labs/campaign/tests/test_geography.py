"""Geography adapter tests.

The adapter reads the real Nigeria state -> LGA -> ward hierarchy from labs'
AdminBoundary (admin_boundaries app) so the campaign synthetic data uses real
place names + polygons instead of inventing them. These tests build a tiny
GeoPoDe-shaped hierarchy via factory rows (the full NGA set is Drive-loaded, not
in the repo) and pin enumeration + in-polygon GPS placement.
"""
from __future__ import annotations

import random

import pytest
from django.contrib.gis.geos import GEOSGeometry, Point

from connect_labs.campaign.services import geography
from connect_labs.labs.admin_boundaries.models import AdminBoundary

pytestmark = pytest.mark.django_db


def _box(lon, lat, d=0.05):
    poly = GEOSGeometry(
        f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))",
        srid=4326,
    )
    return GEOSGeometry(f"MULTIPOLYGON({poly.wkt[len('POLYGON'):]})", srid=4326)


def _bnd(level, name, bid, parent="", lon=3.4, lat=6.5, pop=10000, source="geopode"):
    return AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=level,
        name=name,
        boundary_id=bid,
        parent_boundary_id=parent,
        geometry=_box(lon, lat),
        source=source,
        population=pop,
    )


@pytest.fixture
def nga_tree():
    """1 country -> 2 states -> 2 LGAs each -> 2 wards each."""
    _bnd(0, "Nigeria", "nga")
    s1 = _bnd(1, "Kano", "st-kano", "nga", pop=13000000)
    s2 = _bnd(1, "Lagos", "st-lagos", "nga", lon=3.3, lat=6.5, pop=12000000)
    for s, base in ((s1, "kano"), (s2, "lagos")):
        for li in range(2):
            lga = _bnd(2, f"{s.name} LGA {li}", f"lga-{base}-{li}", s.boundary_id, pop=500000)
            for wi in range(2):
                _bnd(3, f"{lga.name} Ward {wi}", f"ward-{base}-{li}-{wi}", lga.boundary_id, pop=40000)
    return s1, s2


def test_states_enumerates_adm1(nga_tree):
    states = geography.states()
    assert {s.name for s in states} == {"Kano", "Lagos"}


def test_lgas_under_state(nga_tree):
    s1, _ = nga_tree
    lgas = geography.lgas(s1)
    assert len(lgas) == 2
    assert all(lg.parent_boundary_id == s1.boundary_id for lg in lgas)


def test_wards_under_lga(nga_tree):
    s1, _ = nga_tree
    lga = geography.lgas(s1)[0]
    wards = geography.wards(lga)
    assert len(wards) == 2
    assert all(w.admin_level == 3 for w in wards)


def test_random_point_falls_inside_boundary(nga_tree):
    s1, _ = nga_tree
    ward = geography.wards(geography.lgas(s1)[0])[0]
    rng = random.Random(42)
    pt = geography.random_point_in(ward, rng)
    assert isinstance(pt, Point)
    assert ward.geometry.contains(pt)


def test_is_loaded_reflects_presence(nga_tree):
    assert geography.is_loaded() is True


@pytest.mark.django_db
def test_is_loaded_false_when_absent():
    assert geography.is_loaded() is False
