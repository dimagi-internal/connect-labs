"""Normalise an area-input dict into a shapely geometry.

The setup map can define a sampling area three ways, all of which arrive as one
dict in the `areas` list:

  * ``{"geometry": <GeoJSON>}``      — a drawn polygon, or a resolved admin area.
  * ``{"circle": {"lon","lat","radius_m"}}`` — "buildings around a pin".

Both frame generators (sampling, coverage) call ``resolve_area`` so the input
mode is transparent to the rest of the pipeline.
"""

from __future__ import annotations

from shapely.geometry import shape

from commcare_connect.microplans.core.geo import point_buffer


def resolve_area(a: dict):
    """Return a shapely geometry for one area dict. Raises ValueError if neither
    a GeoJSON geometry nor a circle is present."""
    if a.get("geometry"):
        return shape(a["geometry"])
    circle = a.get("circle")
    if circle:
        return point_buffer(float(circle["lon"]), float(circle["lat"]), float(circle["radius_m"]))
    raise ValueError("area requires either 'geometry' or 'circle'")
