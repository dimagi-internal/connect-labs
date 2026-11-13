"""Building the ranking: every real opportunity valued, pooled, placed and ordered."""

from __future__ import annotations

from connect_labs.effectiveness.implementation.catalog import (
    STATUS_COST_POOLED,
    STATUS_MODELLED,
    STATUS_NO_COUNTRY,
    STATUS_NO_MODEL,
    STATUS_NOTHING_DELIVERED,
    STATUS_TEXT,
    models,
)
from connect_labs.effectiveness.implementation.delivery import value
from connect_labs.effectiveness.implementation.pooling import pool_cost_only
from connect_labs.effectiveness.implementation.reach import centres, opportunity_areas, people_reached
from connect_labs.effectiveness.implementation.totals import programmes, rollup
from connect_labs.pulse import costs
from connect_labs.pulse.normalize import COUNTRY_NAMES, service_label

RANKING_CACHE_KEY = "pulse:cea:ranking:v1"


RANKING_CACHE_SECONDS = 60


def ranking(opps_qs=None) -> dict:
    """Every real opportunity, modelled ones ranked by multiple at their actual cost."""
    from connect_labs.pulse.models import PulseOpportunity

    if opps_qs is None:
        opps_qs = PulseOpportunity.objects.filter(is_test=False)
    opps = list(opps_qs)
    ids = [o.opportunity_id for o in opps]
    cost_of = costs.opportunity_costs(ids)
    reached_of = people_reached(ids)
    centre_of = centres(ids)
    m = models()

    own_usd = {
        o.opportunity_id: (float(cost_of[o.opportunity_id].total_usd) if o.opportunity_id in cost_of else 0.0)
        for o in opps
    }
    reach_of = {
        o.opportunity_id: reached_of.get(o.opportunity_id)
        or {"reached": 0, "reach_basis": None, "works_per_person": None}
        for o in opps
    }
    pooled_usd, pooled_from, pooled_into = pool_cost_only(opps, own_usd, reach_of)
    areas_of = opportunity_areas(opps, reach_of)

    rows = []
    for o in opps:
        total = own_usd[o.opportunity_id] + pooled_usd.get(o.opportunity_id, 0.0)
        c = cost_of.get(o.opportunity_id)
        reach = reach_of[o.opportunity_id]
        reached = reach["reached"]
        country = (o.country or "").upper()
        loc = m["countries"].get(country)
        centre = centre_of.get(o.opportunity_id)
        row = {
            "id": o.opportunity_id,
            "name": o.name or f"Opportunity {o.opportunity_id}",
            "org_slug": o.org_slug,
            "program_id": o.program_id,
            "service": o.service_slug or "other",
            "service_name": service_label(o.service_slug),
            "country": country,
            "country_name": (loc or {}).get("name") or COUNTRY_NAMES.get(country) or country or "Unknown",
            "lat": centre[0] if centre else None,
            "lon": centre[1] if centre else None,
            "active": bool(o.is_active),
            "end_date": o.end_date.isoformat() if o.end_date else None,
            "total_usd": round(total, 2),
            "own_usd": round(own_usd[o.opportunity_id], 2),
            "pooled_usd": round(pooled_usd.get(o.opportunity_id, 0.0), 2),
            "pooled_from": pooled_from.get(o.opportunity_id, []),
            "pooled_into": pooled_into.get(o.opportunity_id, []),
            "fixed_usd": round(float(c.fixed_usd), 2) if c else 0.0,
            "reached": reached,
            "reach_basis": reach["reach_basis"],
            "works_per_person": reach["works_per_person"],
        }
        if o.opportunity_id in pooled_into:
            # Its spend is already inside the delivery rows above; valuing it again would count it twice.
            row.update(value(row["service"], country, 0.0, 0))
            row["status"] = STATUS_COST_POOLED
        else:
            row.update(value(row["service"], country, total, reached, areas_of.get(o.opportunity_id)))
        row["areas_n"] = len(areas_of.get(o.opportunity_id) or [])
        row["status_text"] = STATUS_TEXT[row["status"]]
        rows.append(row)

    order = {
        STATUS_MODELLED: 0,
        STATUS_NOTHING_DELIVERED: 1,
        STATUS_NO_COUNTRY: 2,
        STATUS_NO_MODEL: 3,
        STATUS_COST_POOLED: 4,
    }
    rows.sort(key=lambda r: (order[r["status"]], -(r["x"] or 0), -r["total_usd"]))
    rank = 0
    for r in rows:
        if r["status"] == STATUS_MODELLED:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None

    return {
        "rows": rows,
        "totals": rollup(rows),
        "programmes": programmes(rows),
        "bar_x": m["bar_x"],
        "bar_note": m["bar_note"],
        "placed_at": placed_at(),
        "models": {
            k: dict(v, price=next(iter(m["countries"].values()))[k]["unit_cost"]) for k, v in m["models"].items()
        },
        "source": m["source"],
        "generated": m["generated"],
    }


def placed_at():
    from connect_labs.effectiveness import places

    return places.placed_at()


def cached_ranking() -> dict:
    """The ranking, kept for a minute: a slider moved ten times a second must not read the database ten times."""
    from django.core.cache import cache

    return cache.get_or_set(RANKING_CACHE_KEY, ranking, RANKING_CACHE_SECONDS)
