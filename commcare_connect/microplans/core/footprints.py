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

import pandas as pd
from django.db import IntegrityError, transaction
from shapely.geometry.base import BaseGeometry

from commcare_connect.microplans.core import overture

logger = logging.getLogger(__name__)

OVERTURE_BUILDINGS = overture.theme_path("buildings", "building")
_COLUMNS = ["lon", "lat", "area_m2", "confidence"]
# Reject absurdly large areas before they pull gigabytes from S3 and OOM the
# worker. A survey area is a ward/LGA (Maiduguri LGA ≈ 107 km²); 2000 km² is a
# generous ceiling that still blocks "the whole country" mistakes.
MAX_AREA_KM2 = 2000.0


def _area_cache_key(wkt: str) -> str:
    # Geometry-only (confidence-agnostic): we store every building + filter by
    # confidence at read, so a ward is fetched once for both sampling and coverage.
    return hashlib.sha256(f"{overture.OVERTURE_RELEASE}|{wkt}".encode()).hexdigest()


def _apply_confidence(df: pd.DataFrame, min_confidence: float | None) -> pd.DataFrame:
    """Drop low/null-confidence buildings when a threshold is set (matches the
    pilot's Google-Open-Buildings input). No-op when min_confidence is None."""
    if min_confidence is None or df.empty:
        return df
    keep = df["confidence"].notna() & (df["confidence"] >= float(min_confidence))
    return df[keep].reset_index(drop=True)


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
    from commcare_connect.microplans.models import FootprintArea, FootprintBuilding

    approx_km2 = _approx_area_km2(area)
    if approx_km2 > MAX_AREA_KM2:
        raise ValueError(
            f"Area is too large (~{approx_km2:,.0f} km² bounding box; max {MAX_AREA_KM2:,.0f}). "
            "Draw a smaller area (a ward or LGA)."
        )
    area_hash = _area_cache_key(area.wkt)

    cached = FootprintArea.objects.filter(area_hash=area_hash).first()
    if cached is not None:
        df = pd.DataFrame(list(cached.buildings.values(*_COLUMNS)), columns=_COLUMNS)
        logger.info("microplans footprints cache hit (%s, %d buildings)", area_hash[:12], len(df))
        return _apply_confidence(df, min_confidence)

    # Miss: fetch every building once (confidence-agnostic) and persist as rows.
    df_all = _query_overture(area, min_confidence=None)
    try:
        with transaction.atomic():
            fa = FootprintArea.objects.create(
                area_hash=area_hash, overture_release=overture.OVERTURE_RELEASE, n_buildings=len(df_all)
            )
            FootprintBuilding.objects.bulk_create(
                [
                    FootprintBuilding(
                        area=fa,
                        lon=r.lon,
                        lat=r.lat,
                        area_m2=(None if pd.isna(r.area_m2) else r.area_m2),
                        confidence=(None if pd.isna(r.confidence) else r.confidence),
                    )
                    for r in df_all.itertuples(index=False)
                ],
                batch_size=2000,
            )
        logger.info("microplans footprints fetched + cached: %d buildings (%s)", len(df_all), area_hash[:12])
    except IntegrityError:
        # Concurrent miss for the same area beat us to it — read what they wrote.
        logger.info("microplans footprints concurrent fill (%s); using stored rows", area_hash[:12])
        fa = FootprintArea.objects.get(area_hash=area_hash)
        df_all = pd.DataFrame(list(fa.buildings.values(*_COLUMNS)), columns=_COLUMNS)
    return _apply_confidence(df_all, min_confidence)


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
