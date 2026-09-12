"""The chain, counted by stage.

What the landing view reads. Every figure here is a **state** -- how many
records sit at each stage of source-to-contract-to-deliver -- which is a
different kind of thing from a check (see checks.py): a state is a count of
rows, a check is a gap or a contradiction. Keeping them apart is what lets
the landing page carry both without either pretending to be the other.

Counts, not quantities, wherever a quantity would need a unit this cannot
know. Three rounds for three different commodities have no meaningful total
quantity, and inventing one by adding cartons to vials is the failure the
rest of this domain refuses. Pass a `commodity_slug` and the quantities come
back too, because then the unit is knowable.

Nothing here ranks or judges. "6 of 7 awaiting a reply" is a fact; whether
that is a problem depends on when they were sent and what the programme
expects, which is a client's call.
"""

from connect_labs.supply_chain.models import (
    Award,
    Contract,
    Distribution,
    Invoice,
    Movement,
    Outreach,
    Quote,
    Receipt,
    Round,
    Shipment,
    SupplyPoint,
)
from connect_labs.supply_chain.procurement.services.comparison import compare_round
from connect_labs.supply_chain.stock.services import ledger, network, resupply


def _source(access, commodity=None):
    program_id = access.program_id
    rounds = Round.objects.filter(program_id=program_id)
    invitations = Outreach.objects.filter(round__program_id=program_id)
    quotes = Quote.objects.filter(round__program_id=program_id, voided=False, superseded_by__isnull=True)
    if commodity is not None:
        quotes = quotes.filter(commodity=commodity)

    comparable = total = 0
    provisional = False
    for round_ in rounds.filter(status__in=("open", "closed")):
        for line in round_.lines or []:
            slug = line.get("commodity_slug")
            if commodity is not None and slug != commodity.slug:
                continue
            line_commodity = access.get_commodity(slug) if slug else None
            if line_commodity is None:
                continue
            live = [q for q in access.list_quotes(round_id=round_.pk) if q.commodity_id == line_commodity.pk]
            if not live:
                continue
            comparison = compare_round(
                round_,
                line_commodity,
                live,
                {s.pk: s for s in access.list_suppliers()},
                items_by_id=access.items_by_id(),
            )
            comparable += comparison.comparable_count
            total += comparison.total_count
            provisional = provisional or comparison.provisional

    awards = Award.objects.filter(round__program_id=program_id)
    return {
        "demand": {
            "rounds": rounds.count(),
            "open": rounds.filter(status="open").count(),
            "draft": rounds.filter(status="draft").count(),
        },
        "rfq_issued": {
            "invitations": invitations.count(),
            "awaiting_reply": invitations.filter(responded=False).count(),
        },
        "quotations": {"live": quotes.count()},
        "evaluation": {"comparable": comparable, "of": total, "provisional": provisional},
        "award": {
            "count": awards.count(),
            "provisional": awards.filter(provisional=True).count(),
        },
    }


def _order(access, commodity=None):
    program_id = access.program_id
    contracts = Contract.objects.filter(program_id=program_id)
    if commodity is not None:
        contracts = contracts.filter(commodity=commodity)
    shipments = Shipment.objects.filter(contract__in=contracts)
    receipts = Receipt.objects.filter(contract__in=contracts)
    invoices = Invoice.objects.filter(contract__in=contracts)

    by_buyer = {}
    for contract in contracts:
        by_buyer[contract.buyer_of_record] = by_buyer.get(contract.buyer_of_record, 0) + 1

    from connect_labs.supply_chain.records import IN_TRANSIT_STATUSES

    return {
        "contract": {
            "count": contracts.count(),
            # Who is actually buying. Surfaced at the top level because it
            # changes the landed cost, so a reader comparing totals across
            # contracts needs to know the mix.
            "by_buyer_of_record": by_buyer,
            "without_reference": contracts.exclude(status__in=("closed", "cancelled")).filter(reference="").count(),
        },
        "dispatched": {
            "shipments": shipments.count(),
            "in_transit": shipments.filter(status__in=IN_TRANSIT_STATUSES).count(),
        },
        "received": {"receipts": receipts.count()},
        "invoiced": {
            "count": invoices.count(),
            "unpaid": invoices.exclude(status__in=("paid", "rejected")).count(),
        },
    }


def _deliver(access, commodity=None, item=None, opportunity_id=None):
    program_id = access.program_id
    points = SupplyPoint.objects.filter(program_id=program_id, status="active")
    if opportunity_id is not None:
        points = points.filter(opportunity_id=opportunity_id)

    movements = Movement.objects.for_program(program_id)
    if commodity is not None:
        movements = movements.filter(commodity=commodity)
    if opportunity_id is not None:
        movements = movements.for_opportunity(opportunity_id)

    rows = network.network_stock(program_id, opportunity_id=opportunity_id, item=item)
    statuses: dict[str, int] = {}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1

    on_hand = in_transit = consumed = None
    if commodity is not None:
        # A unit is only knowable once a commodity is named, so the
        # quantities appear only then rather than as a meaningless total.
        on_hand = ledger.collapse(
            {unit: amount for unit, amount in _network_balance(movements, points).items()},
            item,
            None,
        )
        in_transit = ledger.in_transit(program_id, item=item)
        consumed = ledger.collapse(movements.consumption_by_unit(), item, None)

    return {
        "network": {
            "supply_points": points.count(),
            "user_held": points.filter(kind="user_held").count(),
            "never_reported": sum(1 for row in rows if row["reported"] is None),
        },
        "on_hand": on_hand,
        "in_transit": in_transit,
        "distributions": {"runs": Distribution.objects.filter(program_id=program_id).count()},
        "consumed": consumed,
        # From each point's OWN min/max band, so "below minimum" means below
        # what this programme set, not below a number chosen here.
        "cover": statuses,
        "amc_window_days": resupply.DEFAULT_WINDOW_DAYS,
    }


def _network_balance(movements, points):
    """{unit: total} held anywhere in the network.

    Inbound from outside the network minus outbound to outside it: a transfer
    between two points we hold nets to nothing, so summing every point's
    balance would be right but summing every movement would not.
    """
    from django.db.models import Sum

    ids = set(points.values_list("pk", flat=True))
    totals: dict[str, object] = {}
    for row in movements.values("quantity_unit", "to_supply_point_id", "from_supply_point_id").annotate(
        total=Sum("quantity")
    ):
        unit = row["quantity_unit"]
        inside_to = row["to_supply_point_id"] in ids
        inside_from = row["from_supply_point_id"] in ids
        if inside_to and not inside_from:
            totals[unit] = totals.get(unit, 0) + row["total"]
        elif inside_from and not inside_to:
            totals[unit] = totals.get(unit, 0) - row["total"]
    return totals


def chain_summary(access, *, commodity_slug=None, opportunity_id=None) -> dict:
    """Stage counts across source, order and deliver."""
    commodity = access.get_commodity(commodity_slug) if commodity_slug else None
    if commodity_slug and commodity is None:
        raise ValueError(f"commodity {commodity_slug!r} does not exist")

    items = [i for i in access.list_items() if commodity and i.commodity_id == commodity.pk]
    # Only meaningful with exactly one trade item: with two that disagree on
    # pack configuration there is no single conversion, and the ledger says so.
    item = items[0] if len(items) == 1 else None

    return {
        "commodity_slug": commodity_slug,
        "opportunity_id": opportunity_id,
        "source": _source(access, commodity=commodity),
        "order": _order(access, commodity=commodity),
        "deliver": _deliver(access, commodity=commodity, item=item, opportunity_id=opportunity_id),
    }
