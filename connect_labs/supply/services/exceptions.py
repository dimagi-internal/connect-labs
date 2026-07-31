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
from datetime import date, timedelta

from .. import gs1
from ..models import Discrepancy, Shipment, ShortfallSignal, SupplyAction
from . import cover

# How long a resolved partner signal stays on the queue carrying its resolution.
# Long enough that the close is visible to anyone who looks at the screen after
# the decision, short enough that the queue does not become a history.
RESOLVED_SIGNAL_VISIBLE_DAYS = 7

# The window a decision taken today can still affect. Cartons reallocated now
# take about a week to land, so a month is roughly two chances to act; harm
# falling outside it is real but is not what this worklist is for.
DECISION_HORIZON_DAYS = 30


def _children_within_horizon(row, as_of):
    """The children this row costs INSIDE the decision horizon.

    A row with no date has already happened — a short receipt is counted, not
    pending — so it spends its whole figure. A row dated beyond the horizon
    spends none of it: the harm is real and stays on the row, it simply stops
    competing for this month's attention with something happening next week.
    """
    at_risk = row.get("children_at_risk") or 0
    by_date = row.get("by_date")
    if not by_date:
        return at_risk
    try:
        due = date.fromisoformat(str(by_date)[:10])
    except ValueError:
        return at_risk
    return at_risk if due <= as_of + timedelta(days=DECISION_HORIZON_DAYS) else 0


