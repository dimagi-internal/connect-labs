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
