"""Real Nigeria geography for the campaign synthetic data.

Reads the state -> LGA -> ward hierarchy from labs' ``AdminBoundary`` (the
``admin_boundaries`` app, GeoPoDe/WHO source: ~37 states / ~774 LGAs / ~9,300
wards) so synthetic workers get real place names + polygons rather than invented
ones. This is a thin read-only adapter — the boundary data itself is loaded once
via ``manage.py load_geopode_from_drive --iso NGA`` and shared with microplans.

Admin levels: 0=country, 1=state, 2=LGA, 3=ward.
"""
from __future__ import annotations

import random

from django.contrib.gis.geos import Point

from connect_labs.labs.admin_boundaries.models import AdminBoundary

ISO = "NGA"
SOURCE = "geopode"  # GeoPoDe loads the full NGA country/state/LGA/ward hierarchy in one source

LEVEL_STATE = 1
LEVEL_LGA = 2
LEVEL_WARD = 3


class GeographyUnavailable(RuntimeError):
    """Raised when the requested admin boundaries aren't loaded in this DB."""


def is_loaded(iso: str = ISO, source: str = SOURCE) -> bool:
    """True if at least the state level is loaded for this country/source."""
    return AdminBoundary.objects.filter(iso_code=iso, admin_level=LEVEL_STATE, source=source).exists()


def states(iso: str = ISO, source: str = SOURCE) -> list[AdminBoundary]:
    return list(AdminBoundary.objects.filter(iso_code=iso, admin_level=LEVEL_STATE, source=source).order_by("name"))


def _children(parent: AdminBoundary, level: int) -> list[AdminBoundary]:
    return list(
        AdminBoundary.objects.filter(
            iso_code=parent.iso_code,
            admin_level=level,
            parent_boundary_id=parent.boundary_id,
            source=parent.source,
        ).order_by("name")
    )


def lgas(state: AdminBoundary) -> list[AdminBoundary]:
    return _children(state, LEVEL_LGA)


def wards(lga: AdminBoundary) -> list[AdminBoundary]:
    return _children(lga, LEVEL_WARD)


def random_point_in(boundary: AdminBoundary, rng: random.Random) -> Point:
    """A WGS84 Point inside ``boundary``'s polygon (rejection sampling within the
    bounding box, falling back to the centroid). Used to place a worker's GPS so
    the 'overlapping GPS' fraud rule and coverage map have realistic coordinates."""
    geom = boundary.geometry
    minx, miny, maxx, maxy = geom.extent
    for _ in range(100):
        pt = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy), srid=4326)
        if geom.contains(pt):
            return pt
    return geom.centroid
