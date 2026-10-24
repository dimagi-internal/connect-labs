"""Physical barriers (major roads, railways, water) for barrier-aware WAG grouping.

A work-area group should never require crossing a river or a major road, so the
barrier-aware grouper needs the *lines* of those features. We read them from
labs' Overture snapshot (transportation/segment for roads + rail; base/water for
rivers/lakes; overture.py) with the same DuckDB pipeline as buildings, and cache
the merged line geometry per area (durable Postgres cache, keyed by geometry
hash) — tiles or the raw snapshot the first time, then instant.

A failed read RAISES. It used to be caught and logged here, which returned "no
barriers" — and fetch_barriers then cached that as the area's answer. That is
how October 2026's expired Overture release made grouping run without roads or
rivers with nothing visible anywhere. Callers that choose to degrade to plain
grouping do so themselves, and log it.

Only MAJOR roads split groups (motorway/trunk/primary/secondary). Minor/residential
streets are deliberately excluded — splitting on them would fragment every block.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.db import IntegrityError, transaction
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from connect_labs.microplans.core import overture

logger = logging.getLogger(__name__)

# Major road classes that meaningfully split a settlement (Overture transportation
# `class`). Residential/tertiary/etc. are intentionally omitted.
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")
# Water bodies large enough to be real barriers (Overture base/water `class`).
WATER_CLASSES = ("river", "canal", "lake", "reservoir")
_MAX_ROWS = 200_000


def _area_hash(wkt: str) -> str:
    # `barrier1` namespaces the key; bump if the barrier query/definition changes.
    return hashlib.sha256(f"barrier1|{overture.active_release()}|{wkt}".encode()).hexdigest()


def fetch_barriers(area: BaseGeometry, allow_remote: bool = True) -> BaseGeometry | None:
    """Merged barrier lines (roads + rail + water outlines) intersecting ``area``.

    Returns a shapely geometry (usually MultiLineString) or None when there are no
    barriers / nothing cached. ``allow_remote=False`` reads the cache only (used on
    the web tier, where a cold Overture fetch would block a request) — the cache is
    warmed on the Celery preview/regroup paths.
    """
    from connect_labs.microplans.models import BarrierArea

    h = _area_hash(area.wkt)
    row = BarrierArea.objects.filter(area_hash=h).first()
    if row is not None:
        return shape(row.geom_json) if row.geom_json else None
    if not allow_remote:
        return None

    geom = _query_barriers(area)
    n = 0 if geom is None else (len(geom.geoms) if hasattr(geom, "geoms") else 1)
    try:
        with transaction.atomic():
            BarrierArea.objects.create(
                area_hash=h,
                overture_release=overture.active_release(),
                n_features=n,
                geom_json=(mapping(geom) if geom is not None else None),
            )
        logger.info("microplans barriers fetched + cached: %d features (%s)", n, h[:12])
    except IntegrityError:
        row = BarrierArea.objects.get(area_hash=h)
        return shape(row.geom_json) if row.geom_json else None
    return geom


def barriers_for_areas(areas: list[dict] | None, allow_remote: bool = True) -> BaseGeometry | None:
    """Merged barriers across a plan's input areas, fetched PER AREA (small bbox each,
    like footprints) and unioned — avoids one giant bbox over scattered wards. The
    per-area cache keys line up whether we warm (preview) or read (create/regroup)."""
    from connect_labs.microplans.core.area_input import resolve_area

    geoms = []
    for a in areas or []:
        try:
            g = resolve_area(a)
        except Exception:  # noqa: BLE001
            continue
        bg = fetch_barriers(g, allow_remote=allow_remote)
        if bg is not None and not bg.is_empty:
            geoms.append(bg)
    if not geoms:
        return None
    merged = unary_union(geoms)
    return None if merged.is_empty else merged


def _lines_from_rows(rows) -> list:
    """Parse ST_AsGeoJSON strings → shapely; polygons (lakes) contribute their
    boundary so a segment crossing the water still intersects a barrier line."""
    out = []
    for (gj,) in rows:
        if not gj:
            continue
        try:
            g = shape(json.loads(gj))
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty:
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            g = g.boundary
        out.append(g)
    return out


def segment_filters() -> dict:
    """What the barrier read selects from transportation/segment. Tiles record it."""
    return {"road_classes": list(ROAD_CLASSES), "rail": True}


def water_filters() -> dict:
    """What the barrier read selects from base/water. Tiles record it."""
    return {"water_classes": list(WATER_CLASSES)}


# bbox OVERLAP pruning (not containment): a through-road's bbox extends past the
# ward, so containment would wrongly drop exactly the barriers we want.
_BBOX_OVERLAP = "bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ?"


def _tile_lines(con, kind: str, cells, overlap: list[float], wkt: str) -> list:
    """Lines of one kind from the grid cells the area touches. A feature is written
    to every cell its bbox touches, so it is read once per cell it spans: one row
    per id."""
    files = overture.cell_files(con, kind, cells)
    if not files:  # covered, and nothing there
        return []
    sql = f"""
        SELECT ST_AsGeoJSON(ST_GeomFromWKB(any_value(geom_wkb))) AS g
        FROM read_parquet({files!r})
        WHERE {_BBOX_OVERLAP}
          AND ST_Intersects(ST_GeomFromWKB(geom_wkb), ST_GeomFromText(?))
        GROUP BY id
        LIMIT {_MAX_ROWS}
    """
    return _lines_from_rows(con.execute(sql, [*overlap, wkt]).fetchall())


def _query_barriers(area: BaseGeometry) -> BaseGeometry | None:
    """Major roads + rail + water in the area, from labs' snapshot (tiles, else raw)."""
    minx, miny, maxx, maxy = area.bounds
    wkt = area.wkt
    con = overture.connect()
    overlap = [maxx, minx, maxy, miny]
    lines = []

    cells = overture.tile_cells(area.bounds, "segments", filters=segment_filters())
    if cells is not None:
        lines += _tile_lines(con, "segments", cells, overlap, wkt)
    else:
        road_in = ", ".join(f"'{c}'" for c in ROAD_CLASSES)  # constant whitelist, safe to inline
        roads_sql = f"""
            SELECT ST_AsGeoJSON(geometry) AS g
            FROM read_parquet('{overture.theme_path('transportation', 'segment')}',
                              filename=false, hive_partitioning=true)
            WHERE {_BBOX_OVERLAP}
              AND ST_Intersects(geometry, ST_GeomFromText(?))
              AND ((subtype = 'road' AND class IN ({road_in})) OR subtype = 'rail')
            LIMIT {_MAX_ROWS}
        """
        lines += _lines_from_rows(con.execute(roads_sql, [*overlap, wkt]).fetchall())

    cells = overture.tile_cells(area.bounds, "water", filters=water_filters())
    if cells is not None:
        lines += _tile_lines(con, "water", cells, overlap, wkt)
    else:
        water_in = ", ".join(f"'{c}'" for c in WATER_CLASSES)
        water_sql = f"""
            SELECT ST_AsGeoJSON(geometry) AS g
            FROM read_parquet('{overture.theme_path('base', 'water')}',
                              filename=false, hive_partitioning=true)
            WHERE {_BBOX_OVERLAP}
              AND ST_Intersects(geometry, ST_GeomFromText(?))
              AND class IN ({water_in})
            LIMIT {_MAX_ROWS}
        """
        lines += _lines_from_rows(con.execute(water_sql, [*overlap, wkt]).fetchall())

    if not lines:
        return None
    merged = unary_union(lines)
    return None if merged.is_empty else merged
