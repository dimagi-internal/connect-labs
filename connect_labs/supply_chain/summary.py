"""The chain, counted by stage.

What the landing view reads. Every figure here is a **state** -- how many
records sit at each stage of source-to-contract-to-deliver -- which is a
different kind of thing from a check (see checks.py): a state is a count of
rows, a check is a gap or a contradiction. Keeping them apart is what lets
the landing page carry both without either pretending to be the other.

Counts, not quantities, wherever a quantity would need a unit this cannot
know. Three tenders for three different commodities have no meaningful total
quantity, and inventing one by adding cartons to vials is the failure the
rest of this domain refuses. Pass a `commodity_slug` and the quantities come
back too, because then the unit is knowable.

Nothing here ranks or judges. "6 of 7 awaiting a reply" is a fact; whether
that is a problem depends on when they were sent and what the programme
expects, which is a client's call.
"""

from django.db.models import Count

from connect_labs.supply_chain import standing
from connect_labs.supply_chain.models import (
    Award,
    Contract,
    Distribution,
    Invoice,
    Movement,
    Outreach,
    Quote,
    Receipt,
    Shipment,
    SupplyPoint,
    Tender,
)
from connect_labs.supply_chain.procurement.services.comparison import compare_tender
from connect_labs.supply_chain.stock.services import ledger, network, resupply
from connect_labs.supply_chain.values import unconfirmed


def _source(access, commodity=None):
    program_id = access.program_id
    tenders = Tender.objects.filter(program_id=program_id)
    invitations = Outreach.objects.filter(tender__program_id=program_id)
    quotes = Quote.objects.filter(tender__program_id=program_id, voided=False, superseded_by__isnull=True)
    if commodity is not None:
        quotes = quotes.filter(commodity=commodity)

    comparable = total = 0
    provisional = False
    # Which tenders the evaluation counts across, so "2 of 4 comparable" can
    # say it is the program's total and not one round's.
    evaluated = set()
    # Each tender's own count, so the program's total can be read back to the
    # "1 of 3" one round's comparison page says: {tender_pk: [label, comparable, of, awarded]}.
    by_tender = {}
    contracted = set(tenders.filter(contracts__isnull=False).values_list("pk", flat=True))
    # An awarded tender was still evaluated: leaving it out read "Evaluation 0
    # of 0 comparable" beside "Award 3" on the tender those awards came from.
    for tender in tenders.filter(status__in=("open", "closed", "awarded")):
        for line in tender.lines or []:
            slug = line.get("commodity_slug")
            if commodity is not None and slug != commodity.slug:
                continue
            line_commodity = access.get_commodity(slug) if slug else None
            if line_commodity is None:
                continue
            live = [q for q in access.list_quotes(tender_id=tender.pk) if q.commodity_id == line_commodity.pk]
            if not live:
                continue
            comparison = compare_tender(
                tender,
                line_commodity,
                live,
                {s.pk: s for s in access.list_suppliers()},
                items_by_id=access.items_by_id(),
            )
            comparable += comparison.comparable_count
            total += comparison.total_count
            evaluated.add(tender.pk)
            entry = by_tender.setdefault(
                tender.pk, [tender.label or f"tender {tender.pk}", 0, 0, tender.status == "awarded"]
            )
            entry[1] += comparison.comparable_count
            entry[2] += comparison.total_count
            # An award that was ordered (a contract signed on it) is no longer
            # provisional, as the overview's own tender row says.
            provisional = provisional or (comparison.provisional and tender.pk not in contracted)

    awards = Award.objects.filter(tender__program_id=program_id)
    awaiting = standing.awaiting_reply(program_id)
    return {
        "demand": {
            "tenders": tenders.count(),
            "open": tenders.filter(status="open").count(),
            "draft": tenders.filter(status="draft").count(),
        },
        "rfq_issued": {
            "invitations": invitations.count(),
            # The silent suppliers the overview's table lists (standing.awaiting_reply): on rounds still
            # being chased, not every unanswered invitation ever sent.
            "awaiting_reply": awaiting["suppliers"],
            "awaiting_tenders": awaiting["tenders"],
            "tenders": invitations.values("tender_id").distinct().count(),
        },
        "quotations": {
            "live": quotes.count(),
            "tenders": quotes.values("tender_id").distinct().count(),
            # Each tender's own live quotes, newest tender first, so the total
            # reads back to each round's own reply count.
            "by_tender": [
                {"label": row["tender__label"] or f"tender {row['tender_id']}", "live": row["n"]}
                for row in quotes.values("tender_id", "tender__label").annotate(n=Count("id")).order_by("-tender_id")
            ],
        },
        "evaluation": {
            "comparable": comparable,
            "of": total,
            "provisional": provisional,
            "tenders": len(evaluated),
            # Newest tender first: "RUTF round 2: 1 of 3 · RUTF round 1: awarded".
            "by_tender": [
                {"label": label, "comparable": c, "of": of, "awarded": awarded}
                for _, (label, c, of, awarded) in sorted(by_tender.items(), reverse=True)
            ],
        },
        "award": {
            "count": awards.count(),
            "provisional": awards.filter(provisional=True).exclude(tender_id__in=contracted).count(),
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
            "in_transit": shipments.filter(Shipment.in_transit_q()).count(),
            "whereabouts": _whereabouts(contracts, shipments),
        },
        "received": {"receipts": receipts.count()},
        "invoiced": {
            "count": invoices.count(),
            # Part paid is not unpaid: money has gone against it (an advance it
            # acknowledged, an instalment). Counted apart so "1 unpaid" does not
            # say nothing was paid.
            "unpaid": invoices.exclude(status__in=("paid", "rejected", "part_paid")).count(),
            "part_paid": invoices.filter(status="part_paid").count(),
        },
    }


