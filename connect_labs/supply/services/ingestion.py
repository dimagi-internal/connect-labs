"""Ingestion: how awarded suppliers push shipment data in.

Three tiers, deliberately, because that is the real capability gradient of a
humanitarian supply chain:

* ``epcis``   — GS1 EPCIS 2.0 JSON-LD documents from a supplier with a real
                traceability system (our Kano factory).
* ``asn``     — a despatch-advice document, the JSON shape any commodity EDI
                translation layer emits from an X12 856 / EDIFACT DESADV.
* ``checkin`` — sparse phone-app confirmations from corridors where nothing
                more is realistic (Port Sudan → El Fasher).

Everything lands as an append-only :class:`SupplyEvent`; shipment status and
milestone actuals are DERIVED from that log, never set directly. Capture is
idempotent on ``external_id`` because delivery is at-least-once.
"""
from datetime import datetime
from datetime import timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from .. import gs1
from ..models import Discrepancy, Milestone, Shipment, ShipmentLine, SupplyEvent, SupplyNode
from .org_actions import ActionError

# EPCIS bizStep -> our vocabulary. Accepts bare steps and CBV URNs.
BIZ_STEPS = {step.value for step in SupplyEvent.BizStep}

# Which bizStep advances a shipment to which status.
STATUS_BY_STEP = {
    SupplyEvent.BizStep.DEPARTING: Shipment.Status.IN_TRANSIT,
    SupplyEvent.BizStep.LOADING: Shipment.Status.IN_TRANSIT,
    SupplyEvent.BizStep.RECEIVING: Shipment.Status.DELIVERED,
}

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
    except ValueError:
        raise ActionError(f"unparseable eventTime: {value}")
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
    if not key:
        return None
    return SupplyNode.objects.filter(gln=key[:13]).first()


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


# ---------------------------------------------------------------------------
# Tier 1 — EPCIS 2.0 capture
# ---------------------------------------------------------------------------


def capture_epcis_document(org, document):
    """Capture an EPCIS 2.0 ``EPCISDocument`` (or a bare event list).

    Returns ``{"captured": n, "duplicates": n, "event_ids": [...]}``.
    """
    if not isinstance(document, dict):
        raise ActionError("body must be an EPCIS document object")

    body = document.get("epcisBody") or {}
    events = body.get("eventList")
    if events is None:
        events = document.get("eventList") or ([document] if document.get("type") else None)
    if not events:
        raise ActionError("no events found in document")

    captured, duplicates, ids = 0, 0, []
    for raw_event in events:
        event, created = _capture_epcis_event(org, raw_event)
        ids.append(event.id)
        if created:
            captured += 1
        else:
            duplicates += 1
    return {"captured": captured, "duplicates": duplicates, "event_ids": ids}


EPCIS_TYPE_MAP = {
    "ObjectEvent": SupplyEvent.EventType.OBJECT,
    "AggregationEvent": SupplyEvent.EventType.AGGREGATION,
    "TransformationEvent": SupplyEvent.EventType.TRANSFORMATION,
}


def _capture_epcis_event(org, raw_event):
    if not isinstance(raw_event, dict):
        raise ActionError("each event must be an object")

    event_type = EPCIS_TYPE_MAP.get(raw_event.get("type"), SupplyEvent.EventType.OBJECT)
    biz_step = normalise_biz_step(raw_event.get("bizStep"))
    event_time = parse_event_time(raw_event.get("eventTime"))
    read_point = resolve_node(raw_event.get("readPoint") or raw_event.get("bizLocation"))

    transactions = {}
    for entry in raw_event.get("bizTransactionList") or []:
        if isinstance(entry, dict) and entry.get("type"):
            transactions[str(entry["type"]).rsplit(":", 1)[-1]] = entry.get("bizTransaction")

    epc_list = raw_event.get("epcList") or raw_event.get("outputEPCList") or []
    quantity_list = raw_event.get("quantityList") or raw_event.get("outputQuantityList") or []

    return capture_event(
        org,
        event_type=event_type,
        biz_step=biz_step,
        event_time=event_time,
        read_point=read_point,
        epc_list=epc_list,
        quantity_list=quantity_list,
        biz_transactions=transactions,
        disposition=str(raw_event.get("disposition") or "").rsplit(":", 1)[-1],
        source_tier=SupplyEvent.SourceTier.EPCIS,
        external_id=raw_event.get("eventID") or "",
        raw=raw_event,
    )


# ---------------------------------------------------------------------------
# Tier 2 — despatch advice (ASN shape)
# ---------------------------------------------------------------------------


