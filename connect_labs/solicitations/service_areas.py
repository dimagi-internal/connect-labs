"""Where a call's work may happen, and where a respondent offers to do it.

A call limits geography in one of three ways (`area_mode`):

* ``plans`` -- it carries a micro-plan snapshot (`Solicitation.plans`); the
  respondent picks whole plans (`selected_plan_ids`). Unchanged by this module.
* ``within`` -- it names one or more admin areas (`service_area_limits`); the
  respondent picks any admin areas, at any level, that lie inside them.
* ``any`` -- neither; the respondent picks any admin areas anywhere.

Admin areas come from the microplans boundary resolver (labs' curated
boundaries first, Overture everywhere else), so a respondent can pick a state,
an LGA or a ward, whichever describes where they work.

What the browser sends is only a descriptor (name, level, source, country,
``ref``). The geometry is always resolved here, from the descriptor, so a
stored area is a real boundary and the "inside the call's areas" check runs on
geometry nobody typed. Stored areas keep a simplified copy of that geometry, as
the micro-plan snapshot does, so a response reads the same after a boundary
source is refreshed.
"""

from __future__ import annotations

import json
import logging

from django.contrib.gis.geos import GEOSGeometry
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

AREA_MODE_PLANS = "plans"
AREA_MODE_WITHIN = "within"
AREA_MODE_ANY = "any"

# A response naming more areas than this is describing a country, which it can
# do by picking the country's regions.
MAX_AREAS = 200
# Stored outlines are simplified to ~50 m: enough for a map and a containment
# test, small enough that a state does not bloat the row.
STORE_TOLERANCE = 0.0005
# How far (degrees, ~200 m) an area's interior point may sit outside a limit and
# still count as inside it: two boundary sources disagree about borders by
# about that much, and stored limits are simplified.
WITHIN_SLACK = 0.002

_DESCRIPTOR_KEYS = ("name", "level", "source", "country")


def area_mode(solicitation) -> str:
    """How this call limits where the work happens."""
    if solicitation.plans:
        return AREA_MODE_PLANS
    if solicitation.service_area_limits:
        return AREA_MODE_WITHIN
    return AREA_MODE_ANY


def area_key(desc: dict) -> str:
    """The identity of one area: its source's boundary id, else source + region + name.

    The same rule the boundary layer uses for its selection, so a pick made on
    the map and the stored area compare equal.
    """
    ref = desc.get("ref") if isinstance(desc.get("ref"), dict) else {}
    if ref.get("boundary_id"):
        return str(ref["boundary_id"])
    return f"{desc.get('source', '')}:{desc.get('region') or ref.get('region') or ''}:{desc.get('name', '')}"


def _resolve_geometry(desc: dict) -> dict | None:
    """The full-resolution GeoJSON geometry for a descriptor, from the boundary resolver."""
    from connect_labs.microplans.core.admin_boundaries import AdminArea, get_resolver

    return get_resolver().geometry(AdminArea.from_json(desc))


def _geos(geojson: dict):
    geom = GEOSGeometry(json.dumps(geojson), srid=4326)
    return geom if geom.valid else geom.buffer(0)


def _label(desc: dict) -> str:
    return str(desc.get("name") or "an area")


