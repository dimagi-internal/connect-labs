"""Cost-only opportunities: where the spend of an opportunity that reached no one goes."""

from __future__ import annotations


def pool_cost_only(opps, own_usd: dict, reach_of: dict):
    """Fold the spend of cost-only opportunities into the delivery they paid for.

    Some partners are paid through an opportunity that records no service at
    all: Ruwoyd's Mother Baby Wellness invoices went through "MBW Invoicing for
    Ruwoyd" ($12k, nobody reached), while its two cohort opportunities carried
    only $3k between them. Valued alone, the cohorts looked 13x better than
    they were, and topped the ranking at 349x and 274x.

    So spend on an opportunity that reached no one moves to the opportunities
    in the same programme, run by the same partner, that did reach people,
    split by people reached. Every invoice counts (owner decision, 2026-11-08).
    A cost-only opportunity with no such delivery to join keeps its own row.

    Returns (extra $ per delivery opp, delivery opp -> [source ids],
    source opp -> [delivery ids]).
    """
    groups: dict[tuple, list] = {}
    for o in opps:
        if o.program_id is not None and o.org_slug:
            groups.setdefault((o.program_id, o.org_slug), []).append(o)
    pooled_usd: dict[int, float] = {}
    pooled_from: dict[int, list] = {}
    pooled_into: dict[int, list] = {}
    for members in groups.values():
        delivery = [o for o in members if reach_of[o.opportunity_id]["reached"] > 0]
        sources = [o for o in members if reach_of[o.opportunity_id]["reached"] == 0 and own_usd[o.opportunity_id] > 0]
        if not delivery or not sources:
            continue
        people = sum(reach_of[o.opportunity_id]["reached"] for o in delivery)
        for src in sources:
            for d in delivery:
                share = own_usd[src.opportunity_id] * reach_of[d.opportunity_id]["reached"] / people
                pooled_usd[d.opportunity_id] = pooled_usd.get(d.opportunity_id, 0.0) + share
                pooled_from.setdefault(d.opportunity_id, []).append(src.opportunity_id)
            pooled_into[src.opportunity_id] = [d.opportunity_id for d in delivery]
    return pooled_usd, pooled_from, pooled_into
