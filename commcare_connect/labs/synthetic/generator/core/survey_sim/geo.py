"""Geometry + shared sampling primitives for survey simulation.

Pure: no Django, no DB, no network. Reusable building blocks the scatter and
back-check simulators share — rejection-sample a point inside a ward polygon,
offset a point by a metric distance, linear interpolation, and the household
roof-type categorical (a stable Type-1 back-check identifier).
"""

from __future__ import annotations

import math
import random

from commcare_connect.labs.synthetic.generator.core.survey_quality.stats import bbox, point_in_geom

_M_PER_DEG = 111_320.0  # metres per degree latitude (good enough at this scale)

# Stable household attribute used as a Type-1 back-check identifier (a roof can't
# change between two visits a week apart — a mismatch is a fabrication signal).
_ROOF_TYPES = ["thatch", "metal sheet", "mud", "tile"]
_ROOF_WEIGHTS = [0.42, 0.34, 0.16, 0.08]


def _sample_in_geom(rng: random.Random, geom: dict, n: int) -> list:
    x0, y0, x1, y1 = bbox(geom)
    pts, guard = [], 0
    while len(pts) < n and guard < n * 200:
        guard += 1
        lon, lat = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if point_in_geom(geom, lat, lon):
            pts.append((lat, lon))
    return pts


def _offset(rng: random.Random, lat: float, lon: float, meters: float) -> tuple:
    """Move a point by ``meters`` in a random bearing (small-distance approx)."""
    bearing = rng.uniform(0, 2 * math.pi)
    dlat = (meters * math.cos(bearing)) / _M_PER_DEG
    dlon = (meters * math.sin(bearing)) / (_M_PER_DEG * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def _interp(a: float, b: float, i: int, n: int) -> float:
    return a if n <= 1 else a + (b - a) * (i / (n - 1))