def normalise(raw, *, field: str) -> list[dict]:
    """Descriptors from a caller -> stored areas, each with resolved geometry.

    Raises ValidationError (keyed by ``field``) for a malformed list, too many
    areas, or an area the resolver cannot find. Duplicates are dropped; areas
    inside another picked area are dropped too (Kano State already covers every
    ward in it).
    """
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValidationError({field: "must be a list of areas"}) from e
    if not isinstance(raw, list):
        raise ValidationError({field: "must be a list of areas"})
    if len(raw) > MAX_AREAS:
        raise ValidationError({field: f"name at most {MAX_AREAS} areas; pick a larger area that contains them"})

    areas, seen = [], set()
    for i, desc in enumerate(raw):
        if not isinstance(desc, dict) or any(not desc.get(k) for k in _DESCRIPTOR_KEYS):
            raise ValidationError({field: f"area {i + 1} needs a name, level, source and country"})
        try:
            level = int(desc["level"])
        except (TypeError, ValueError) as e:
            raise ValidationError({field: f"area {i + 1} has no valid level"}) from e
        key = area_key(desc)
        if key in seen:
            continue
        seen.add(key)
        try:
            geojson = _resolve_geometry(desc)
        except Exception:  # noqa: BLE001 -- a source outage reads as "not found", logged
            logger.exception("service area geometry lookup failed (%s)", key)
            geojson = None
        if not geojson:
            raise ValidationError({field: f"could not find the boundary of {_label(desc)}; pick it again on the map"})
        full = _geos(geojson)
        areas.append(
            {
                "key": key,
                "name": str(desc["name"]),
                "level": level,
                "level_label": _level_label(level, desc["country"]),
                "source": str(desc["source"]),
                "country": str(desc["country"]),
                "region": str(desc.get("region") or ""),
                "parent_name": str(desc.get("parent_name") or ""),
                "ref": desc["ref"] if isinstance(desc.get("ref"), dict) else {},
                "geometry": json.loads(full.simplify(STORE_TOLERANCE, preserve_topology=True).geojson),
                "_full": full,
            }
        )
    kept = _drop_nested(areas)
    for area in kept:
        area.pop("_full", None)
    return kept


def _level_label(level: int, country: str) -> str:
    from connect_labs.microplans.core.admin_boundaries import level_label

    return level_label(level, country)


def _drop_nested(areas: list[dict]) -> list[dict]:
    """Drop every area that lies inside another picked area of a coarser level."""
    kept: list[dict] = []
    for area in sorted(areas, key=lambda a: a["level"]):
        point = area["_full"].point_on_surface
        if any(other["level"] < area["level"] and other["_full"].contains(point) for other in kept):
            continue
        kept.append(area)
    order = {a["key"]: i for i, a in enumerate(areas)}
    return sorted(kept, key=lambda a: order[a["key"]])


def outside_limits(areas: list[dict], limits: list[dict]) -> list[str]:
    """Names of the areas that do not lie inside any of the call's limits."""
    if not limits:
        return []
    bounds = [_geos(lim["geometry"]).buffer(WITHIN_SLACK) for lim in limits if lim.get("geometry")]
    out = []
    for area in areas:
        point = _geos(area["geometry"]).point_on_surface
        if not any(b.contains(point) for b in bounds):
            out.append(area["name"])
    return out


def check_response_areas(solicitation, areas: list[dict]) -> None:
    """Refuse areas a call does not allow: any at all on a plan call, any outside a `within` call's limits."""
    mode = area_mode(solicitation)
    if not areas:
        return
    if mode == AREA_MODE_PLANS:
        raise ValidationError({"service_areas": "this call offers its own study areas; pick from those instead"})
    if mode == AREA_MODE_WITHIN:
        outside = outside_limits(areas, solicitation.service_area_limits)
        if outside:
            limit_names = ", ".join(describe(solicitation.service_area_limits))
            verb = "is" if len(outside) == 1 else "are"
            raise ValidationError(
                {"service_areas": f"{', '.join(outside)} {verb} not inside the areas this call covers ({limit_names})"}
            )


def describe(areas: list[dict]) -> list[str]:
    """One line per area for lists and emails: "Kano (State)", "Gwale (Ward) — Kano › Gwale"."""
    lines = []
    for area in areas or []:
        line = f"{area.get('name', '')} ({area.get('level_label') or 'area'})"
        if area.get("parent_name"):
            line += f" — {area['parent_name']}"
        lines.append(line)
    return lines


def without_geometry(areas: list[dict]) -> list[dict]:
    """The areas as descriptors, for a form to send back: the geometry is re-resolved on save."""
    return [{k: v for k, v in a.items() if k != "geometry"} for a in areas or []]


def level_labels() -> dict:
    """Each country's words for levels 1-3, plus the generic ones, for the map's chips."""
    from connect_labs.microplans.core.admin_boundaries import COUNTRY_LEVEL_LABELS, LEVEL_LABELS

    return {"default": LEVEL_LABELS, **COUNTRY_LEVEL_LABELS}
