"""Event capture and the state it derives.

Every ingestion tier funnels through :func:`capture_event`. Shipment status and
milestone actuals are derived here and nowhere else, so however an event
arrives — a standards feed, a despatch document, or a typed form — it moves the
same state the same way.

Capture is idempotent on ``external_id``: delivery is at-least-once, so a
repeat is normal traffic rather than an error.
"""
from datetime import datetime
from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone

from ... import gs1
from ...models import Discrepancy, Milestone, Shipment, ShipmentLine, SupplyEvent, SupplyNode
from ..org_actions import ActionError

# EPCIS bizStep -> our vocabulary. Accepts bare steps and CBV URNs.
BIZ_STEPS = {step.value for step in SupplyEvent.BizStep}

# Which bizStep advances a shipment to which status. Arriving implies in-transit
# too: goods cannot arrive somewhere without having left, and out-of-order feeds
# are normal.
STATUS_BY_STEP = {
    SupplyEvent.BizStep.LOADING: Shipment.Status.IN_TRANSIT,
    SupplyEvent.BizStep.DEPARTING: Shipment.Status.IN_TRANSIT,
    SupplyEvent.BizStep.ARRIVING: Shipment.Status.IN_TRANSIT,
    SupplyEvent.BizStep.RECEIVING: Shipment.Status.DELIVERED,
}

# Status is monotonic: a late-arriving event can advance a shipment but never
# walk it backwards.
STATUS_ORDER = [
    Shipment.Status.PLANNED,
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.DELIVERED,
    Shipment.Status.CONFIRMED,
]


def normalise_biz_step(value):
    """Accept ``shipping``, ``urn:epcglobal:cbv:bizstep:departing``, etc."""
    if not value:
        raise ActionError("bizStep is required")
    step = str(value).rsplit(":", 1)[-1].lower()
    aliases = {"shipping": "departing", "unloading": "arriving", "storing": "storing"}
    step = aliases.get(step, step)
    if step not in BIZ_STEPS:
        raise ActionError(f"unsupported bizStep: {value}")
    return step


