"""Lazy per-area building-footprint fetch from Overture Maps.

Overture publishes the global buildings theme as Parquet on a public S3 bucket.
We query it with DuckDB (spatial + httpfs) filtered to the drawn area's bounding
box (predicate pushdown via Overture's `bbox` struct column) and clipped to the
actual polygon. No planet-scale download, no resident warehouse — just the area
we need, cached aggressively per area-hash.

Returns one row per building: centroid lon/lat, spheroidal area (m²), and the
Google-source confidence (null for Microsoft/OSM footprints). This is the input
shape the R sampling pipeline consumes (minus `distance_to_visit`, which the
pilot forced to a single "Low" stratum anyway — see cluster.py).
"""

from __future__ import annotations

import hashlib
import logging
import pickle

import pandas as pd
from django.core.cache import cache
from shapely.geometry.base import BaseGeometry

from commcare_connect.microplans.sampling import overture

logger = logging.getLogger(__name__)

OVERTURE_BUILDINGS = overture.theme_path("buildings", "building")
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days — building stock barely moves.
# Reject absurdly large areas before they pull gigabytes from S3 and OOM the
# worker. A survey area is a ward/LGA (Maiduguri LGA ≈ 107 km²); 2000 km² is a
# generous ceiling that still blocks "the whole country" mistakes.
MAX_AREA_KM2 = 2000.0


def _area_cache_key(wkt: str, min_confidence: float | None) -> str:
    h = hashlib.sha256(f"{overture.OVERTURE_RELEASE}|{min_confidence}|{wkt}".encode()).hexdigest()
    return f"rooftop:footprints:{h}"


def _approx_area_km2(area: BaseGeometry) -> float:
    """Cheap bbox-based area estimate in km² (upper bound; no projection needed)."""
    import math

    minx, miny, maxx, maxy = area.bounds
    mid_lat = math.radians((miny + maxy) / 2)
    width_km = (maxx - minx) * 111.32 * max(math.cos(mid_lat), 0.01)
    height_km = (maxy - miny) * 110.57
    return abs(width_km * height_km)


def fetch_buildings(area: BaseGeometry, min_confidence: float | None = None) -> pd.DataFrame:
    """Fetch building footprints whose centroid falls inside `area`.

    Args:
        area: a shapely Polygon/MultiPolygon in WGS84 (lon/lat).
        min_confidence: if set, drop buildings whose Google-source confidence is
            below this. Null-confidence footprints (Microsoft/OSM) are dropped too
            when this is set — matching the pilot's Google-Open-Buildings input.

    Returns:
        DataFrame[lon, lat, area_m2, confidence].

    Raises:
        ValueError: if the area's bounding box exceeds MAX_AREA_KM2.
    """
    approx_km2 = _approx_area_km2(area)
    if approx_km2 > MAX_AREA_KM2:
        raise ValueError(
            f"Area is too large (~{approx_km2:,.0f} km² bounding box; max {MAX_AREA_KM2:,.0f}). "
            "Draw a smaller area (a ward or LGA)."
        )
    wkt = area.wkt
    key = _area_cache_key(wkt, min_confidence)
    cached = cache.get(key)
    if cached is not None:
        logger.info("rooftop footprints cache hit (%s)", key)
        return pickle.loads(cached)

    df = _query_overture(area, min_confidence)
    cache.set(key, pickle.dumps(df, protocol=pickle.HIGHEST_PROTOCOL), CACHE_TTL_SECONDS)
    logger.info("rooftop footprints fetched + cached: %d buildings (%s)", len(df), key)
    return df


def _query_overture(area: BaseGeometry, min_confidence: float | None) -> pd.DataFrame:
    minx, miny, maxx, maxy = area.bounds
    wkt = area.wkt

    con = overture.connect()

    # Parameterized: all caller-derived values bind as `?` (no string interpolation
    # of user-drawn geometry into SQL). The read_parquet path is a constant.
    conf_clause = ""
    params = [minx, maxx, miny, maxy, wkt]
    if min_confidence is not None:
        conf_clause = "AND sources[1].confidence >= ?"
        params.append(float(min_confidence))

    query = f"""
        SELECT
            ST_X(ST_Centroid(geometry)) AS lon,
            ST_Y(ST_Centroid(geometry)) AS lat,
            ST_Area_Spheroid(geometry)  AS area_m2,
            sources[1].confidence       AS confidence
        FROM read_parquet('{OVERTURE_BUILDINGS}', filename=false, hive_partitioning=true)
        WHERE bbox.xmin >= ? AND bbox.xmax <= ?
          AND bbox.ymin >= ? AND bbox.ymax <= ?
          AND ST_Within(ST_Centroid(geometry), ST_GeomFromText(?))
          {conf_clause}
    """
    return con.execute(query, params).df()
