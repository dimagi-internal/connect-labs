"""Small geographic helpers shared by the sampling stages.

Distances in the rooftop methodology are small (12m neighbor checks, 15m pin
separation), so we project lon/lat to the local UTM zone (meters) and do planar
math there — accurate to well under a meter at these scales.
"""

from __future__ import annotations

import math

import numpy as np
from pyproj import Transformer


def utm_epsg_for(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180) / 6) % 60) + 1
    return (32600 if lat >= 0 else 32700) + zone


def project_to_meters(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Project lon/lat arrays to the UTM zone of their centroid. Returns (x, y, epsg)."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    epsg = utm_epsg_for(float(np.mean(lon)), float(np.mean(lat)))
    transformer = Transformer.from_crs(4326, epsg, always_xy=True)
    x, y = transformer.transform(lon, lat)
    return np.asarray(x), np.asarray(y), epsg


def point_buffer(lon: float, lat: float, radius_m: float, quad_segs: int = 32):
    """A circular WGS84 polygon: the point buffered by `radius_m` meters.

    Used by the "buildings around a pin" area input — drop a pin + radius and the
    buffer becomes the sampling area. Buffering is done in the local UTM zone
    (meters) then reprojected, so the circle is metrically accurate.
    """
    from shapely.geometry import Point
    from shapely.ops import transform as shp_transform

    epsg = utm_epsg_for(lon, lat)
    fwd = Transformer.from_crs(4326, epsg, always_xy=True)
    inv = Transformer.from_crs(epsg, 4326, always_xy=True)
    x, y = fwd.transform(lon, lat)
    circle_m = Point(x, y).buffer(max(1.0, float(radius_m)), quad_segs=quad_segs)
    return shp_transform(lambda xs, ys, z=None: inv.transform(xs, ys), circle_m)
