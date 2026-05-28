"""Admin boundaries from Overture Maps' global `divisions` theme.

The existing `labs.admin_boundaries` app is a curated ~14-country library
(geoBoundaries/OSM/GRID3 per-country configs). Overture's divisions theme is
global and queryable through the same DuckDB+S3 path we already use for
buildings, so the rooftop area-picker can offer "pick an admin area" for any
country with no new infrastructure.

Subtype hierarchy (Overture): country → region (state/province) → county
(district/LGA) → locality (ward/settlement). The picker walks down from a
country; the chosen area's polygon becomes the sampling frame's boundary.

Quality varies by country (Overture fuses OSM + open boundary sets), so where
`labs.admin_boundaries` has a higher-quality local source we still prefer it;
Overture is the universal default/fallback.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.core.cache import cache

from commcare_connect.rooftop_surveys.sampling import overture

logger = logging.getLogger(__name__)

DIVISION_AREA = overture.theme_path("divisions", "division_area")
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days — admin boundaries are stable.

# Overture subtypes that make sense as a sampling-area choice, coarse → fine.
PICKABLE_SUBTYPES = ("region", "county", "locality")


def _key(prefix: str, *parts) -> str:
    h = hashlib.sha256("|".join([overture.OVERTURE_RELEASE, *map(str, parts)]).encode()).hexdigest()[:24]
    return f"rooftop:boundaries:{prefix}:{h}"


def list_admin_areas(
    country_iso2: str,
    subtype: str | None = None,
    region: str | None = None,
    name_contains: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """List admin areas for a country (no geometry — for picker dropdowns).

    Returns dicts: {name, subtype, region, area_km2}. `region` filters to a
    state code (e.g. "NG-BO"); `subtype` filters the level; `name_contains`
    does a case-insensitive substring match.
    """
    key = _key("list", country_iso2, subtype, region, name_contains, limit)
    cached = cache.get(key)
    if cached is not None:
        return cached

    where = [f"country = '{_sql(country_iso2)}'"]
    where.append(
        f"subtype = '{_sql(subtype)}'"
        if subtype
        else "subtype IN (" + ",".join(f"'{s}'" for s in PICKABLE_SUBTYPES) + ")"
    )
    if region:
        where.append(f"region = '{_sql(region)}'")
    if name_contains:
        where.append(f"lower(names.primary) LIKE '%{_sql(name_contains.lower())}%'")

    con = overture.connect()
    rows = con.execute(
        f"""
        SELECT names.primary AS name, subtype, region,
               round(ST_Area_Spheroid(geometry)/1e6, 1) AS area_km2
        FROM read_parquet('{DIVISION_AREA}', filename=false, hive_partitioning=true)
        WHERE {' AND '.join(where)} AND names.primary IS NOT NULL
        ORDER BY subtype, name
        LIMIT {int(limit)}
        """
    ).df()
    result = rows.to_dict("records")
    cache.set(key, result, CACHE_TTL_SECONDS)
    logger.info("rooftop boundaries listed: %d areas (%s/%s)", len(result), country_iso2, subtype)
    return result


def get_admin_area_geojson(country_iso2: str, name: str, subtype: str, region: str | None = None) -> dict | None:
    """Return the GeoJSON geometry for one admin area (for use as a sampling boundary)."""
    key = _key("geom", country_iso2, name, subtype, region)
    cached = cache.get(key)
    if cached is not None:
        return cached

    where = [
        f"country = '{_sql(country_iso2)}'",
        f"subtype = '{_sql(subtype)}'",
        f"names.primary = '{_sql(name)}'",
    ]
    if region:
        where.append(f"region = '{_sql(region)}'")

    con = overture.connect()
    rows = con.execute(
        f"""
        SELECT ST_AsGeoJSON(geometry) AS geojson
        FROM read_parquet('{DIVISION_AREA}', filename=false, hive_partitioning=true)
        WHERE {' AND '.join(where)}
        LIMIT 1
        """
    ).fetchall()
    geom = json.loads(rows[0][0]) if rows else None
    cache.set(key, geom, CACHE_TTL_SECONDS)
    return geom


def _sql(value: str) -> str:
    """Escape single quotes for inlining into a DuckDB string literal."""
    return str(value).replace("'", "''")
