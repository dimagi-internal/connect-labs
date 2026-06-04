"""Pure-Python statistics + geometry helpers for survey-quality algorithms.

No third-party deps (no numpy/scipy/GDAL) so the package imports anywhere in
labs without a heavy environment. Everything here is small, deterministic, and
unit-tested in ``tests/test_survey_quality.py``.
"""

import math
from collections.abc import Sequence

# ---------------------------------------------------------------- basic stats


def mean(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def pstdev(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0 if xs else None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def median(xs: Sequence[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def mad(xs: Sequence[float]) -> float | None:
    """Median absolute deviation."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    med = median(xs)
    return median([abs(x - med) for x in xs])


def mad_modified_z(xs: Sequence[float]) -> list[float | None]:
    """Iglewicz-Hoaglin modified z-score: 0.6745*(x-median)/MAD.

    Robust to the very outliers we're hunting (mean/SD are corrupted by
    fabricated extremes). Convention: flag |z| > 3.5. Returns one score per
    input value (None where undefined).
    """
    vals = [x for x in xs]
    present = [x for x in vals if x is not None]
    if len(present) < 2:
        return [None] * len(vals)
    med = median(present)
    m = mad(present)
    if not m:  # all identical -> no spread, no outliers
        return [0.0 if x is not None else None for x in vals]
    return [None if x is None else 0.6745 * (x - med) / m for x in vals]


def iqr_bounds(xs: Sequence[float], k: float = 1.5) -> tuple[float | None, float | None]:
    """Tukey fences (Q1 - k*IQR, Q3 + k*IQR). SurveyCTO's default outlier rule
    uses k=1.5."""
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 4:
        return None, None

    def _q(p: float) -> float:
        idx = p * (len(xs) - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return xs[lo]
        return xs[lo] + (xs[hi] - xs[lo]) * (idx - lo)

    q1, q3 = _q(0.25), _q(0.75)
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr


# ---------------------------------------------------------------- inference


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_proportion_z(s1: int, n1: int, s2: int, n2: int) -> tuple[float | None, float | None]:
    """Two-sample test of equality of proportions (the ``prtest`` analogue used
    by IPA ``bcstats`` for binary back-check outcomes). Returns (z, two-sided
    p-value). p > 0.05 => the two proportions are statistically indistinguishable
    (the outcome reproduces under re-survey)."""
    if not n1 or not n2:
        return None, None
    p1, p2 = s1 / n1, s2 / n2
    p = (s1 + s2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    pval = 2 * (1 - normal_cdf(abs(z)))
    return z, pval


# ---------------------------------------------------------------- geometry


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _ring_contains(ring, x: float, y: float) -> bool:
    """Ray-casting point-in-ring. Ring coords are [lon, lat] pairs; x=lon, y=lat."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def outer_rings(geom: dict) -> list:
    """Outer ring(s) for a GeoJSON Polygon or MultiPolygon (holes ignored)."""
    t = geom.get("type")
    if t == "Polygon":
        return [geom["coordinates"][0]]
    if t == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def point_in_geom(geom: dict, lat: float, lon: float) -> bool:
    return any(_ring_contains(r, lon, lat) for r in outer_rings(geom))


def bbox(geom: dict) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for ring in outer_rings(geom):
        for x, y in ring:
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)
