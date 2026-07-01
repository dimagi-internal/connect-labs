"""Hermetic tests for the tightened GeoPoDe loader.

Builds a tiny in-memory GeoPoDe-format ZIP (EPSG:3857, denormalized hierarchy,
population_1, multi-word leaf level) so we exercise the real loader without
depending on the Drive fixtures. Mirrors the schema observed across the actual
country ZIPs (see geopode_sources.json).
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.contrib.gis.geos import GEOSGeometry

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.admin_boundaries.services import GeoPoDELoader

pytestmark = pytest.mark.django_db


def _mercator_multipolygon(lon, lat, d=0.05):
    """A small WGS84 box around (lon, lat), expressed in EPSG:3857 coordinates —
    so the loader has to detect 3857 and reproject back to 4326."""
    box = GEOSGeometry(
        f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))",
        srid=4326,
    )
    box.transform(3857)
    ring = list(box.coords[0])
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def _feature(geom, props):
    return {"type": "Feature", "geometry": geom, "properties": props}


_CRS = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}}
_BASE = {"country_name": "Testland", "country_code": "T1", "source": "WHO", "source_date": "2025-01-01"}


def _zip_bytes():
    """country / regions / sub_districts (multi-word leaf), EPSG:3857."""
    files = {
        "boundary_country_default.json": [
            _feature(_mercator_multipolygon(3.4, 6.5, 0.4), {**_BASE, "global_id": "c-1", "population_1": 1_000_000}),
        ],
        "boundary_regions_default.json": [
            _feature(
                _mercator_multipolygon(3.4, 6.6, 0.2),
                {**_BASE, "global_id": "r-1", "population_1": 600_000, "regions_name": "North", "regions_code": "R1"},
            ),
        ],
        "boundary_sub_districts_default.json": [
            _feature(
                _mercator_multipolygon(3.4, 6.55),
                {
                    **_BASE,
                    "global_id": "sd-1",
                    "population_1": 50_000,
                    "regions_name": "North",
                    "regions_code": "R1",
                    "sub_districts_name": "Alpha",
                    "sub_districts_code": "SD1",
                },
            ),
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, feats in files.items():
            zf.writestr(name, json.dumps({"type": "FeatureCollection", "crs": _CRS, "features": feats}))
    return buf.getvalue()


class TestGeoPoDeLoaderTightened:
    def test_full_load_captures_population_hierarchy_and_reprojects(self):
        res = GeoPoDELoader().load_from_zip(io.BytesIO(_zip_bytes()), clear=True, iso_override="TST")
        assert res.iso_code == "TST"
        qs = AdminBoundary.objects.filter(iso_code="TST", source="geopode")
        assert qs.count() == 3

        country = qs.get(admin_level=0)
        region = qs.get(admin_level=1)
        sub = qs.get(admin_level=2)

        # Multi-word leaf name is the unit's own, not the parent's.
        assert region.name == "North"
        assert sub.name == "Alpha"  # NOT "North"

        # Population captured from population_1 at every level.
        assert country.population == 1_000_000
        assert region.population == 600_000
        assert sub.population == 50_000

        # Parent linkage by denormalized code: sub -> region -> country.
        assert sub.parent_boundary_id == region.boundary_id == "geopode-r-1"
        assert region.parent_boundary_id == country.boundary_id == "geopode-c-1"

        # Provider + provenance kept in extra.
        assert sub.extra["provider"] == "WHO"
        assert sub.extra["source_date"] == "2025-01-01"
        assert sub.extra["level_token"] == "sub_districts"
        assert sub.extra["parent_codes"]["regions"] == "R1"

        # EPSG:3857 was detected and reprojected back to WGS84 (centroid near 3.4, 6.55).
        c = sub.geometry.centroid
        assert 3.0 < c.x < 3.8 and 6.2 < c.y < 6.9

    def test_idempotent_reload(self):
        loader = GeoPoDELoader()
        loader.load_from_zip(io.BytesIO(_zip_bytes()), clear=True, iso_override="TST")
        loader.load_from_zip(io.BytesIO(_zip_bytes()), clear=True, iso_override="TST")  # no duplicate-key error
        assert AdminBoundary.objects.filter(iso_code="TST", source="geopode").count() == 3

    def test_iso_override_wins_over_filename(self):
        # filename says NGA, override says COD (the DRC case)
        GeoPoDELoader().load_from_zip(
            io.BytesIO(_zip_bytes()), clear=True, filename="GeoPoDe_NGA_Geometry.zip", iso_override="COD"
        )
        assert AdminBoundary.objects.filter(iso_code="COD", source="geopode").count() == 3
        assert not AdminBoundary.objects.filter(iso_code="NGA", source="geopode").exists()
