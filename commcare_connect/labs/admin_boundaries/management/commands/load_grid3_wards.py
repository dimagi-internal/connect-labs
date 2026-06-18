"""Load GRID3 operational ward boundaries (admin level 3) into AdminBoundary.

GRID3 (https://grid3.org) publishes Nigeria's operational ward boundaries. We pull
them from the public GRID3 v1.0 ArcGIS FeatureService (country-wide, ADM3), which
carries ward/LGA/state names + codes. Loaded as source="grid3" so they sit
alongside GeoPoDe wards as a second selectable boundary source.

Each ward stores its parents (state, LGA) in ``extra.parent_names`` so the picker /
inspect panel can show "Ward — State › LGA" (same convention as the GeoPoDe loader).
GRID3 carries no population, so ``population`` stays null.

Usage:
    python manage.py load_grid3_wards            # load all NGA wards (clears existing grid3 NGA first)
    python manage.py load_grid3_wards --dry-run  # report counts only, no writes
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand

from commcare_connect.labs.admin_boundaries.models import AdminBoundary

# GRID3 NGA Operational Wards v1.0 (country-wide ADM3) feature service.
SERVICE = (
    "https://services3.arcgis.com/BU6Aadhn6tbBEdyk/arcgis/rest/services/" "NGA_Ward_Boundaries/FeatureServer/0/query"
)
PAGE = 2000  # service maxRecordCount


def _fetch_page(offset: int) -> dict:
    params = {
        "where": "1=1",
        "outFields": "wardname,wardcode,lganame,lgacode,statename,statecode",
        "outSR": "4326",
        "f": "geojson",
        "resultOffset": offset,
        "resultRecordCount": PAGE,
    }
    url = f"{SERVICE}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 (trusted GRID3 host)
        return json.load(r)


def _to_multipolygon(geom: dict | None):
    if not geom:
        return None
    g = GEOSGeometry(json.dumps(geom))
    if g.srid is None:
        g.srid = 4326
    if g.geom_type == "Polygon":
        g = MultiPolygon(g)
    return g if g.geom_type == "MultiPolygon" else None


class Command(BaseCommand):
    help = "Load GRID3 NGA operational ward boundaries (ADM3) as source='grid3'."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts only; no writes.")

    def handle(self, *args, **opts):
        iso = "NGA"
        self.stdout.write("Fetching GRID3 NGA wards…")
        feats: list[dict] = []
        offset = 0
        while True:
            data = _fetch_page(offset)
            page = data.get("features", []) or []
            feats.extend(page)
            if len(page) < PAGE:
                break
            offset += PAGE
        self.stdout.write(f"Fetched {len(feats)} ward features.")

        rows = []
        skipped = 0
        for f in feats:
            p = f.get("properties", {}) or {}
            name = (p.get("wardname") or "").strip()
            code = (p.get("wardcode") or "").strip()
            geom = _to_multipolygon(f.get("geometry"))
            if not name or not code or geom is None:
                skipped += 1
                continue
            rows.append(
                AdminBoundary(
                    iso_code=iso,
                    admin_level=3,
                    name=name,
                    boundary_id=f"grid3-{code}",
                    geometry=geom,
                    source="grid3",
                    source_url="grid3:NGA_Ward_Boundaries_v1",
                    population=None,
                    parent_boundary_id="",
                    extra={
                        "provider": "GRID3",
                        "level_token": "ward",
                        "own_code": code,
                        "parent_codes": {"lga": p.get("lgacode") or "", "state": p.get("statecode") or ""},
                        "parent_names": {
                            "state": (p.get("statename") or "").strip(),
                            "lga": (p.get("lganame") or "").strip(),
                        },
                    },
                )
            )

        self.stdout.write(f"Valid wards: {len(rows)} (skipped {skipped} with missing name/code/geometry).")
        if opts["dry_run"]:
            self.stdout.write("DRY RUN — no writes.")
            return

        deleted, _ = AdminBoundary.objects.filter(iso_code=iso, source="grid3", admin_level=3).delete()
        AdminBoundary.objects.bulk_create(rows, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(rows)} GRID3 wards (cleared {deleted} existing)."))
