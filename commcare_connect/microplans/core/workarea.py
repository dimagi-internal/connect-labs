"""Map sampled rooftop pins → Connect microplanning WorkAreas.

Building-as-WorkArea (per the design decision): each sampled pin becomes one
tiny WorkArea — centroid = the pin, boundary = the building's real footprint
polygon (lightly buffered by a small doorstep margin), building_count = 1,
expected_visit_count = 1. When a pin carries no footprint we fall back to a
small square around the centroid. This preserves rooftop's exact-building
pinning inside Connect's first-class microplanning feature (assignment, mobile
delivery, visit tracking, the inaccessibility/substitution flow) without
inventing a parallel mechanism.

`sample_type` (primary/alternate), `cluster`, and `order_in_cluster` ride in
`case_properties` so the FLW app and dashboards can distinguish targets from
their ranked substitutes. The study `arm` (intervention/comparison) is a LABS-SIDE
field, deliberately kept OUT of `case_properties` — arm assignment must stay
blind to the LLO/FLWs (a shared two-arm plan that reveals which side is which is
an anti-pattern), so it never enters the Connect-facing bucket.

Each work area's labs-side `properties` key-value bag (built by
`plan._sampling_properties` / `_coverage_properties`, schema below) is stored on
the plan/LabsRecord and is what becomes the Connect WorkArea `case_properties`
once the API accepts them. ``WorkAreaProperties`` documents the recognised keys.

Two output shapes:
  * `to_api_payload` — for the (proposed) `POST /export/opportunity/<id>/work_areas/`
    endpoint, keyed by WorkArea model fields.
  * `to_csv_rows` — for Connect's existing web CSV importer, keyed by its column
    labels (see microplanning/tasks.py WorkAreaCSVImporter.HEADERS).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TypedDict

from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform

from commcare_connect.microplans.core.geo import utm_epsg_for


class WorkAreaProperties(TypedDict, total=False):
    """The recognised keys in a work area's labs-side ``properties`` bag.

    It is a key-value store (stored on the plan/LabsRecord, serialised with the
    work area) — typed here so producers don't sling magic strings, but stored and
    sent as a plain dict. This bag is what becomes the Connect WorkArea
    ``case_properties`` once that API exists; it grows as we add analysis
    dimensions. (``arm`` is intentionally NOT here — it stays labs-side first-class.)
    """

    sample_type: str  # sampling: "primary" (a unit to survey) | "alternate" (ranked backup)
    cluster: str  # the cluster the unit belongs to (sampling: the PSU; coverage: the grid cluster)
    order_in_cluster: int  # sampling: rank within the PSU (1 = first)
    stratum: str  # sampling: PSU size stratum
    weight: float  # sampling: inclusion weight (primaries only)
    cell_size_m: float  # coverage: grid cell edge in metres


# Connect's WorkAreaCSVImporter column labels.
#
# ⚠ NIGERIA-HARDCODED VOCABULARY. "Ward" / "LGA" / "State" are Nigeria's
# administrative tiers (ADM3 / ADM2 / ADM1). They are hardcoded HERE only to
# mirror Connect's importer, which hardcodes these exact column names
# (dimagi/commcare-connect microplanning/tasks.py WorkAreaCSVImporter.HEADERS).
# Labs itself is already country-generic — the admin-boundary resolver speaks
# canonical levels (1=region/state, 2=county/district/LGA, 3=locality/ward; see
# core/admin_boundaries.py), so a Kenyan "County" or Indian "District" is the
# same canonical level 2 we shove into the "LGA" column on export.
#
# TODO(generalize): when Connect generalizes its work-area importer to canonical
# admin levels (or per-country vocabulary), generalize this mapping and the
# plan's lga/state fields (core/plan.derive_lga_state, models.PlanRecord) to
# match — drop the Nigeria-specific column names. Until then a non-Nigeria plan
# still exports under "LGA"/"State" headers (the values are just labels; Connect
# only checks them non-empty, so it imports, but the column names lie).
CSV_HEADERS = {
    "slug": "Area Slug",
    "ward": "Ward",
    "centroid": "Centroid",
    "boundary": "Boundary",
    "building_count": "Building Count",
    "expected_visit_count": "Expected Visit Count",
    "target_population": "Target Population",
    "lga": "LGA",  # Nigeria ADM2 — see note above
    "state": "State",  # Nigeria ADM1 — see note above
}


@dataclass
class WorkAreaPayload:
    slug: str
    ward: str
    centroid_lon: float
    centroid_lat: float
    boundary_wkt: str
    building_count: int = 1
    expected_visit_count: int = 1
    target_population: int = 0
    # Study arm (intervention/comparison) is LABS-SIDE only — deliberately NOT in
    # case_properties (the Connect/FLW-facing bucket) so the shared plan stays blind.
    arm: str = "intervention"
    case_properties: dict = field(default_factory=dict)


def _square_boundary_shape(lon: float, lat: float, half_m: float):
    """A small axis-aligned square (in meters) centered on the pin, as a WGS84 shapely Polygon."""
    epsg = utm_epsg_for(lon, lat)
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = Transformer.from_crs(epsg, 4326, always_xy=True)
    x, y = fwd.transform(lon, lat)
    square_m = box(x - half_m, y - half_m, x + half_m, y + half_m)
    return transform(lambda xs, ys, z=None: inv.transform(xs, ys), square_m)


def _square_boundary_wkt(lon: float, lat: float, half_m: float) -> str:
    return _square_boundary_shape(lon, lat, half_m).wkt


def footprint_boundary_shape(geom_json, lon: float, lat: float, buffer_m: float, fallback_half_m: float):
    """The building's real footprint polygon (lightly buffered, in meters), as a WGS84 shapely geom.

    This is the WorkArea boundary an FLW actually receives — the outline of the
    sampled building plus a small doorstep margin, not a generic box. Falls back to
    a small square around the centroid when the pin carries no footprint (legacy
    pins, or a source row whose polygon wasn't cached).
    """
    if not geom_json:
        return _square_boundary_shape(lon, lat, fallback_half_m)
    try:
        raw = geom_json if isinstance(geom_json, dict) else json.loads(geom_json)
        g = shape(raw)
        if g.is_empty:
            return _square_boundary_shape(lon, lat, fallback_half_m)
        if buffer_m and buffer_m > 0:
            epsg = utm_epsg_for(lon, lat)
            fwd = Transformer.from_crs(4326, epsg, always_xy=True)
            inv = Transformer.from_crs(epsg, 4326, always_xy=True)
            g_m = transform(lambda xs, ys, z=None: fwd.transform(xs, ys), g)
            g = transform(lambda xs, ys, z=None: inv.transform(xs, ys), g_m.buffer(buffer_m))
        return g
    except (ValueError, TypeError, json.JSONDecodeError):
        return _square_boundary_shape(lon, lat, fallback_half_m)


def _footprint_boundary_wkt(geom_json, lon: float, lat: float, buffer_m: float, fallback_half_m: float) -> str:
    return footprint_boundary_shape(geom_json, lon, lat, buffer_m, fallback_half_m).wkt


def build_work_areas(
    pins_geojson: dict,
    *,
    ward_for_arm: dict | None = None,
    lga: str = "",
    state: str = "",
    boundary_half_m: float = 8.0,
    boundary_buffer_m: float = 3.0,
) -> list[WorkAreaPayload]:
    """Convert a pins FeatureCollection (from sampling.frame) into WorkArea payloads."""
    ward_for_arm = ward_for_arm or {}
    out: list[WorkAreaPayload] = []
    for feat in pins_geojson.get("features", []):
        lon, lat = feat["geometry"]["coordinates"]
        props = feat.get("properties", {})
        arm = props.get("arm", "intervention")
        cluster = props.get("cluster", "C0")
        sample_type = props.get("sample_type", "primary")
        order = int(props.get("order_in_cluster", 0))
        slug = f"{arm[:3]}-{cluster}-{sample_type[:4]}-{order}".lower()
        out.append(
            WorkAreaPayload(
                slug=slug,
                ward=ward_for_arm.get(arm, arm),
                centroid_lon=lon,
                centroid_lat=lat,
                boundary_wkt=_footprint_boundary_wkt(
                    props.get("geom_json"), lon, lat, boundary_buffer_m, boundary_half_m
                ),
                building_count=1,
                expected_visit_count=1,
                target_population=0,
                arm=arm,  # labs-side only — never pushed to Connect
                case_properties={
                    "sample_type": sample_type,
                    "cluster": cluster,
                    "order_in_cluster": order,
                    "lga": lga,
                    "state": state,
                },
            )
        )
    return out


def build_coverage_work_areas(
    area_features: dict,
    *,
    ward_for_arm: dict | None = None,
    lga: str = "",
    state: str = "",
) -> list[WorkAreaPayload]:
    """Grid-cell-as-WorkArea (coverage mode): each occupied grid cell → one WorkArea.

    Unlike sampling (one tiny WorkArea per pinned building), coverage assigns a
    whole grid cell to an FLW: boundary = the cell box itself,
    expected_visit_count = building_count (visit every household in the cell).
    """
    ward_for_arm = ward_for_arm or {}
    out: list[WorkAreaPayload] = []
    for feat in area_features.get("features", []):
        geom = shape(feat["geometry"])
        centroid = geom.centroid
        props = feat.get("properties", {})
        # Coverage has no intervention/comparison split — default to "coverage".
        # (Legacy snapshots may still carry "intervention" from the hull era.)
        arm = props.get("arm", "coverage")
        cluster = props.get("cluster", "C0")
        building_count = int(props.get("building_count", 0))
        out.append(
            WorkAreaPayload(
                slug=f"{arm[:3]}-{cluster}".lower(),
                ward=ward_for_arm.get(arm, arm),
                centroid_lon=float(centroid.x),
                centroid_lat=float(centroid.y),
                boundary_wkt=geom.wkt,
                building_count=building_count,
                expected_visit_count=building_count,  # coverage: visit every household
                target_population=0,
                case_properties={
                    "cluster": cluster,
                    "arm": arm,
                    "mode": "coverage",
                    "lga": lga,
                    "state": state,
                },
            )
        )
    return out


def to_api_payload(payloads: list[WorkAreaPayload]) -> list[dict]:
    """Shape for the proposed POST /work_areas/ endpoint (keyed by model fields)."""
    return [
        {
            "slug": p.slug,
            "ward": p.ward,
            "centroid": {
                "type": "Point",
                "coordinates": [p.centroid_lon, p.centroid_lat],
            },
            "boundary_wkt": p.boundary_wkt,
            "building_count": p.building_count,
            "expected_visit_count": p.expected_visit_count,
            "target_population": p.target_population,
            "case_properties": p.case_properties,
        }
        for p in payloads
    ]


def to_csv_rows(payloads: list[WorkAreaPayload]) -> list[dict]:
    """Shape for Connect's existing web CSV importer (keyed by column labels).

    Connect REQUIRES LGA + State non-empty on every row, or it rejects the whole
    file (full contract: ``microplans/CONNECT_IMPORT_CONTRACT.md``). They come from
    each payload's ``case_properties`` — populate them upstream (see
    ``plan.derive_lga_state`` / ``ProgramPlanCSVView``) so the export is importable."""
    h = CSV_HEADERS
    return [
        {
            h["slug"]: p.slug,
            h["ward"]: p.ward,
            h["centroid"]: f"{p.centroid_lon} {p.centroid_lat}",
            h["boundary"]: p.boundary_wkt,
            h["building_count"]: p.building_count,
            h["expected_visit_count"]: p.expected_visit_count,
            h["target_population"]: p.target_population,
            h["lga"]: p.case_properties.get("lga", ""),
            h["state"]: p.case_properties.get("state", ""),
        }
        for p in payloads
    ]
