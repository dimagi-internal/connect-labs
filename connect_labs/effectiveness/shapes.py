"""Country outlines for the workspace map, keyed by the same alpha-3 code a market row carries.

The models hold their figures per country, so a market is a country's; the map draws the baseline's country (ADM0)
outlines and a page paints them with the rows. Outlines do not depend on the scenario, so they are one cached answer
for everyone and the page fetches them once. Read through the baseline's public ``boundaries`` module.
"""

from __future__ import annotations

import json

from django.core.cache import cache

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators import response_cache
from connect_labs.labs.indicators.africa import name_for

#: Degrees of tolerance when simplifying an outline: country-sized shapes, so coarse is right and the answer small.
SIMPLIFY = 0.04


def country_outlines() -> dict:
    """A GeoJSON FeatureCollection, one feature per country with ``iso`` (alpha-3) and ``name``."""
    key = "effectiveness:shapes:" + response_cache.data_version()
    got = cache.get(key)
    if got is not None:
        return got
    features = []
    for b in boundary_set.owned().filter(admin_level=0).order_by("iso_code"):
        geom = b.geometry.simplify(SIMPLIFY, preserve_topology=True)
        if geom.empty or geom.num_coords == 0:
            geom = b.geometry
        features.append(
            {
                "type": "Feature",
                "id": b.iso_code,
                "geometry": json.loads(geom.geojson),
                "properties": {"iso": b.iso_code, "name": name_for(b.iso_code)},
            }
        )
    out = {"type": "FeatureCollection", "features": features}
    cache.set(key, out, response_cache.TTL_SECONDS)
    return out
