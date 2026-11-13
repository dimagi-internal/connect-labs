"""One opportunity end to end: where, baseline, model, cost and result, every step shown."""

from __future__ import annotations

from connect_labs.effectiveness.implementation.build import ranking
from connect_labs.effectiveness.implementation.catalog import STATUS_COST_POOLED, STATUS_MODELLED, iso3_for, models
from connect_labs.effectiveness.implementation.reach import opportunity_areas


def opportunity_detail(opp_id: int) -> dict | None:
    """One opportunity end to end: where, baseline, model, cost and result, every step shown.

    Ranked among the opportunities it shares a programme and partner with, so
    invoice pooling is the same as on the ranking page. ``None`` if it is not a
    real (non-test) opportunity.
    """
    from connect_labs.effectiveness import places, result
    from connect_labs.pulse.models import PulseOpportunity

    o = PulseOpportunity.objects.filter(opportunity_id=opp_id, is_test=False).first()
    if o is None:
        return None
    peers = PulseOpportunity.objects.filter(is_test=False, opportunity_id=opp_id)
    if o.program_id is not None and o.org_slug:
        peers = PulseOpportunity.objects.filter(is_test=False, program_id=o.program_id, org_slug=o.org_slug)
    data = ranking(peers)
    row = next(r for r in data["rows"] if r["id"] == opp_id)
    out = {
        "row": row,
        "bar_x": data["bar_x"],
        "bar_note": data["bar_note"],
        "placed_at": data["placed_at"],
        "result": None,
        "placement": None,
    }
    key, iso3 = row["model"], iso3_for(row["country"])
    if key is None or iso3 is None:
        return out
    placement = places.opportunity_places([opp_id]).get(opp_id)
    out["placement"] = (
        {"visits": placement.visits, "unplaced": placement.unplaced, "areas": len(placement.areas)}
        if placement
        else {"visits": 0, "unplaced": 0, "areas": 0}
    )
    areas = opportunity_areas([o], {}).get(opp_id)
    if not areas:
        from connect_labs.effectiveness import baseline

        areas = [
            {
                "name": row["country_name"],
                "level": "country",
                "share": 1.0,
                "inputs": baseline.Baselines([]).for_area(key, iso3, None),
            }
        ]
    total = 0.0 if row["status"] == STATUS_COST_POOLED else row["total_usd"]
    out["result"] = result.evaluate(key, iso3, areas, row["reached"], total, data["bar_x"], detail=True)
    out["model_meta"] = models()["models"][key]
    out["explore"] = explore_links(key, row, out["result"])
    return out


def explore_links(key: str, row: dict, result: dict) -> list[dict]:
    """Links into the Models explorer with this opportunity's place and its actual cost already set.

    One per area its visits fell in (a delivery across several states is valued state by state), so a
    reader can test how sensitive the answer is for a real opportunity, on the same evaluator. Pooled and
    unvalued rows have no cost of their own to carry, so they get none.
    """
    from urllib.parse import urlencode

    from django.urls import reverse

    from connect_labs.effectiveness import explore

    if row["status"] != STATUS_MODELLED or not result.get("cost_per_person"):
        return []
    links = []
    for a in result["areas"]:
        overrides = explore.delivery_overrides(
            key, {k: v["value"] for k, v in a["inputs"].items()}, result["cost_per_person"]
        )
        query = {"model": key, **{f"i_{k}": v for k, v in overrides.items()}}
        links.append(
            {
                "area": a["name"],
                "level": a["level"],
                "share": a["share"],
                "path": f"{reverse('cea:models')}?{urlencode(query)}",
            }
        )
    return links
