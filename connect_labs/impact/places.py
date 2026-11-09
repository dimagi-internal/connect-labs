"""Where an opportunity's work happened: its approved visits, by the most local admin area.

Every approved visit carries GPS. Each one is placed in the most local area of
the targeting boundary set (``indicators.boundaries.owned()``: geoBoundaries
ADM1, and ADM2 where it is loaded), because that is the tessellation every
baseline figure in Labs was matched against. An area's share of an opportunity
is its share of the opportunity's approved visits.

Visits are rounded to 0.001 degrees (about 100 m) before placing, which turns
~1.9M visits into far fewer distinct points without moving any of them across
anything but a border they were already within 100 m of.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache
from django.db import connection
from django.db.models import Max

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries as boundary_set

CACHE_SECONDS = 6 * 3600


@dataclass
class Area:
    boundary: AdminBoundary
    visits: int
    share: float


@dataclass
class Placement:
    areas: list[Area]
    visits: int  # approved visits with GPS
    unplaced: int  # of those, visits outside every boundary in the set


_SQL = """
WITH pts AS (
    SELECT opportunity_id AS opp,
           round(lat::numeric, 3)::float8 AS lat,
           round(lon::numeric, 3)::float8 AS lon,
           count(*) AS n
    FROM {events}
    WHERE opportunity_id = ANY(%s)
      AND status = 'approved'
      AND lat IS NOT NULL AND lon IS NOT NULL
      AND NOT (lat = 0 AND lon = 0)
    GROUP BY 1, 2, 3
),
hit AS (
    SELECT DISTINCT ON (p.opp, p.lat, p.lon) p.opp, p.n, b.id AS bid
    FROM pts p
    JOIN {boundaries} b
      ON b.source = %s
     AND b.admin_level IN (1, 2)
     AND ST_Covers(b.geometry, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326))
    ORDER BY p.opp, p.lat, p.lon, b.admin_level DESC
)
SELECT 'placed' AS kind, opp, bid, sum(n)::bigint FROM hit GROUP BY opp, bid
UNION ALL
SELECT 'total', opp, NULL, sum(n)::bigint FROM pts GROUP BY opp
"""


def _compute(opp_ids: list[int]) -> dict[int, dict]:
    from connect_labs.pulse.models import PulseEvent

    sql = _SQL.format(events=PulseEvent._meta.db_table, boundaries=AdminBoundary._meta.db_table)
    placed: dict[int, dict[int, int]] = {}
    total: dict[int, int] = {}
    with connection.cursor() as cur:
        cur.execute(sql, [list(opp_ids), boundary_set.SOURCE])
        for kind, opp, bid, n in cur.fetchall():
            if kind == "placed":
                placed.setdefault(opp, {})[bid] = int(n)
            else:
                total[opp] = int(n)
    return {opp: {"areas": placed.get(opp, {}), "visits": total.get(opp, 0)} for opp in set(total) | set(placed)}


def opportunity_places(opp_ids) -> dict[int, Placement]:
    """Each opportunity's approved visits by most local area, largest share first.

    An opportunity with no approved visits carrying GPS is absent from the result;
    the caller values it at its country's figures and says so.
    """
    from connect_labs.pulse.models import PulseEvent

    ids = sorted({int(i) for i in opp_ids})
    if not ids:
        return {}
    stamp = PulseEvent.objects.aggregate(m=Max("id"))["m"] or 0
    key = f"impact:places:{stamp}:{hash(tuple(ids))}"
    raw = cache.get(key)
    if raw is None:
        raw = _compute(ids)
        cache.set(key, raw, CACHE_SECONDS)

    bids = {bid for r in raw.values() for bid in r["areas"]}
    by_pk = {b.pk: b for b in AdminBoundary.objects.filter(pk__in=bids)}
    out: dict[int, Placement] = {}
    for opp, r in raw.items():
        placed = sum(r["areas"].values())
        areas = [
            Area(boundary=by_pk[bid], visits=n, share=n / placed)
            for bid, n in sorted(r["areas"].items(), key=lambda kv: -kv[1])
            if bid in by_pk
        ]
        out[opp] = Placement(areas=areas, visits=r["visits"], unplaced=r["visits"] - placed)
    return out