def _days(n):
    """``6 days`` / ``1 day`` — a count with a unit that agrees with it.

    Every day-count on this screen went through an f-string with a hardcoded
    plural, so a one-day delay read "1 days behind plan" on the highest-ranked
    card in the product.
    """
    n = int(round(n))
    return f"{n} day" if n == 1 else f"{n} days"


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
                # ``what`` states the PHYSICAL fact in its own unit; the row's
                # children figure is rendered once, by the risk line beneath it.
                # Both used to say "N children lose a full course", so the
                # highest-ranked card in the product printed its own headline
                # number twice, three lines apart, in the same words.
                "what": (
                    f"{shipment.reference} is {_days(delay)} late into {destination.name}"
                    if at_risk
                    else f"No children go without at {destination.name}, despite being {_days(delay)} late"
                ),
                "why": (
                    f"{shipment.reference} is {_days(delay)} behind the plan it was awarded "
                    f"against, moving {shipment.origin.name} to {destination.name}."
                    + (
                        ""
                        if at_risk
                        else (
                            f" {destination.name} is holding "
                            f"{node_cover['weeks_of_cover'] if node_cover else 0} weeks of cover, "
                            f"so the delay is absorbed before anybody misses a course."
                        )
                    )
                ),
                # A row that costs nobody a course is not a worklist item.
                #
                # It recommended "expedite the consignment, or reallocate from a
                # node holding surplus" over its own sentence saying the delay
                # is absorbed before anybody misses a course — so the queue
                # advised spending a decision on the one row it had just proved
                # needs none. Ranking on children and then prescribing action
                # regardless throws away what the ranking bought.
                "monitor_only": not at_risk,
                "action": (
                    "Expedite the consignment, or reallocate from a node holding surplus."
                    if at_risk
                    else "No action needed — the destination absorbs this delay. Watch it in case the delay grows."
                ),
                "derivation": (
                    f"{_days(delay)} late against "
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
                # This row's node is the SOURCE of the move it advises, not the
                # destination. Every other exception names a node that needs
                # cartons; this one names a node holding more than it can use
                # before they expire. The queue offered "Reallocate to Djibo" on
                # the row saying Djibo has 25 weeks of cover — following the
                # product's own advice would have moved stock INTO the node that
                # already cannot consume what it has.
                "reallocation_role": "source",
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

    A signal the centre has answered stays on the queue for a week, marked
    resolved and carrying the action that resolved it. Dropping it the instant
    it resolved meant the one exception in the product that genuinely CLOSES
    closed off camera: the row simply stopped existing, and a reader looking at
    the queue afterwards saw an absence rather than a decision. Absence is the
    weakest possible evidence for the claim this screen is making.
    """
    as_of = as_of or date.today()
    rows = []
    signals = ShortfallSignal.objects.select_related("site", "org", "resolved_by_action").exclude(
        status=ShortfallSignal.Status.RESOLVED,
        resolved_by_action__isnull=True,
    )
    for signal in signals:
        action = signal.resolved_by_action if signal.status == ShortfallSignal.Status.RESOLVED else None
        if action is not None and (as_of - action.created_at.date()).days > RESOLVED_SIGNAL_VISIBLE_DAYS:
            continue
        # The next consignment already heading for this site, if there is one.
        #
        # The recommendation named two paths — reallocate OR expedite — and the
        # row carried no shipment, so only Reallocate could ever render. Naming
        # the inbound consignment wires the second verb instead of deleting it:
        # a site running dry with a lorry already on the road is exactly the
        # case where expediting that lorry is the cheaper answer.
        next_inbound = (
            Shipment.objects.filter(destination_id=signal.site_id)
            .exclude(status__in=[Shipment.Status.CONFIRMED, Shipment.Status.DELIVERED])
            .order_by("eta")
            .first()
        )
        rows.append(
            {
                "key": f"signal-{signal.id}",
                "kind": "Partner shortfall",
                "origin": "partner",
                "tone": "good" if action else "bad",
                "signal_id": signal.id,
                # The close, on the row, with who made it and why. This is the
                # one exception kind that genuinely resolves — a derived row can
                # only be ANSWERED until the cartons land — so it is the only
                # place the queue can show a loop completing.
                "resolved_by": (
                    {
                        "action_id": action.id,
                        "actor": action.actor,
                        "effect": action.effect,
                        "rationale": action.rationale,
                        "resolved_on": action.created_at.date().isoformat(),
                    }
                    if action
                    else None
                ),
                "node_id": signal.site_id,
                "node_name": signal.site.name,
                "org_name": signal.org.legal_name,
                "raised_on": signal.raised_on.isoformat(),
                "children_at_risk": signal.children_affected,
                "by_date": signal.needed_by.isoformat(),
                # Only where the expedite verb has a consignment to act on.
                "shipment_id": next_inbound.id if (next_inbound and not action) else None,
                "shipment_reference": next_inbound.reference if (next_inbound and not action) else None,
                # The physical fact, in cartons. It read "N children at SITE by
                # 4 September" while the risk line beneath it read "N children
                # lose a full course by 4 Sep" — the same fact twice, on
                # adjacent lines, in two different date formats.
                "what": f"{int(signal.cartons_short):,} cartons short at {signal.site.name}",
                "why": signal.note
                or f"{signal.org.legal_name} reported a shortfall of {int(signal.cartons_short):,} cartons.",
                "action": (
                    "Closed by the reallocation that answered it."
                    if action
                    else (
                        f"Reallocate from a node holding surplus, or expedite {next_inbound.reference}."
                        if next_inbound
                        else "Reallocate from a node holding surplus — nothing is currently inbound to expedite."
                    )
                ),
                "derivation": (
                    f"Reported by {signal.org.legal_name} on {signal.raised_on:%-d %B} "
                    f"from their own distribution calendar."
                ),
            }
        )
    return rows


def _answered_nodes():
    """Nodes with cartons already on the way from a decision somebody took.

    A reallocation creates a real consignment with planned milestones, and
    until it arrives the node's stock — and therefore its cover, and therefore
    its children at risk — is unchanged. Which is correct: the cartons are not
    there yet. But it left the command centre unable to show its own central
    claim, that an exception is answered by the action that answers it. The row
    sat in the queue identical to before, and the only trace of a decision that
    had actually been taken was a toast that had already faded.

    So an exception whose node has an undelivered inbound reallocation is
    ANSWERED, not resolved. It stays in the queue because the children are
    still at risk until the truck arrives; it stops competing for attention
    with the ones nobody has done anything about yet.
    """
    answered = {}
    actions = SupplyAction.objects.filter(kind=SupplyAction.Kind.REALLOCATE, target_node__isnull=False).select_related(
        "target_node", "shipment"
    )
    for action in actions:
        shipment = action.shipment
        if shipment is not None and shipment.status == Shipment.Status.CONFIRMED:
            continue  # landed and counted; the node's own stock now carries it
        answered.setdefault(action.target_node_id, action)
    return answered


def _expedited_shipments():
    """Consignments somebody has already chased, keyed by shipment.

    The reallocation case above taught this lesson once: a decision that
    leaves the underlying figures unchanged (correctly — the cartons are not
    there yet) also left the queue unable to show that the decision happened.
    An expedite is the same shape one level down. It is recorded against a
    SHIPMENT rather than a node, so a late row is answered by an expedite on
    exactly its own consignment — an expedite on some other lorry into the
    same hub answers nothing.
    """
    chased = {}
    actions = SupplyAction.objects.filter(kind=SupplyAction.Kind.EXPEDITE, shipment__isnull=False).select_related(
        "shipment"
    )
    for action in actions:
        if action.shipment.status == Shipment.Status.CONFIRMED:
            continue  # it arrived; the chase is history, not an answer
        chased.setdefault(action.shipment_id, action)
    return chased


def build_queue(contracts=None, as_of=None):
    """Every exception, ranked by the children who go without SOONEST.

    Rows already answered by a reallocation sort last whatever their figure,
    because the question this queue answers is "what has nobody done anything
    about", and a row with cartons on the road is not that.

    Within that, ranking is on children at risk *inside the decision horizon*
    rather than on the raw figure. The screen promises "where, and by when" and
    says any other ordering is not an ordering, and then ranked on magnitude
    alone — so 907 children whose cartons expire on 25 December outranked 87
    children who go without on 4 August. Both numbers are real; only one is
    actionable this month, and a worklist that cannot tell them apart is a
    leaderboard.

    The unit does not change — it is still children, which is what makes the
    four kinds comparable at all. What changes is that a row only spends its
    figure if the harm falls within the horizon. A row with no date is treated
    as already happening: a short receipt is not pending, it is counted.
    """
    as_of = as_of or date.today()
    rows = (
        late_exceptions(contracts=contracts, as_of=as_of)
        + discrepancy_exceptions(as_of=as_of)
        + expiry_exceptions(as_of=as_of)
        + partner_signal_exceptions(as_of=as_of)
    )
    answered = _answered_nodes()
    expedited = _expedited_shipments()
    for row in rows:
        # Everything that is not an expiry row names the node that NEEDS
        # cartons, so a reallocation raised from it moves stock toward it.
        row.setdefault("reallocation_role", "target")
        row["children_at_risk_soon"] = _children_within_horizon(row, as_of=as_of)
        row["decision_horizon_days"] = DECISION_HORIZON_DAYS
        # A shipment-level answer outranks a node-level one because it is the
        # more specific claim: this exact consignment has been chased.
        action = expedited.get(row.get("shipment_id")) or answered.get(row.get("node_id"))
        row["answered_by"] = (
            {
                "action_id": action.id,
                "effect": action.effect,
                "actor": action.actor,
                "rationale": action.rationale,
            }
            if action
            else None
        )
    return sorted(
        rows,
        key=lambda r: (
            # closed rows sink below answered ones, and answered below
            # everything still waiting on somebody. The queue's question is
            # "what has nobody done anything about", and neither is that.
            r.get("resolved_by") is not None,
            r["answered_by"] is not None,
            # then by who goes without soonest, with the raw figure only
            # breaking ties between rows equally urgent
            -(r.get("children_at_risk_soon") or 0),
            -(r["children_at_risk"] or 0),
            r["key"],
        ),
    )
