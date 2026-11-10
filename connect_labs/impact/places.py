"""Where an opportunity's work happened: its approved visits, by the most local admin area.

Placing points in admin areas has one home in labs, ``labs.admin_boundaries.placement``
(#2971), so Pulse's place names, the coverage check and this cannot disagree about
where work happened. This module only gathers an opportunity's visits and the
units to place them in:

* **Points.** Every approved visit with GPS, rounded to 0.001 degrees (about
  100 m) and weighted by how many visits share the point, which turns ~1.9M
  visits into far fewer points without moving any across anything but a border
  it was already within 100 m of.
* **Units.** The targeting boundary set (``indicators.boundaries.owned()``:
  geoBoundaries ADM1, and ADM2 where loaded), because that is the tessellation
  every baseline figure in Labs was matched against.
* **Level.** The placement core's ``finest_level``: the most local level that
  still places nearly all of the opportunity's visits. An area's share is its
  share of the visits placed at that level.

Placing every modelled opportunity takes about a minute on production
(measured 2026-11-09: 60s for 106 opportunities, 1.85M visits), far too long
for a page to wait on. So ``refresh()`` runs on a timer
(``connect_labs.impact.tasks.refresh_opportunity_places``, every six hours) and
stores the result; pages only ever read it (``opportunity_places``). An
opportunity not yet placed is valued at its country's figures, and says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import connection

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries as boundary_set

#: Relative to the best level: a level placing at least this share of what the best one places is "nearly all".
NEARLY_ALL = 0.9


@dataclass
class Area:
    boundary: AdminBoundary
    visits: int
    share: float


@dataclass
class Placement:
    areas: list[Area]
    visits: int  # approved visits with GPS
    unplaced: int  # of those, visits outside every area at the level used


_POINTS = """
SELECT opportunity_id, round(lat::numeric, 3)::float8, round(lon::numeric, 3)::float8, count(*)
FROM {events}
WHERE opportunity_id = ANY(%s)
  AND status = 'approved'
  AND lat IS NOT NULL AND lon IS NOT NULL
  AND NOT (lat = 0 AND lon = 0)
GROUP BY 1, 2, 3
"""


def _units(iso3s) -> dict[str, list]:
    """The targeting set's ADM1 and ADM2 for these countries, as placement units, by country."""
    from shapely import wkb

    from connect_labs.labs.admin_boundaries.placement import Unit

    out: dict[str, list] = {}
    for pk, iso, level, name, geom in (
        boundary_set.owned()
        .filter(iso_code__in=list(iso3s), admin_level__in=(1, 2))
        .values_list("pk", "iso_code", "admin_level", "name", "geometry")
    ):
        if geom is None:
            continue
        out.setdefault(iso, []).append(
            Unit(key=str(pk), name=name, level=level, geometry=wkb.loads(bytes(geom.wkb)), country=iso)
        )
    return out


def _compute(opps: dict[int, str]) -> dict[int, dict]:
    """``opps``: {opportunity id: its country's alpha-3}. Returns {opp: {visits, level, areas: {pk: visits}}}."""
    import pandas as pd

    from connect_labs.labs.admin_boundaries.placement import place_points
    from connect_labs.pulse.models import PulseEvent

    points: dict[int, list] = {}
    with connection.cursor() as cur:
        cur.execute(_POINTS.format(events=PulseEvent._meta.db_table), [list(opps)])
        for opp, lat, lon, n in cur.fetchall():
            points.setdefault(opp, []).append((lat, lon, int(n)))
    units = _units({opps[o] for o in points})
    out: dict[int, dict] = {}
    for opp, pts in points.items():
        df = pd.DataFrame(pts, columns=["lat", "lon", "weight"])
        visits = int(df["weight"].sum())
        placement = place_points(df, units.get(opps[opp], []))
        best = max((lp.placed_share or 0.0 for lp in placement.levels.values()), default=0.0)
        level = placement.finest_level(min_placed_share=best * NEARLY_ALL) if best else None
        areas = (
            {int(k): int(round(w)) for k, w in placement.levels[level].weights.items()} if level is not None else {}
        )
        out[opp] = {"visits": visits, "level": level, "areas": areas}
    return out


SCALAR_KEY = "impact_places"


def refresh(opps: dict) -> dict:
    """Place the given opportunities' visits now and store the result. Run by the timer, not by pages.

    ``opps``: {opportunity id: its country's alpha-3}; visits are placed only in that country's areas.
    """
    from django.utils import timezone

    from connect_labs.pulse.models import PulseScalar

    raw = _compute({int(k): v for k, v in opps.items()}) if opps else {}
    value = {
        "computed_at": timezone.now().isoformat(),
        "opps": {
            str(opp): {"visits": r["visits"], "areas": {str(b): n for b, n in r["areas"].items()}}
            for opp, r in raw.items()
        },
    }
    PulseScalar.objects.update_or_create(key=SCALAR_KEY, defaults={"value": value})
    return value


def placed_at() -> str | None:
    """When the stored placement was computed, or None if it never has been."""
    from connect_labs.pulse.models import PulseScalar

    row = PulseScalar.objects.filter(key=SCALAR_KEY).values_list("value", flat=True).first()
    return (row or {}).get("computed_at")


def opportunity_places(opp_ids) -> dict[int, Placement]:
    """Each opportunity's approved visits by most local area, largest share first, from the stored placement.

    An opportunity that has not been placed yet, or has no approved visits with
    GPS, is absent; the caller values it at its country's figures and says so.
    """
    from connect_labs.pulse.models import PulseScalar

    stored = (PulseScalar.objects.filter(key=SCALAR_KEY).values_list("value", flat=True).first() or {}).get("opps", {})
    wanted = {str(int(i)) for i in opp_ids}
    raw = {int(k): v for k, v in stored.items() if k in wanted}
    bids = {int(bid) for r in raw.values() for bid in r["areas"]}
    by_pk = {b.pk: b for b in AdminBoundary.objects.filter(pk__in=bids).defer("geometry")}
    out: dict[int, Placement] = {}
    for opp, r in raw.items():
        counts = {int(b): n for b, n in r["areas"].items()}
        placed = sum(counts.values())
        areas = [
            Area(boundary=by_pk[bid], visits=n, share=n / placed)
            for bid, n in sorted(counts.items(), key=lambda kv: -kv[1])
            if bid in by_pk
        ]
        out[opp] = Placement(areas=areas, visits=r["visits"], unplaced=r["visits"] - placed)
    return out
