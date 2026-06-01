"""Map sampled rooftop pins → Connect microplanning WorkAreas.

Building-as-WorkArea (per the design decision): each sampled pin becomes one
tiny WorkArea — centroid = the pin, boundary = a small square around it,
building_count = 1, expected_visit_count = 1. This preserves rooftop's
exact-building pinning inside Connect's first-class microplanning feature
(assignment, mobile delivery, visit tracking, the inaccessibility/substitution
flow) without inventing a parallel mechanism.

`role` (primary/alternate), `cluster`, and `order_in_cluster` ride in
`case_properties` so the FLW app and dashboards can distinguish targets from
their 15m substitutes. The study `arm` (intervention/comparison) is a LABS-SIDE
field, deliberately kept OUT of `case_properties` — arm assignment must stay
blind to the LLO/FLWs (a shared two-arm plan that reveals which side is which is
an anti-pattern), so it never enters the Connect-facing bucket.

Two output shapes:
  * `to_api_payload` — for the (proposed) `POST /export/opportunity/<id>/work_areas/`
    endpoint, keyed by WorkArea model fields.
  * `to_csv_rows` — for Connect's existing web CSV importer, keyed by its column
    labels (see microplanning/tasks.py WorkAreaCSVImporter.HEADERS).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform

from commcare_connect.microplans.core.geo import utm_epsg_for

# Connect's WorkAreaCSVImporter column labels.
CSV_HEADERS = {
    "slug": "Area Slug",
    "ward": "Ward",
    "centroid": "Centroid",
    "boundary": "Boundary",
    "building_count": "Building Count",
    "expected_visit_count": "Expected Visit Count",
    "target_population": "Target Population",
    "lga": "LGA",
    "state": "State",
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


def _square_boundary_wkt(lon: float, lat: float, half_m: float) -> str:
    """A small axis-aligned square (in meters) centered on the pin, as WGS84 WKT."""
    epsg = utm_epsg_for(lon, lat)
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = Transformer.from_crs(epsg, 4326, always_xy=True)
    x, y = fwd.transform(lon, lat)
    square_m = box(x - half_m, y - half_m, x + half_m, y + half_m)
    square_wgs = transform(lambda xs, ys, z=None: inv.transform(xs, ys), square_m)
    return square_wgs.wkt


def build_work_areas(
    pins_geojson: dict,
    *,
    ward_for_arm: dict | None = None,
    lga: str = "",
    state: str = "",
    boundary_half_m: float = 8.0,
) -> list[WorkAreaPayload]:
    """Convert a pins FeatureCollection (from sampling.frame) into WorkArea payloads."""
    ward_for_arm = ward_for_arm or {}
    out: list[WorkAreaPayload] = []
    for feat in pins_geojson.get("features", []):
        lon, lat = feat["geometry"]["coordinates"]
        props = feat.get("properties", {})
        arm = props.get("arm", "intervention")
        cluster = props.get("cluster", "C0")
        role = props.get("role", "primary")
        order = int(props.get("order_in_cluster", 0))
        slug = f"{arm[:3]}-{cluster}-{role[:4]}-{order}".lower()
        out.append(
            WorkAreaPayload(
                slug=slug,
                ward=ward_for_arm.get(arm, arm),
                centroid_lon=lon,
                centroid_lat=lat,
                boundary_wkt=_square_boundary_wkt(lon, lat, boundary_half_m),
                building_count=1,
                expected_visit_count=1,
                target_population=0,
                arm=arm,  # labs-side only — never pushed to Connect
                case_properties={
                    "role": role,
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
                case_properties={"cluster": cluster, "arm": arm, "mode": "coverage", "lga": lga, "state": state},
            )
        )
    return out


def to_api_payload(payloads: list[WorkAreaPayload]) -> list[dict]:
    """Shape for the proposed POST /work_areas/ endpoint (keyed by model fields)."""
    return [
        {
            "slug": p.slug,
            "ward": p.ward,
            "centroid": {"type": "Point", "coordinates": [p.centroid_lon, p.centroid_lat]},
            "boundary_wkt": p.boundary_wkt,
            "building_count": p.building_count,
            "expected_visit_count": p.expected_visit_count,
            "target_population": p.target_population,
            "case_properties": p.case_properties,
        }
        for p in payloads
    ]


def to_csv_rows(payloads: list[WorkAreaPayload]) -> list[dict]:
    """Shape for Connect's existing web CSV importer (keyed by column labels)."""
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
