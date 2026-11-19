"""The two lenses of the buildings-reached check, from household rows wherever they came from.

The rows are ``check.points_sql``-shaped and ``served`` is ``check.parse_served``'s, whether they were read from
Connect visits, an uploaded file or a CommCare project's forms, so the page, the tool and a workflow page all compute
the same.
"""

from __future__ import annotations

import logging

from connect_labs.microplans.coverage import check, population
from connect_labs.microplans.coverage.request import BadRequest

logger = logging.getLogger(__name__)


def compute_run(rows, spec, labels, fetch=None) -> dict:
    """The buildings lens from household rows, wherever they came from (Connect visits or an uploaded file).

    ``fetch(lon, lat, radius_m)`` reads the buildings of one catchment (default: the microplanner's own); a caller that
    wants to say how far along it is wraps it."""
    result = check.run_check(rows, spec, fetch)
    for area in result["areas"]:
        area["district"] = labels.get(area["group"])
    result["total"] = check.total_row(result)
    result["rollup"] = check.rollup(result, labels) if labels else []
    return result


def compute_population(rows, served, spec, labels, iso, targets) -> dict:
    """The population lens and its denominators panel from household rows and children served."""
    placed, quality = check.placed_households(rows, spec)
    if not len(placed):
        raise BadRequest("no household has a usable point, so there is nothing to place on the grid")
    units, units_error = [], None
    try:
        from connect_labs.labs.admin_boundaries import placement

        units = placement.load_units(placed, iso=iso, levels=(2,))
    except Exception as e:  # noqa: BLE001 -- boundaries are an extra denominator, never a reason to fail the lens
        logger.warning("buildings reached: no admin units for %s", iso, exc_info=True)
        units_error = str(e)[:160]
    x0, y0, x1, y1 = population.bounds_of(placed)
    for u in units:
        ux0, uy0, ux1, uy1 = u.geometry.bounds
        x0, y0, x1, y1 = min(x0, ux0), min(y0, uy0), max(x1, ux1), max(y1, uy1)
    try:
        grid = population.fetch_grid(iso, (x0, y0, x1, y1))
    except ValueError as e:
        raise BadRequest(str(e)) from e
    result = population.population_lens(placed, served, grid, labels)
    result["quality"] = quality
    result["iso"] = iso
    result["denominators"] = population.denominators(placed, served, grid, units, result, labels, targets)
    result["denominators"]["unit_level"] = "ADM2" if units else None
    if units_error or not units:
        result["denominators"]["unit_note"] = units_error or "no admin boundaries found for these households"
    return result
