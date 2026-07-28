"""What the centre decided to do, and the movement it actually creates.

A reallocation is not a status change. It creates a real
:class:`~..models.Shipment` from the surplus node with planned milestones and
stored route geometry, and every downstream figure — the destination's weeks of
cover, the corridor's pipeline gap, the flow map — recomputes from that
shipment rather than being adjusted to match the decision.

That distinction is the whole argument for recording actions here at all. A
decision that only moves a number is indistinguishable from someone editing the
number. A decision that puts a consignment on the map is the same kind of fact
as the consignments already on it, and the exception resolves because the
situation changed.
"""
from datetime import timedelta

from django.contrib.gis.geos import LineString
from django.db import transaction
from django.utils import timezone

from .. import routes
from ..models import Milestone, Shipment, ShipmentLine, ShortfallSignal, SupplyAction
from .cover import stock_on_hand
from .org_actions import ActionError

# How long a reallocated consignment is planned to take. Short, because a
# reallocation is by definition a move between two nodes already inside the
# response rather than a fresh import.
REALLOCATION_TRANSIT_DAYS = 6


def _next_reference():
    count = Shipment.objects.filter(reference__startswith="SHP-RA-").count()
    return f"SHP-RA-{count + 1:04d}"


def _plan_route(shipment, origin, destination):
    coords = routes.build_route(origin, destination)
    if coords and len(coords) >= 2:
        shipment.route = LineString(coords, srid=4326)
        shipment.save(update_fields=["route"])


def _plan_milestones(shipment, origin, destination, departs_at, arrives_at):
    Milestone.objects.update_or_create(
        shipment=shipment,
        node=origin,
        kind=Milestone.Kind.DEPART,
        sequence=0,
        defaults={"planned_at": departs_at, "estimated_at": departs_at},
    )
    Milestone.objects.update_or_create(
        shipment=shipment,
        node=destination,
        kind=Milestone.Kind.ARRIVE,
        sequence=1,
        defaults={"planned_at": arrives_at, "estimated_at": arrives_at},
    )


@transaction.atomic
def reallocate(*, actor, source_node, target_node, quantity, rationale, signal=None, contract=None):
    """Move surplus stock between two nodes, as a real consignment.

    The source must actually hold what is being moved: a reallocation that
    overdraws a node is a paper transfer, and the receiving site would plan
    against cartons that never arrive.
    """
    if source_node is None or target_node is None:
        raise ActionError("a reallocation needs a source and a destination")
    if source_node.id == target_node.id:
        raise ActionError("a reallocation needs two different nodes")
    quantity = float(quantity or 0)
    if quantity <= 0:
        raise ActionError("quantity must be positive")
    if not (rationale or "").strip():
        raise ActionError("a reallocation must record why it was made")

    available = float(stock_on_hand(source_node))
    if quantity > available:
        raise ActionError(f"{source_node.name} holds {available:,.0f} cartons; cannot reallocate {quantity:,.0f}")

    if contract is None:
        # Hang the movement off a contract that already serves this corridor,
        # so the consignment stays attributable to a funding envelope rather
        # than appearing on the map from nowhere.
        contract = (
            Shipment.objects.filter(destination__country=target_node.country)
            .select_related("contract")
            .values_list("contract", flat=True)
            .first()
        )
        from ..models import Contract

        contract = Contract.objects.filter(id=contract).first() if contract else Contract.objects.first()
    if contract is None:
        raise ActionError("no contract available to carry the reallocation")

    now = timezone.now()
    arrives_at = now + timedelta(days=REALLOCATION_TRANSIT_DAYS)
    shipment = Shipment.objects.create(
        contract=contract,
        reference=_next_reference(),
        origin=source_node,
        destination=target_node,
        quantity=quantity,
        unit="cartons",
        status=Shipment.Status.PLANNED,
        eta=arrives_at,
    )
    ShipmentLine.objects.create(
        shipment=shipment,
        gtin="",
        batch_lot="",
        quantity=quantity,
        unit="cartons",
    )
    _plan_milestones(shipment, source_node, target_node, now, arrives_at)
    _plan_route(shipment, source_node, target_node)

    action = SupplyAction.objects.create(
        kind=SupplyAction.Kind.REALLOCATE,
        actor=actor,
        rationale=rationale.strip(),
        effect=(
            f"{quantity:,.0f} cartons from {source_node.name} to {target_node.name}, "
            f"planned to arrive {arrives_at:%-d %B}."
        ),
        source_node=source_node,
        target_node=target_node,
        quantity=quantity,
        shipment=shipment,
    )

    if signal is not None:
        # The decision and the evidence that prompted it become one record —
        # the thing a messaging group can never give you afterwards.
        signal.status = ShortfallSignal.Status.RESOLVED
        signal.resolved_by_action = action
        signal.save(update_fields=["status", "resolved_by_action"])

    return action


@transaction.atomic
def expedite(*, actor, shipment, rationale):
    """Record a decision to chase a consignment, against the consignment."""
    if not (rationale or "").strip():
        raise ActionError("an expedite must record why it was made")
    return SupplyAction.objects.create(
        kind=SupplyAction.Kind.EXPEDITE,
        actor=actor,
        rationale=rationale.strip(),
        effect=f"{shipment.reference} escalated with the carrier.",
        source_node=shipment.origin,
        target_node=shipment.destination,
        quantity=shipment.quantity,
        shipment=shipment,
    )


@transaction.atomic
def raise_shortfall(*, org, site, needed_by, children_affected, cartons_short, note=""):
    """A partner reporting, from their own screen, that they will run short."""
    if site.owner_id != org.id:
        raise ActionError("a partner can only raise a shortfall at one of their own sites")
    if int(children_affected or 0) <= 0:
        raise ActionError("a shortfall has to name how many children are affected")
    return ShortfallSignal.objects.create(
        org=org,
        site=site,
        raised_on=timezone.now().date(),
        needed_by=needed_by,
        children_affected=int(children_affected),
        cartons_short=cartons_short,
        note=note or "",
    )
