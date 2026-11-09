"""Load an OCHA COD-AB dataset from HDX (humanitarian data exchange) as the ``hdx`` boundary source.

COD-AB ("common operational dataset, administrative boundaries") is the authoritative subnational set a
country's OCHA office publishes with the national statistics office. The dataset page lists a zip of one GeoJSON
per level (``<iso3>_admin1.geojson`` ...); this reads the zip's download URL from HDX's own dataset API (so no
resource URL is written down here), upserts levels 1 to 3 as ``source="hdx"`` rows with their place codes and parents,
and leaves every other source's rows alone (indicator values point at them).

Niger is the first: ``load_hdx_cod NER cod-ab-ner`` gives 8 regions, 67 departments and 266 communes, where the
geoBoundaries layer labs held merged Tahoua with Agadez and Zinder with Diffa (6 regions).

Usage:
    python manage.py load_hdx_cod NER cod-ab-ner
    python manage.py load_hdx_cod NER cod-ab-ner --dry-run
"""

from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import urlparse

import httpx
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.admin_boundaries.models import AdminBoundary

HDX_API = "https://data.humdata.org/api/3/action/package_show"
LEVELS = (1, 2, 3)
PROVIDER = "OCHA / national statistics office (COD-AB)"


def zip_url(package: dict) -> str:
    """The GeoJSON zip's download URL from an HDX dataset record; only HDX's own host is followed."""
    for resource in package.get("resources", []):
        if str(resource.get("name", "")).lower().endswith("geojson.zip"):
            url = resource.get("url", "")
            if urlparse(url).hostname == "data.humdata.org":
                return url
    raise CommandError("the dataset has no GeoJSON zip resource on data.humdata.org")


def _multi(geometry: dict) -> MultiPolygon | None:
    geom = GEOSGeometry(json.dumps(geometry))
    if isinstance(geom, Polygon):
        return MultiPolygon(geom)
    return geom if isinstance(geom, MultiPolygon) else None


def build_rows(zip_bytes: bytes, iso3: str, source_url: str, dataset: str) -> list[AdminBoundary]:
    """``AdminBoundary`` rows (unsaved) for levels 1 to 3 of a COD-AB zip."""
    prefix = iso3.lower()
    rows: list[AdminBoundary] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for level in LEVELS:
            name = f"{prefix}_admin{level}.geojson"
            if name not in zf.namelist():
                raise CommandError(f"{name} is not in the zip")
            for feature in json.loads(zf.read(name)).get("features", []):
                props, geom = feature.get("properties") or {}, _multi(feature.get("geometry") or {})
                pcode, label = props.get(f"adm{level}_pcode"), props.get(f"adm{level}_name")
                if geom is None or not pcode or not label:
                    continue
                parents = {}
                if level >= 2:
                    parents["state"] = props.get("adm1_name") or ""
                if level >= 3:
                    parents["lga"] = props.get("adm2_name") or ""
                rows.append(
                    AdminBoundary(
                        iso_code=iso3,
                        admin_level=level,
                        name=label,
                        boundary_id=f"hdx-{pcode}",
                        parent_boundary_id=f"hdx-{props[f'adm{level - 1}_pcode']}" if level > 1 else "",
                        geometry=geom,
                        source=AdminBoundary.Source.HDX,
                        source_url=source_url,
                        extra={
                            "provider": PROVIDER,
                            "dataset": dataset,
                            "place_code": pcode,
                            "parent_names": parents,
                            "version": props.get("version"),
                            "valid_on": str(props.get("valid_on") or ""),
                        },
                    )
                )
    return rows


class Command(BaseCommand):
    help = "Load an OCHA COD-AB dataset from HDX as the 'hdx' admin-boundary source (levels 1 to 3)."

    def add_arguments(self, parser):
        parser.add_argument("iso_code", help="ISO 3166-1 alpha-3 code, e.g. NER")
        parser.add_argument("dataset", help="HDX dataset id, e.g. cod-ab-ner")
        parser.add_argument("--dry-run", action="store_true", help="Report what would be loaded; write nothing")

    def handle(self, *args, **opts):
        iso3, dataset = opts["iso_code"].upper(), opts["dataset"]
        with httpx.Client(follow_redirects=True, timeout=120) as http:
            package = http.get(HDX_API, params={"id": dataset}).raise_for_status().json()["result"]
            url = zip_url(package)
            rows = build_rows(http.get(url).raise_for_status().content, iso3, url, dataset)
        counts = {lv: sum(1 for r in rows if r.admin_level == lv) for lv in LEVELS}
        self.stdout.write(f"{iso3} {package.get('title', dataset)}: {counts}")
        if opts["dry_run"]:
            return
        made = updated = 0
        for row in rows:
            _, created = AdminBoundary.objects.update_or_create(
                source=row.source,
                boundary_id=row.boundary_id,
                defaults={
                    "iso_code": row.iso_code,
                    "admin_level": row.admin_level,
                    "name": row.name,
                    "geometry": row.geometry,
                    "source_url": row.source_url,
                    "parent_boundary_id": row.parent_boundary_id,
                    "extra": row.extra,
                },
            )
            made, updated = made + created, updated + (not created)
        self.stdout.write(f"created {made}, updated {updated}")
