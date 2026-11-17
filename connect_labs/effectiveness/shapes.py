"""Outlines for the workspace map, keyed by the same ``key`` a market row carries.

The models hold their figures per country, and the baseline holds regional counts and mortality, so a market is a
country's or a territory's; the map draws the baseline's country (ADM0) or state-and-province (ADM1) outlines and a
page paints them with the rows. Outlines do not depend on the scenario, so each is one cached answer for everyone and
the page fetches it once. Read through the baseline's public ``boundaries`` module.
"""

from __future__ import annotations

import json

from django.core.cache import cache

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators import response_cache
from connect_labs.labs.indicators.africa import name_for

#: Degrees of tolerance when simplifying an outline. A country is large, so coarse is right; a state or province is
#: smaller, so finer, and the answer stays a few hundred kilobytes.
SIMPLIFY = {0: 0.04, 1: 0.02}


def _outlines(level: int, key) -> dict:
    cache_key = f"effectiveness:shapes:{level}:" + response_cache.data_version()
    got = cache.get(cache_key)
    if got is not None:
        return got
    features = []
    for b in boundary_set.owned().filter(admin_level=level).order_by("iso_code", "name"):
        geom = b.geometry.simplify(SIMPLIFY[level], preserve_topology=True)
        if geom.empty or geom.num_coords == 0:
            geom = b.geometry
        k = key(b)
        features.append(
            {
                "type": "Feature",
                "id": k,
                "geometry": json.loads(geom.geojson),
                "properties": {
                    "key": k,
                    "iso": b.iso_code,
                    "name": b.name if level else name_for(b.iso_code),
                    "country": name_for(b.iso_code),
                },
            }
        )
    out = {"type": "FeatureCollection", "features": features}
    cache.set(cache_key, out, response_cache.TTL_SECONDS)
    return out


def country_outlines() -> dict:
    """A GeoJSON FeatureCollection, one feature per country with ``key`` and ``iso`` (alpha-3) and ``name``."""
    return _outlines(0, lambda b: b.iso_code)


def territory_outlines() -> dict:
    """One feature per state or province, ``key`` the boundary's id (the key a territory's market row carries)."""
    return _outlines(1, lambda b: str(b.pk))


def outlines(grain: str) -> dict:
    return territory_outlines() if grain == "territory" else country_outlines()