@transaction.atomic
def capture_despatch_advice(org, payload, source_tier=SupplyEvent.SourceTier.ASN):
    """Materialise a shipment from an ASN-shaped document.

    The tree mirrors X12 856 / EDIFACT DESADV: shipment → orders → packages →
    items, which is what any EDI translation layer emits as JSON. The supplier
    portal posts the SAME shape from a webform with ``source_tier="portal"``,
    so a supplier with no system at all can key in what an API client sends.
    """
    if not isinstance(payload, dict):
        raise ActionError("body must be a despatch advice object")

    asn_ref = (payload.get("asn_reference") or payload.get("despatch_advice_number") or "").strip()
    if not asn_ref:
        raise ActionError("asn_reference is required")

    contract_ref = (payload.get("contract_reference") or "").strip()
    from ..models import Contract

    contract = Contract.objects.filter(reference=contract_ref, org=org).first()
    if contract is None:
        raise ActionError("contract_reference does not match a contract for your organisation")

    origin = (
        resolve_node(payload.get("ship_from"))
        or SupplyNode.objects.filter(gln=str(payload.get("ship_from_gln") or "")).first()
    )
    destination = (
        resolve_node(payload.get("ship_to"))
        or SupplyNode.objects.filter(gln=str(payload.get("ship_to_gln") or "")).first()
    )
    if origin is None or destination is None:
        raise ActionError("ship_from and ship_to must resolve to known locations (by GLN)")

    existing = Shipment.objects.filter(asn_reference=asn_ref, contract__org=org).first()
    if existing:
        return existing, False

    packages = payload.get("packages") or []
    lines_data = []
    total = 0.0
    for package in packages:
        sscc = str(package.get("sscc") or "")
        for item in package.get("items") or []:
            qty = float(item.get("quantity") or 0)
            total += qty
            lines_data.append(
                {
                    "gtin": str(item.get("gtin") or ""),
                    "batch_lot": str(item.get("batch_lot") or item.get("lot") or ""),
                    "expiry_date": item.get("expiry_date") or None,
                    "quantity": qty,
                    "unit": item.get("unit") or "cartons",
                    "sscc": sscc,
                }
            )
    if not lines_data:
        raise ActionError("despatch advice contains no items")

    reference = payload.get("shipment_reference") or f"SHP-{asn_ref}"
    shipment = Shipment.objects.create(
        contract=contract,
        reference=reference,
        asn_reference=asn_ref,
        origin=origin,
        destination=destination,
        waypoints=payload.get("waypoint_gLNs") or [],
        quantity=total,
        unit=lines_data[0]["unit"],
        eta=parse_event_time(payload.get("eta")) if payload.get("eta") else None,
    )
    ShipmentLine.objects.bulk_create([ShipmentLine(shipment=shipment, **row) for row in lines_data])

    # An ASN also creates the planned legs, so ETA-vs-plan is renderable from
    # the moment of despatch.
    Milestone.objects.create(
        shipment=shipment,
        node=origin,
        kind=Milestone.Kind.DEPART,
        sequence=0,
        planned_at=parse_event_time(payload.get("departed_at")) if payload.get("departed_at") else None,
    )
    Milestone.objects.create(
        shipment=shipment,
        node=destination,
        kind=Milestone.Kind.ARRIVE,
        sequence=1,
        planned_at=shipment.eta,
        estimated_at=shipment.eta,
    )

    capture_event(
        org,
        biz_step=SupplyEvent.BizStep.DEPARTING,
        event_time=parse_event_time(payload.get("departed_at")) if payload.get("departed_at") else timezone.now(),
        read_point=origin,
        epc_list=[gs1.digital_link("00", p["sscc"]) for p in lines_data if p["sscc"]],
        quantity_list=[
            {"gtin": row["gtin"], "batch_lot": row["batch_lot"], "quantity": row["quantity"], "uom": "CT"}
            for row in lines_data
        ],
        biz_transactions={"desadv": asn_ref, "po": payload.get("po_reference", "")},
        disposition="in_transit",
        source_tier=source_tier,
        external_id=f"asn:{asn_ref}",
        raw=payload,
        shipment=shipment,
    )
    return shipment, True


# ---------------------------------------------------------------------------
# Tier 3 — low-tech check-ins
# ---------------------------------------------------------------------------


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
    node = None
    if payload.get("location_gln"):
        node = SupplyNode.objects.filter(gln=str(payload["location_gln"])).first()
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
    if shipment.contract.org_id != org.id:
        raise ActionError("shipment does not belong to your organisation")

    biz_step = normalise_biz_step(payload.get("biz_step"))
    node = None
    if payload.get("node_id"):
        node = SupplyNode.objects.filter(id=payload["node_id"]).first()
    elif payload.get("location_gln"):
        node = SupplyNode.objects.filter(gln=str(payload["location_gln"])).first()

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
    if shipment.contract.org_id != org.id:
        raise ActionError("shipment does not belong to your organisation")
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
