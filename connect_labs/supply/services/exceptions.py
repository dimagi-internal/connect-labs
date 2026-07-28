"""The command-centre exception queue, ranked by children rather than tonnage.

Four different things can be wrong — a consignment is late, a receipt came up
short, stock will expire before it can be used, or a partner has said they are
going to run out — and until now they were ranked against each other by
whatever number each happened to have. Tonnage times lateness for one, raw
carton shortfall doubled for another. Those are not comparable, so the ordering
of the queue was arbitrary at exactly the point it mattered most.

**Every row here carries the same unit: children who miss a full course of
treatment.** That is the only quantity all four kinds share, it is the one a
programme director is actually deciding between, and it makes the ordering
defensible — an expiring batch outranks a late truck when more children are
behind it, and not otherwise.

Each row also carries its ``derivation``, because a severity ranking nobody can
reconstruct is a severity ranking nobody will act against.
"""
from datetime import date

from .. import gs1
from ..models import Discrepancy, Shipment, ShortfallSignal
from . import cover


def _late_shipments(contracts=None):
    qs = Shipment.objects.exclude(status=Shipment.Status.CONFIRMED).select_related("destination", "contract__org")
    if contracts is not None:
        qs = qs.filter(contract__in=contracts)
    return qs.prefetch_related("milestones__node")


def _delay_days(shipment):
    """Days behind plan on the arrival leg, or 0."""
    worst = 0
    for milestone in shipment.milestones.all():
        delta = milestone.delta_days
        if delta and delta > worst:
            worst = delta
    return worst


def late_exceptions(contracts=None, as_of=None):
    rows = []
    for shipment in _late_shipments(contracts):
        delay = _delay_days(shipment)
        if delay <= 0:
            continue
        destination = shipment.destination
        at_risk = cover.children_at_risk(destination, delay_days=delay, as_of=as_of)
        node_cover = cover.cover_for_node(destination, as_of=as_of)
        rows.append(
            {
                "key": f"late-{shipment.id}",
                "kind": "Late",
                "origin": "derived",
                "tone": "bad" if at_risk else "warn",
                "shipment_id": shipment.id,
                "shipment_reference": shipment.reference,
                "node_id": destination.id,
                "node_name": destination.name,
                "children_at_risk": at_risk,
                "by_date": node_cover["stockout_on"] if node_cover else None,
                "what": (
                    f"{at_risk:,} children lose a full course at {destination.name}"
                    if at_risk
                    else f"{shipment.reference} is {delay:.0f} days behind plan"
                ),
                "why": (
                    f"{shipment.reference} is {delay:.0f} days behind the plan it was awarded "
                    f"against, moving {shipment.origin.name} to {destination.name}."
                ),
                "action": "Expedite the consignment, or reallocate from a node holding surplus.",
                "derivation": (
                    f"{delay:.0f} days late against "
                    f"{node_cover['weeks_of_cover'] if node_cover else 0} weeks of cover; "
                    f"the days after the store runs dry x the admission rate."
                ),
            }
        )
    return rows


def discrepancy_exceptions(as_of=None):
    rows = []
    for discrepancy in Discrepancy.objects.filter(status=Discrepancy.Status.OPEN).select_related(
        "shipment__destination"
    ):
        short = int(discrepancy.shortfall or 0)
        children = gs1.cartons_to_children(short)
        node = discrepancy.shipment.destination
        rows.append(
            {
                "key": f"disc-{discrepancy.id}",
                "kind": "Short receipt",
                "origin": "derived",
                "tone": "bad",
                "discrepancy_id": discrepancy.id,
                "shipment_reference": discrepancy.shipment.reference,
                "node_id": node.id,
                "node_name": node.name,
                "children_at_risk": children,
                "by_date": None,
                "what": f"{short:,} cartons short at {node.name}",
                "why": (
                    f"{int(discrepancy.expected_quantity):,} cartons despatched against "
                    f"{int(discrepancy.received_quantity):,} counted at destination."
                ),
                "action": "Reconcile against the despatch advice, then record the outcome to close it.",
                "derivation": f"{short:,} cartons short, at one carton per child's full course.",
            }
        )
    return rows


def expiry_exceptions(as_of=None):
    rows = []
    for node in cover.demand_serving_nodes():
        risk = cover.expiry_risk(node, as_of=as_of)
        if risk is None:
            continue
        rows.append(
            {
                "key": f"expiry-{node.id}",
                "kind": "Expiry risk",
                "origin": "derived",
                "tone": "warn",
                "node_id": node.id,
                "node_name": node.name,
                "children_at_risk": risk["children_equivalent"],
                "by_date": risk["expires_on"],
                "what": f"{risk['cartons_at_risk']:,} cartons at {node.name} expire before they can be used",
                "why": (
                    f"Stock at {node.name} exceeds what the caseload it serves can consume "
                    f"before {risk['expires_on']}."
                ),
                "action": "Reallocate the surplus to a node with cover below plan.",
                "derivation": (
                    "Cartons held at the node minus what its weekly burn can consume " "before the batch expiry date."
                ),
            }
        )
    return rows


def partner_signal_exceptions(as_of=None):
    """Shortfalls the partners raised themselves.

    Kept a separate kind, and marked ``origin: partner``, because the
    difference is the point. A centre that only shows alerts it derived is
    running a monitoring product; one that shows what the people holding the
    cartons reported is running a coordination product.
    """
    rows = []
    signals = ShortfallSignal.objects.exclude(status=ShortfallSignal.Status.RESOLVED).select_related("site", "org")
    for signal in signals:
        rows.append(
            {
                "key": f"signal-{signal.id}",
                "kind": "Partner shortfall",
                "origin": "partner",
                "tone": "bad",
                "signal_id": signal.id,
                "node_id": signal.site_id,
                "node_name": signal.site.name,
                "org_name": signal.org.legal_name,
                "raised_on": signal.raised_on.isoformat(),
                "children_at_risk": signal.children_affected,
                "by_date": signal.needed_by.isoformat(),
                "what": (
                    f"{signal.children_affected:,} children at {signal.site.name} " f"by {signal.needed_by:%-d %B}"
                ),
                "why": signal.note
                or f"{signal.org.legal_name} reported a shortfall of {int(signal.cartons_short):,} cartons.",
                "action": "Reallocate from a node holding surplus, or expedite the next consignment.",
                "derivation": (
                    f"Reported by {signal.org.legal_name} on {signal.raised_on:%-d %B} "
                    f"from their own distribution calendar."
                ),
            }
        )
    return rows


def build_queue(contracts=None, as_of=None):
    """Every exception, ranked by children at risk, worst first."""
    as_of = as_of or date.today()
    rows = (
        late_exceptions(contracts=contracts, as_of=as_of)
        + discrepancy_exceptions(as_of=as_of)
        + expiry_exceptions(as_of=as_of)
        + partner_signal_exceptions(as_of=as_of)
    )
    return sorted(rows, key=lambda r: (-(r["children_at_risk"] or 0), r["key"]))
