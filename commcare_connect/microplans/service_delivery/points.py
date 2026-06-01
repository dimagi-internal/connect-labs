"""ServiceDeliveryPoints provider: an opportunity's visits -> GeoJSON GPS points.

The reusable core. Default path runs the built-in SERVICE_DELIVERY_GPS_SCHEMA
through the workflow pipeline engine (caching, multi-opp, CCHQ auth handled for
free). Override path reads any saved pipeline's rows and auto-detects lat/lon.

Pure helpers (rows_to_points, detect_lat_lon, points_to_geojson) are unit-tested;
fetch_points wires them to the live pipeline and is validated against labs.
"""

from __future__ import annotations

import logging

from commcare_connect.microplans.service_delivery.schema import SERVICE_DELIVERY_GPS_SCHEMA

logger = logging.getLogger(__name__)

# Distinct, color-blind-friendly palette; opportunities are colored in order.
OPP_COLORS = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#db2777",
    "#65a30d",
    "#ea580c",
    "#4f46e5",
]


def color_for(index: int) -> str:
    return OPP_COLORS[index % len(OPP_COLORS)]


def _parse_packed_location(value) -> tuple[float, float] | None:
    """Parse a packed "lat lon altitude accuracy" string -> (lon, lat)."""
    if not value or not isinstance(value, str):
        return None
    parts = value.split()
    if len(parts) < 2:
        return None
    try:
        return float(parts[1]), float(parts[0])  # (lon, lat)
    except (ValueError, TypeError):
        return None


def detect_lat_lon(columns) -> tuple[str, str] | None:
    """Pick the (lat_col, lon_col) from a pipeline's row columns.

    Returns ("location", "location") when only the packed base column is present
    (rows_to_points then parses it). None when no GPS column is detectable.
    """
    cols = set(columns)
    if "latitude" in cols and "longitude" in cols:
        return "latitude", "longitude"
    if "lat" in cols and "lon" in cols:
        return "lat", "lon"
    lat_suffix = next((c for c in columns if c.endswith("_lat")), None)
    lon_suffix = next((c for c in columns if c.endswith("_lon")), None)
    if lat_suffix and lon_suffix:
        return lat_suffix, lon_suffix
    if "location" in cols:
        return "location", "location"
    return None


# Per-point properties worth carrying to the map popup, if the row has them.
_CARRY_PROPS = ("username", "status", "visit_date", "entity_name", "flagged", "deliver_unit")


def _valid_lonlat(lon, lat) -> bool:
    try:
        lon, lat = float(lon), float(lat)
    except (ValueError, TypeError):
        return False
    if lon == 0.0 and lat == 0.0:
        return False  # null island — almost always a missing reading
    return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def rows_to_points(rows: list[dict]) -> list[dict]:
    """Pipeline rows -> [{"lon","lat", ...carried props}] for rows with valid GPS."""
    if not rows:
        return []
    detected = detect_lat_lon(rows[0].keys())
    if detected is None:
        return []
    lat_col, lon_col = detected

    points = []
    for row in rows:
        if lat_col == lon_col:  # packed "location" string
            parsed = _parse_packed_location(row.get(lat_col))
            if parsed is None:
                continue
            lon, lat = parsed
        else:
            lat, lon = row.get(lat_col), row.get(lon_col)
        if not _valid_lonlat(lon, lat):
            continue
        point = {"lon": float(lon), "lat": float(lat)}
        for key in _CARRY_PROPS:
            if key in row and row[key] is not None:
                point[key] = row[key]
        points.append(point)
    return points


def points_to_geojson(points: list[dict], opportunity_id: int, color: str) -> dict:
    """Points -> GeoJSON FeatureCollection, each feature tagged with opp + color."""
    features = []
    for p in points:
        props = {"opportunity_id": opportunity_id, "color": color}
        for key in _CARRY_PROPS:
            if key in p:
                props[key] = p[key]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": props,
            }
        )
    return {"type": "FeatureCollection", "features": features}


# Max GPS points the service-delivery overlay will return in one response. The
# overlay is a visual density cue, not analysis — beyond a few thousand dots it's
# indistinguishable but keeps inflating the payload (a multi-opp selection can be
# tens of thousands of points). Above the cap we uniformly subsample so the
# spatial spread is preserved, and report what was dropped (no silent truncation).
MAX_OVERLAY_POINTS = 6000


def downsample_features(features: list[dict], max_n: int = MAX_OVERLAY_POINTS) -> tuple[list[dict], bool, int]:
    """Cap a feature list to ``max_n`` by uniform stride. Returns
    ``(features, sampled, total)`` — ``sampled`` is True iff anything was dropped."""
    total = len(features)
    if max_n <= 0 or total <= max_n:
        return features, False, total
    stride = -(-total // max_n)  # ceil(total / max_n) → keeps ≤ max_n, evenly spread
    return features[::stride], True, total


def fetch_points(
    opp_id: int,
    request=None,
    access_token: str | None = None,
    pipeline_id: int | None = None,
) -> dict:
    """Fetch service-delivery GPS points for one opportunity.

    Returns {"points": [...], "stats": {...}, "error": str|None}. Never raises;
    on failure `error` is populated and `points` is empty (mirrors the pipeline
    engine's no-raise contract so multi-opp callers can detect per-opp failures).
    """
    from commcare_connect.workflow.data_access import PipelineDataAccess

    pda = PipelineDataAccess(opportunity_id=opp_id, request=request, access_token=access_token)
    if pipeline_id is not None:
        result = pda.execute_pipeline(pipeline_id, opp_id)
    else:
        result = pda.execute_pipeline_from_schema(SERVICE_DELIVERY_GPS_SCHEMA, opp_id, alias="service_delivery_gps")

    meta = result.get("metadata", {})
    error = meta.get("error")
    rows = result.get("rows", [])
    points = rows_to_points(rows)
    stats = {
        "opportunity_id": opp_id,
        "total_rows": len(rows),
        "with_gps": len(points),
        "gps_pct": round(100.0 * len(points) / len(rows), 1) if rows else 0.0,
        "from_cache": meta.get("from_cache", False),
    }
    out = {"points": points, "stats": stats, "error": error}
    # Surface CCHQ auth errors so the FE can prompt re-authorization.
    for k in ("auth_error", "auth_error_domain", "auth_authorize_url"):
        if k in meta:
            out[k] = meta[k]
    return out