def _whereabouts(contracts, shipments) -> dict[str, int]:
    """Shipments on the road counted by where they are, worded as the order page words it.

    "1 at customs — held, waiting on us", not "1 in transit", when the order's
    own page says the goods are held at customs on us
    (records.shipment_whereabouts). Plainly moving ones stay "in transit".
    """
    from connect_labs.supply_chain.fulfilment.services.holds import holds_for
    from connect_labs.supply_chain.records import shipment_whereabouts

    moving = list(shipments.filter(Shipment.in_transit_q()).only("pk", "status", "contract_id"))
    if not moving:
        return {}
    holds = holds_for(list(contracts.filter(pk__in={s.contract_id for s in moving})))
    counts: dict[str, int] = {}
    for shipment in moving:
        if shipment.status in ("at_customs", "cleared"):
            words = shipment_whereabouts(shipment.status, bool(holds.get(shipment.contract_id)))
        else:
            words = "in transit"
        counts[words] = counts.get(words, 0) + 1
    return counts


def _network_total(movements, points, item, disagreement):
    """The network's own balance, or the reason it is not one number.

    When two trade items under a commodity are packed differently, a total
    spanning both is not a quantity anyone can act on -- and saying "no pack
    specification" would be wrong, because each item states one. The reason
    names the actual problem: they disagree.
    """
    if disagreement is not None:
        return disagreement
    return ledger.collapse(_network_balance(movements, points), item, None)


def _deliver(access, commodity=None, item=None, opportunity_id=None, disagreement=None):
    program_id = access.program_id
    # The in-transit point is where the ledger parks a consignment on the
    # road -- not a place stock rests, so not one of the network's places.
    points = SupplyPoint.objects.filter(program_id=program_id, status="active").exclude(kind="in_transit")
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
        on_hand = _network_total(movements, points, item, disagreement)
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
    # Only meaningful with exactly one trade item: with two that are packed
    # differently there is no single conversion, so a network total spanning
    # both is not a quantity. The reason names the disagreement rather than
    # claiming nobody stated a pack size -- each item did.
    item = items[0] if len(items) == 1 else None
    disagreement = None
    packs = {i.base_per_pack for i in items if i.base_per_pack}
    if len(packs) > 1:
        described = " and ".join(
            f"{i.name} is {i.base_per_pack} to the {i.pack_unit or 'pack'}" for i in items if i.base_per_pack
        )
        disagreement = unconfirmed(
            f"{described} — a network total spanning both is not one quantity. "
            "Choose a trade item on the Stock page."
        )

    return {
        "commodity_slug": commodity_slug,
        "opportunity_id": opportunity_id,
        "source": _source(access, commodity=commodity),
        "order": _order(access, commodity=commodity),
        "deliver": _deliver(
            access,
            commodity=commodity,
            item=item,
            opportunity_id=opportunity_id,
            disagreement=disagreement,
        ),
    }
