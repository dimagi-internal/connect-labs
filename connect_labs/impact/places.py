"""Where an opportunity's work happened: its approved visits, by the most local admin area.

Every approved visit carries GPS. Each one is placed in the most local area of
the targeting boundary set (``indicators.boundaries.owned()``: geoBoundaries
ADM1, and ADM2 where it is loaded), because that is the tessellation every
baseline figure in Labs was matched against. An area's share of an opportunity
is its share of the opportunity's approved visits.

Visits are rounded to 0.001 degrees (about 100 m) before placing, which turns
~1.9M visits into far fewer distinct points without moving any of them across
anything but a border they were already within 100 m of.

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


SCALAR_KEY = "impact_places"


def refresh(opp_ids) -> dict:
    """Place the given opportunities' visits now and store the result. Run by the timer, not by pages."""
    from django.utils import timezone

    from connect_labs.pulse.models import PulseScalar

    ids = sorted({int(i) for i in opp_ids})
    raw = _compute(ids) if ids else {}
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
    by_pk = {b.pk: b for b in AdminBoundary.objects.filter(pk__in=bids)}
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