def parse_event_time(value):
    if not value:
        return timezone.now()
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ActionError(f"unparseable eventTime: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def resolve_node(reference):
    """Resolve a readPoint/bizLocation to a node via its GLN."""
    if not reference:
        return None
    if isinstance(reference, dict):
        reference = reference.get("id")
    _ai, key = gs1.parse_digital_link(reference)
    return node_by_gln(key)


def node_by_gln(gln):
    """Look up a node by GLN. A blank GLN matches nothing — never the first
    node that happens to have an empty one."""
    gln = (gln or "").strip()
    if not gln:
        return None
    return SupplyNode.objects.filter(gln=gln[:13]).first()


def _shipment_from_transactions(org, biz_transactions, epc_list):
    """Find the shipment an event belongs to.

    Preference order: the despatch-advice reference (the ASN is what ties an
    event stream to a consignment), then the shipment reference, then a pallet
    SSCC that appeared on a shipment line.
    """
    desadv = biz_transactions.get("desadv") or biz_transactions.get("asn")
    if desadv:
        ref = str(desadv).rsplit(":", 1)[-1]
        found = Shipment.objects.filter(asn_reference=ref, contract__org=org).first()
        if found:
            return found
    shipment_ref = biz_transactions.get("shipment")
    if shipment_ref:
        found = Shipment.objects.filter(reference=shipment_ref, contract__org=org).first()
        if found:
            return found
    for epc in epc_list or []:
        _ai, key = gs1.parse_digital_link(epc)
        if not key:
            continue
        line = ShipmentLine.objects.filter(sscc=key, shipment__contract__org=org).first()
        if line:
            return line.shipment
    return None


def _normalise_quantities(quantity_list):
    """EPCIS quantityList -> [{gtin, batch_lot, quantity, uom}]."""
    rows = []
    for entry in quantity_list or []:
        epc_class = entry.get("epcClass") or entry.get("gtin") or ""
        gtin, batch = "", ""
        if "id.gs1.org" in str(epc_class):
            parts = str(epc_class).split("id.gs1.org/")[-1].strip("/").split("/")
            for i in range(0, len(parts) - 1, 2):
                if parts[i] == "01":
                    gtin = parts[i + 1]
                elif parts[i] == "10":
                    batch = parts[i + 1]
        else:
            gtin = str(epc_class)
            batch = entry.get("batch_lot") or entry.get("lot") or ""
        rows.append(
            {
                "gtin": gtin,
                "batch_lot": batch or entry.get("batch_lot", ""),
                "quantity": float(entry.get("quantity") or 0),
                "uom": entry.get("uom") or "CT",
            }
        )
    return rows


@transaction.atomic
def capture_event(
    org,
    *,
    biz_step,
    event_time,
    source_tier,
    read_point=None,
    epc_list=None,
    quantity_list=None,
    biz_transactions=None,
    event_type=SupplyEvent.EventType.OBJECT,
    disposition="",
    external_id="",
    raw=None,
    shipment=None,
):
    """Record one event and let it advance derived state.

    Returns ``(event, created)``. A repeat of the same ``external_id`` returns
    the stored event with ``created=False`` — at-least-once delivery means
    consumers must be idempotent.
    """
    if external_id:
        existing = SupplyEvent.objects.filter(org=org, external_id=external_id).first()
        if existing:
            return existing, False

    biz_transactions = biz_transactions or {}
    epc_list = epc_list or []
    quantities = _normalise_quantities(quantity_list)

    if shipment is None:
        shipment = _shipment_from_transactions(org, biz_transactions, epc_list)

    try:
        with transaction.atomic():
            event = SupplyEvent.objects.create(
                org=org,
                shipment=shipment,
                event_type=event_type,
                biz_step=biz_step,
                disposition=disposition,
                event_time=event_time,
                read_point=read_point,
                epc_list=epc_list,
                quantity_list=quantities,
                biz_transactions=biz_transactions,
                source_tier=source_tier,
                external_id=external_id or "",
                raw=raw or {},
            )
    except IntegrityError:
        # A concurrent retry won the race on the (org, external_id) constraint.
        # At-least-once delivery means this is normal, not an error.
        existing = SupplyEvent.objects.filter(org=org, external_id=external_id).first()
        if existing:
            return existing, False
        raise

    if shipment:
        _apply_to_shipment(event, shipment)
    return event, True


def _apply_to_shipment(event, shipment):
    """Advance milestone actuals, shipment status, and raise discrepancies."""
    # Milestone actuals: departing stamps the depart leg at that node, arriving
    # and receiving stamp the arrive leg.
    kind = None
    if event.biz_step == SupplyEvent.BizStep.DEPARTING:
        kind = Milestone.Kind.DEPART
    elif event.biz_step in (SupplyEvent.BizStep.ARRIVING, SupplyEvent.BizStep.RECEIVING):
        kind = Milestone.Kind.ARRIVE

    if kind and event.read_point:
        milestone = (
            shipment.milestones.filter(node=event.read_point, kind=kind, actual_at__isnull=True)
            .order_by("sequence")
            .first()
        )
        if milestone:
            milestone.actual_at = event.event_time
            milestone.save(update_fields=["actual_at"])

    new_status = STATUS_BY_STEP.get(event.biz_step)
    if new_status and STATUS_ORDER.index(new_status) > STATUS_ORDER.index(shipment.status):
        shipment.status = new_status
        if new_status == Shipment.Status.IN_TRANSIT and not shipment.departed_at:
            shipment.departed_at = event.event_time
        if new_status == Shipment.Status.DELIVERED:
            shipment.delivered_at = event.event_time
        shipment.save(update_fields=["status", "departed_at", "delivered_at"])

    if event.biz_step == SupplyEvent.BizStep.RECEIVING:
        _reconcile_receipt(event, shipment)


def _reconcile_receipt(event, shipment):
    received = sum(row["quantity"] for row in event.quantity_list)
    if not received:
        return
    expected = float(shipment.quantity)
    if abs(received - expected) < 0.001:
        return
    Discrepancy.objects.get_or_create(
        shipment=shipment,
        event=event,
        defaults={
            "expected_quantity": expected,
            "received_quantity": received,
            "note": (
                f"Receipt at {event.read_point.name if event.read_point else 'destination'} "
                f"reconciles to {received:g} against {expected:g} despatched."
            ),
        },
    )
