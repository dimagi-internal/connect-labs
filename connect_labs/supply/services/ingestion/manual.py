"""Tier 3 — check-ins, hand-keyed events, and delivery confirmation.

The low-tech end of the capability gradient, and deliberately first class: on
the hardest corridors a phone call and a form are all there is. Events recorded
here carry ``source_tier`` ``checkin`` or ``portal`` so hand-entered data is
never presented as a system feed.
"""
from django.utils import timezone  # noqa: F401

from ... import gs1
from ...models import Shipment, SupplyEvent, SupplyNode
from ..org_actions import ActionError
from ._core import capture_event, node_by_gln, normalise_biz_step, parse_event_time


def assert_may_report(org, shipment):
    """Raise unless ``org`` is a party to this consignment.

    A consignment has two parties with something to report — the supplier that
    despatched it, and the organisation that receives it at its own site — and
    only the despatching one was permitted. That made the receiving partner's
    whole capture surface unusable: "Record the count" posted, the service
    refused it as somebody else's shipment, and the narrated act of a
    storekeeper counting cartons off a truck could not be performed by the
    storekeeper.

    Receipt authority follows NODE OWNERSHIP, which is the same rule the
    partner's own site list and cover figures are scoped by, so it cannot drift
    from them.
    """
    if shipment.contract.org_id == org.id:
        return
    destination_owner_id = getattr(shipment.destination, "owner_id", None)
    if destination_owner_id is not None and destination_owner_id == org.id:
        return
    raise ActionError("shipment does not belong to your organisation")


def capture_checkin(org, payload):
    """A phone-app confirmation: consignment reference, place, what happened.

    Deliberately first-class. On the hardest corridors this is all there is,
    and a demo that pretends otherwise is not credible.
    """
    reference = (payload.get("shipment_reference") or payload.get("consignment") or "").strip()
    if not reference:
        raise ActionError("shipment_reference is required")
    shipment = Shipment.objects.filter(reference=reference, contract__org=org).first()
    if shipment is None:
        raise ActionError("shipment_reference does not match a shipment for your organisation")

    biz_step = normalise_biz_step(payload.get("status") or payload.get("biz_step") or "arriving")
    node = node_by_gln(payload.get("location_gln"))
    if node is None and payload.get("place"):
        node = SupplyNode.objects.filter(name__iexact=str(payload["place"]).strip()).first()

    quantity_list = []
    if payload.get("quantity"):
        quantity_list = [{"gtin": "", "batch_lot": "", "quantity": float(payload["quantity"]), "uom": "CT"}]

    return capture_event(
        org,
        biz_step=biz_step,
        event_time=parse_event_time(payload.get("occurred_at")),
        read_point=node,
        quantity_list=quantity_list,
        biz_transactions={"shipment": reference},
        source_tier=SupplyEvent.SourceTier.CHECKIN,
        external_id=payload.get("checkin_id") or "",
        raw=payload,
        shipment=shipment,
    )


def record_manual_event(org, shipment, payload):
    """Webform equivalent of an EPCIS event.

    Same vocabulary (bizStep, location, time, GTIN/lot/quantity), same
    append-only log, same derived state — only the source tier differs, so the
    provenance of hand-keyed data stays visible everywhere it is shown.
    """
    assert_may_report(org, shipment)

    biz_step = normalise_biz_step(payload.get("biz_step"))
    node = None
    if payload.get("node_id"):
        node = SupplyNode.objects.filter(id=payload["node_id"]).first()
    else:
        node = node_by_gln(payload.get("location_gln"))

    quantity_list = []
    if payload.get("quantity"):
        quantity_list = [
            {
                "gtin": str(payload.get("gtin") or ""),
                "batch_lot": str(payload.get("batch_lot") or ""),
                "quantity": float(payload["quantity"]),
                "uom": "CT",
            }
        ]

    epc_list = []
    if payload.get("sscc"):
        epc_list = [gs1.digital_link("00", str(payload["sscc"]))]

    return capture_event(
        org,
        biz_step=biz_step,
        event_time=parse_event_time(payload.get("event_time")),
        read_point=node,
        epc_list=epc_list,
        quantity_list=quantity_list,
        biz_transactions={"shipment": shipment.reference},
        disposition=str(payload.get("disposition") or ""),
        source_tier=SupplyEvent.SourceTier.PORTAL,
        raw={"note": payload.get("note", ""), "entered_by_hand": True},
        shipment=shipment,
    )


def confirm_delivery(org, shipment, quantity=None, note=""):
    """Portal-tier confirmation that closes out a delivered shipment."""
    assert_may_report(org, shipment)
    if shipment.status == Shipment.Status.PLANNED:
        raise ActionError("shipment has not departed yet")

    event, _created = capture_event(
        org,
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=shipment.destination,
        quantity_list=[{"gtin": "", "quantity": float(quantity or shipment.quantity), "uom": "CT"}],
        biz_transactions={"shipment": shipment.reference},
        disposition="in_progress",
        source_tier=SupplyEvent.SourceTier.PORTAL,
        raw={"note": note},
        shipment=shipment,
    )
    shipment.refresh_from_db()
    if shipment.status == Shipment.Status.DELIVERED:
        shipment.status = Shipment.Status.CONFIRMED
        shipment.save(update_fields=["status"])
    return event
