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

logger = logging.getLogger(__name__)

# Overture release. Bump as Overture cuts monthly releases; the S3 layout is stable.
OVERTURE_RELEASE = "2026-05-20.0"
OVERTURE_BUILDINGS = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}/theme=buildings/type=building/*"
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days — building stock barely moves.


def _area_cache_key(wkt: str, min_confidence: float | None) -> str:
    h = hashlib.sha256(f"{OVERTURE_RELEASE}|{min_confidence}|{wkt}".encode()).hexdigest()[:24]
    return f"rooftop:footprints:{h}"


def fetch_buildings(area: BaseGeometry, min_confidence: float | None = None) -> pd.DataFrame:
    """Fetch building footprints whose centroid falls inside `area`.

    Args:
        area: a shapely Polygon/MultiPolygon in WGS84 (lon/lat).
        min_confidence: if set, drop buildings whose Google-source confidence is
            below this. Null-confidence footprints (Microsoft/OSM) are dropped too
            when this is set — matching the pilot's Google-Open-Buildings input.

    Returns:
        DataFrame[lon, lat, area_m2, confidence].
    """
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
    import duckdb

    minx, miny, maxx, maxy = area.bounds
    wkt = area.wkt

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")

    conf_clause = ""
    if min_confidence is not None:
        conf_clause = f"AND sources[1].confidence >= {float(min_confidence)}"

    query = f"""
        SELECT
            ST_X(ST_Centroid(geometry)) AS lon,
            ST_Y(ST_Centroid(geometry)) AS lat,
            ST_Area_Spheroid(geometry)  AS area_m2,
            sources[1].confidence       AS confidence
        FROM read_parquet('{OVERTURE_BUILDINGS}', filename=false, hive_partitioning=true)
        WHERE bbox.xmin >= {minx} AND bbox.xmax <= {maxx}
          AND bbox.ymin >= {miny} AND bbox.ymax <= {maxy}
          AND ST_Within(ST_Centroid(geometry), ST_GeomFromText('{wkt}'))
          {conf_clause}
    """
    return con.execute(query).df()
