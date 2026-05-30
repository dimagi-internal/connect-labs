"""Derive a boundary polygon from a service-delivery point cloud.

Pure geometry, no I/O. The point cloud is the GPS locations of an opportunity's
visits; the boundary is "the border of the service-delivery data itself", which
becomes a planning area (sampling / coverage) or a reference for picking admin
boundaries.

Concave (alpha-shape) hull by default: a convex hull bridges across rivers and
uninhabited gaps and badly over-covers real delivery footprints, which would
inflate any downstream sampling/coverage frame. The hull and the outward buffer
are both computed in the local UTM zone (meters) so `buffer_m` is metric and the
`concavity` length-scale is meaningful, then reprojected to WGS84.
"""

from __future__ import annotations

from pyproj import Transformer
from shapely import concave_hull
from shapely.geometry import MultiPoint, mapping
from shapely.ops import transform as shp_transform

from commcare_connect.microplans.core.geo import utm_epsg_for


def derive_boundary(
    points: list[dict],
    method: str = "concave",
    concavity: float = 0.3,
    buffer_m: float = 25.0,
) -> dict:
    """Boundary polygon (GeoJSON geometry) enclosing `points`.

    Args:
        points: list of {"lon", "lat"} dicts (extra keys ignored).
        method: "concave" (alpha shape, default) or "convex".
        concavity: shapely concave_hull `ratio` (0..1). Lower hugs the cloud
            tighter; higher approaches the convex hull. Ignored for convex.
            Exposed to the UI as a "tightness" slider.
        buffer_m: outward buffer in meters applied to the hull (0 = none).

    Returns:
        A GeoJSON geometry dict (Polygon or MultiPolygon).

    Raises:
        ValueError: if `points` is empty.
    """
    if not points:
        raise ValueError("Cannot derive a boundary from zero points.")

    lons = [float(p["lon"]) for p in points]
    lats = [float(p["lat"]) for p in points]
    epsg = utm_epsg_for(sum(lons) / len(lons), sum(lats) / len(lats))
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = Transformer.from_crs(epsg, 4326, always_xy=True)

    xs, ys = fwd.transform(lons, lats)
    mp = MultiPoint(list(zip(xs, ys)))

    if method == "convex" or len(points) < 3:
        # convex_hull of 1-2 points is a point/line with zero area; the buffer
        # (forced positive below for that case) gives it a usable footprint.
        hull = mp.convex_hull
    else:
        hull = concave_hull(mp, ratio=max(0.0, min(1.0, concavity)))

    effective_buffer = buffer_m
    if hull.area == 0 and effective_buffer <= 0:
        effective_buffer = 25.0  # degenerate cloud (collinear / <3 pts) needs area
    if effective_buffer > 0:
        hull = hull.buffer(effective_buffer)

    hull_wgs84 = shp_transform(lambda x, y, z=None: inv.transform(x, y), hull)
    return mapping(hull_wgs84)
