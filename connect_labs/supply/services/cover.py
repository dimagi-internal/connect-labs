"""Cover: how long a node's stock lasts against the caseload it serves.

**This module is the single source of these figures.** The command centre and
the implementing-partner surface both show weeks of cover and a projected
stockout date for the same nodes, and the two must agree — a partner told they
have eleven days while the centre reads three weeks is worse than neither
having the number. Two independent implementations would drift the first time
either was touched, so there is one, on the server, and both surfaces render
what it returns.

That is also what makes these figures testable. Severity used to be computed in
``tab_command.jsx`` with no JS test harness anywhere in the repo; ranking an
exception queue by children-at-risk is a load-bearing claim and it belongs
somewhere pytest can reach.

The derivation, end to end:

* **stock on hand** = cartons received at the node minus cartons despatched
  from it, both from the append-only :class:`SupplyEvent` log — never a stored
  field, for the same reason shipment status is not one.
* **served children** = the district's monthly SAM caseload, divided between
  the demand-serving nodes sitting in that district.
* **weekly burn** = served children ÷ weeks per month, in cartons. One carton
  is one child's full course (``gs1.CARTONS_PER_CHILD_TREATED``), and a course
  is issued across a treatment episode, so the store is drawn down at the rate
  children are *admitted*.
* **weeks of cover** = stock on hand ÷ weekly burn.
* **stockout date** = as-of date + weeks of cover.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q

from .. import gs1
from ..models import WEEKS_PER_MONTH, CaseloadEstimate, ShipmentLine, SupplyEvent, SupplyNode


def demand_serving_nodes():
    """Nodes that hold stock against a caseload.

    The discriminator is ``adm1_code``, not ``kind``. A port or a national
    warehouse sits on the route but serves no children, so a cover figure there
    would be meaningless — the denominator is absent, not small. A forward
    store like Kassala *does* serve a district, and it is a warehouse. Carrying
    a district code is the thing that makes a node answerable for children.
    """
    return SupplyNode.objects.exclude(adm1_code="")


# Steps that move stock into and out of a node.
_INBOUND_STEPS = (SupplyEvent.BizStep.RECEIVING,)
_OUTBOUND_STEPS = (SupplyEvent.BizStep.DEPARTING, SupplyEvent.BizStep.LOADING)


def _event_cartons(event):
    """Cartons on an event, from its EPCIS quantity list.

    Falls back to the shipment quantity when a check-in carried no explicit
    quantity list — the lowest tier is a consignment reference and a place, and
    treating that as zero would silently under-count exactly the corridors that
    matter most.
    """
    total = Decimal("0")
    for row in event.quantity_list or []:
        if row.get("uom") in (None, "", "cartons", "EA"):
            try:
                total += Decimal(str(row.get("quantity") or 0))
            except (TypeError, ValueError):
                continue
    if total == 0 and event.shipment is not None and event.shipment.unit == "cartons":
        return Decimal(str(event.shipment.quantity))
    return total


def stock_on_hand(node):
    """Cartons currently held at ``node``, derived from the event log."""
    received = Decimal("0")
    despatched = Decimal("0")
    events = SupplyEvent.objects.filter(read_point=node).select_related("shipment")
    for event in events:
        if event.biz_step in _INBOUND_STEPS:
            received += _event_cartons(event)
        elif event.biz_step in _OUTBOUND_STEPS:
            despatched += _event_cartons(event)
    return received - despatched


def caseload_for_district(adm1_code, month=None):
    """The most recent caseload estimate for a district at or before ``month``."""
    if not adm1_code:
        return None
    qs = CaseloadEstimate.objects.filter(adm1_code=adm1_code)
    if month is not None:
        qs = qs.filter(month__lte=month)
    return qs.order_by("-month").first()


def served_children(node, month=None):
    """Children this node is responsible for in a month.

    Two different answers for two different kinds of node, and the difference
    is not an inconsistency:

    * A **delivery point** admits children itself, so it carries its share of
      the district — the caseload split between the sites in that district.
    * A **hub or forward store** supplies all of those sites, so it carries the
      district's whole caseload.

    They are the same demand seen from two positions in the chain. Splitting a
    hub's figure the way a site's is split would understate what the hub has to
    cover; giving a site the district total would overstate what it admits.
    """
    estimate = caseload_for_district(node.adm1_code, month=month)
    if estimate is None:
        return 0
    if node.kind != SupplyNode.Kind.DELIVERY_POINT:
        return estimate.children_sam

    # Sites are not the same size, so the district does not divide evenly
    # between them. An even split makes every row on the partner's calendar
    # read 214 / 214 — identical figures down the page, which is the surest
    # sign a demo was generated rather than observed. `catchment_weight` is the
    # share of the district each site actually serves.
    peers = list(demand_serving_nodes().filter(adm1_code=node.adm1_code, kind=SupplyNode.Kind.DELIVERY_POINT))
    total_weight = sum(max(p.catchment_weight, 0) for p in peers)
    if total_weight <= 0:
        return estimate.children_sam / len(peers) if peers else 0
    return estimate.children_sam * (max(node.catchment_weight, 0) / total_weight)


def weekly_burn(node, month=None):
    """Cartons consumed per week at ``node``, from the admission rate."""
    children = served_children(node, month=month)
    if not children:
        return Decimal("0")
    weekly_admissions = Decimal(str(children)) / Decimal(str(WEEKS_PER_MONTH))
    return weekly_admissions * Decimal(str(gs1.CARTONS_PER_CHILD_TREATED))


def cover_for_node(node, as_of=None, month=None):
    """The full cover picture for one node.

    Returns ``None`` for a node with no caseload behind it — a port has no
    weeks of cover, and reporting one as infinite or zero would both be lies.
    """
    as_of = as_of or date.today()
    burn = weekly_burn(node, month=month)
    if burn <= 0:
        return None
    stock = stock_on_hand(node)
    weeks = float(stock / burn)
    return {
        "node_id": node.id,
        "node_name": node.name,
        "adm1_code": node.adm1_code,
        "stock_on_hand": float(stock),
        "served_children": round(served_children(node, month=month)),
        "weekly_burn": round(float(burn), 1),
        "weeks_of_cover": round(weeks, 1),
        "stockout_on": (as_of + timedelta(days=int(weeks * 7))).isoformat(),
        "as_of": as_of.isoformat(),
        "method": (
            "Stock on hand is receipts minus despatches from the event log. "
            "Weekly burn is the district SAM caseload divided between the sites "
            "serving it, at one carton per child's full course."
        ),
    }


def cover_by_node(nodes=None, as_of=None, month=None):
    """Cover for every demand-serving node, worst first."""
    if nodes is None:
        nodes = demand_serving_nodes()
    rows = [cover_for_node(n, as_of=as_of, month=month) for n in nodes]
    rows = [r for r in rows if r is not None]
    return sorted(rows, key=lambda r: r["weeks_of_cover"])


def children_at_risk(node, delay_days, as_of=None, month=None):
    """Children who miss a full course because supply is ``delay_days`` late.

    The figure the exception queue ranks on. A consignment nine days late into
    a district admitting four hundred children a week costs more than the same
    consignment nine days late into one admitting forty, and tonnage × lateness
    cannot tell those apart.

    Only the days of delay that fall *after* the store runs dry count: a delay
    absorbed by stock on hand costs nobody a course.
    """
    as_of = as_of or date.today()
    cover = cover_for_node(node, as_of=as_of, month=month)
    if cover is None or not delay_days or delay_days <= 0:
        return 0
    days_dry = delay_days - (cover["weeks_of_cover"] * 7)
    if days_dry <= 0:
        return 0
    daily_admissions = cover["served_children"] / (WEEKS_PER_MONTH * 7)
    return int(round(days_dry * daily_admissions))


def expiry_risk(node, as_of=None, month=None, horizon_days=180):
    """Cartons at ``node`` that expire before its caseload can consume them.

    A loss nobody is currently looking for: stock sitting where the demand
    behind it is too small to work through the batch before its expiry date.
    Only counts batches actually received at the node.
    """
    as_of = as_of or date.today()
    burn = weekly_burn(node, month=month)
    if burn <= 0:
        return None
    lines = ShipmentLine.objects.filter(
        Q(shipment__destination=node),
        shipment__status__in=("delivered", "confirmed"),
        expiry_date__isnull=False,
        expiry_date__lte=as_of + timedelta(days=horizon_days),
    ).select_related("shipment")

    at_risk = Decimal("0")
    soonest = None
    for line in lines:
        days_left = (line.expiry_date - as_of).days
        if days_left <= 0:
            consumable = Decimal("0")
        else:
            consumable = burn * Decimal(str(days_left / 7))
        surplus = Decimal(str(line.quantity)) - consumable
        if surplus > 0:
            at_risk += surplus
            if soonest is None or line.expiry_date < soonest:
                soonest = line.expiry_date
    if at_risk <= 0:
        return None
    return {
        "node_id": node.id,
        "node_name": node.name,
        "cartons_at_risk": int(at_risk),
        "expires_on": soonest.isoformat() if soonest else None,
        "children_equivalent": gs1.cartons_to_children(int(at_risk)),
    }
