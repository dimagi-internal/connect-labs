"""Estimated population for an area that carries none of its own.

A ward picked from the boundary layer brings its ``extra.populations`` bag
(WorldPop / Meta / GRID3 / GeoPoDe, total + under-5), which the setup planning
table shows. A freehand polygon, a pin circle or an unmatched uploaded shape has
no bag, so its row showed "—". This estimates one from the wards the area
overlaps: each ward contributes its populations in proportion to the share of
ITS buildings that fall inside the area — people live in buildings, so this
follows settlement where an area share would spread them evenly over farmland.

    estimate[key] = Σ_wards  ward.populations[key] × (ward buildings inside area ÷ ward buildings)

A ward with no buildings on record falls back to the share of its area the shape
covers. So does every ward when the shape overlaps more than
``MAX_WARDS_FOR_BUILDING_SHARES`` of them (each ward is its own footprint fetch).

``combine_ward_shares`` is the arithmetic, pure (no DB, no network);
``estimate_area_population`` gathers its inputs.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from shapely import contains_xy, make_valid, prepare

logger = logging.getLogger(__name__)

# Above this many overlapping wards, shares are by area for all of them rather than
# fetching every ward's footprints (a cold fetch is seconds to minutes per ward).
MAX_WARDS_FOR_BUILDING_SHARES = 25

BASIS_BUILDINGS = "buildings"
BASIS_AREA = "area"


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def combine_ward_shares(wards: list[dict]) -> dict:
    """Combine overlapping wards' populations into one estimate for an area.

    Each ward: ``{name, provider, populations: {key: number}, inside, total,
    area_fraction}`` — ``inside`` = the area's buildings within the ward, ``total`` =
    the ward's own building count (either may be None when not fetched), and
    ``area_fraction`` = area(area ∩ ward) ÷ area(ward), the fallback share.

    A ward's share is ``inside / total`` when ``total > 0``, else ``area_fraction``;
    clamped to [0, 1]. A key is estimated only when EVERY ward that contributes
    (share > 0) has it — summing a key some wards lack would silently undercount.

    Returns ``{"populations_est": {key: number}, "population_basis": [{ward,
    provider, share, basis}]}`` (basis is "buildings" or "area")."""
    basis = []
    shared = []  # (share, numeric bag) per ward
    for w in wards:
        total, inside = w.get("total"), w.get("inside")
        if total and inside is not None:
            share, how = inside / total, BASIS_BUILDINGS
        else:
            share, how = (w.get("area_fraction") or 0.0), BASIS_AREA
        share = min(max(float(share), 0.0), 1.0)
        shared.append((share, _num_bag(w.get("populations"))))
        basis.append(
            {"ward": w.get("name") or "", "provider": w.get("provider") or "", "share": round(share, 4), "basis": how}
        )
    # Keys every contributing ward carries. When the area holds none of the wards'
    # buildings, every ward "contributes" 0 — an honest 0, not a blank.
    contributing = [(s, p) for s, p in shared if s > 0] or shared
    keys = set.intersection(*(set(p) for _, p in contributing)) if contributing else set()
    estimate = {k: round(sum(s * p[k] for s, p in contributing), 1) for k in sorted(keys)}
    return {"populations_est": estimate, "population_basis": basis}


def _num_bag(pops) -> dict:
    """The numeric entries of a populations bag (drops blanks and junk)."""
    return {k: v for k, v in ((k, _num(v)) for k, v in (pops or {}).items()) if v is not None}


def count_points_within(geom, lons, lats) -> int:
    """How many (lon, lat) points fall inside ``geom`` — building centroids vs a ward."""
    if len(lons) == 0:
        return 0
    prepare(geom)
    return int(np.count_nonzero(contains_xy(geom, np.asarray(lons, dtype=float), np.asarray(lats, dtype=float))))


def estimate_area_population(
    area,
    area_buildings: pd.DataFrame,
    *,
    min_confidence: float | None = None,
    fetch=None,
    on_progress=None,
) -> dict | None:
    """Estimated populations for ``area`` (a WGS84 shapely geometry) from the wards
    it overlaps, or None when it overlaps no ward with populations.

    ``area_buildings`` is the area's own footprint frame (``fetch_buildings``; its
    ``lon``/``lat`` are building centroids). Each overlapping ward's building total
    is fetched with the same ``min_confidence`` and every provider, so both sides of
    the share count the same kind of building. ``fetch`` defaults to
    ``footprints.fetch_buildings`` (PG-cached per ward geometry, so a ward a second
    area also overlaps is a cache hit).

    Returns ``combine_ward_shares``'s dict, plus ``population_note`` when every
    share fell back to area because too many wards overlap."""
    from connect_labs.microplans.core.admin_boundaries import overlapping_population_wards

    if fetch is None:
        from connect_labs.microplans.core.footprints import fetch_buildings as fetch

    if not area.is_valid:
        area = make_valid(area)  # a self-crossing freehand polygon
    wards = overlapping_population_wards(area)
    entries = []
    for w in wards:
        geom = w["geometry"]
        overlap = area.intersection(geom).area
        if overlap <= 0:
            continue  # only touches along an edge
        entries.append(
            {
                "name": w["name"],
                "provider": w["source"],
                "populations": w["populations"],
                "geometry": geom,
                "area_fraction": overlap / geom.area if geom.area else 0.0,
                "inside": None,
                "total": None,
            }
        )
    if not entries:
        return None

    by_buildings = len(entries) <= MAX_WARDS_FOR_BUILDING_SHARES
    if by_buildings:
        lons = area_buildings["lon"].to_numpy() if "lon" in area_buildings else []
        lats = area_buildings["lat"].to_numpy() if "lat" in area_buildings else []
        for n, e in enumerate(entries, start=1):
            if on_progress:
                on_progress(f"Estimating population: ward {n} of {len(entries)} ({e['name']})…")
            e["inside"] = count_points_within(e["geometry"], lons, lats)
            try:
                e["total"] = len(fetch(e["geometry"], min_confidence=min_confidence, with_geom=False))
            except ValueError:
                # e.g. a ward too large to fetch — fall back to its area share.
                logger.warning("microplans: ward footprint fetch failed for %s", e["name"], exc_info=True)
    result = combine_ward_shares(entries)
    if not by_buildings:
        result["population_note"] = (
            f"This area overlaps {len(entries)} wards (more than {MAX_WARDS_FOR_BUILDING_SHARES}), "
            "so each ward's share is by area, not by buildings."
        )
    return result
