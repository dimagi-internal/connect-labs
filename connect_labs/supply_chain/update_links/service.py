"""What an organisation can do through its update link, and the guard around it.

**No shadow write path.** Every action here turns into one ordinary operation
call -- the same `call_operation` the screens, the HTTP API and the MCP tools
use -- with `source="supplier_reported"` and `recorded_by_org_id` set to the
link's organisation. A partner is a party with the same operations (design doc
section 17.2); the link only decides which rows it may name.

**Two guards, deliberately redundant.** The public form offers only in-scope
rows, so a browser cannot pick another contract. `submit` checks again against
the link's own scope, re-read from the database, because the form is not the
only thing that could ever call it and because the page is unauthenticated: a
scope check that lives only in a form's queryset is one refactor away from
gone. Anything outside scope raises `OutOfScope` and nothing is written.

**Attributable.** Each write is recorded three ways: the row it produced
(`source`, `recorded_by_org`), an `UpdateLinkSubmission` naming the link, and
an audit-trail event (docs/AUDIT_LOGGING.md) carrying the link and the
organisation, since there is no user to attribute it to.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from connect_labs.audit_trail.models import Action
from connect_labs.audit_trail.service import record as audit_record
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import scopes as synthetic_scopes
from connect_labs.supply_chain.models import Contract, Item, Movement, Payment, Shipment, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links.models import UpdateLink, UpdateLinkSubmission
from connect_labs.supply_chain.values import money_digits, quantity_digits, quantity_phrase

SOURCE = "supplier_reported"

# The statuses a supplier may move a dispatch to. `planned` is ours: it is
# what an order looks like before anything has left.
SUPPLIER_SHIPMENT_STATUSES = ("dispatched", "in_transit", "at_customs", "cleared", "delivered", "lost")

# An order the supplier can confirm: one we placed. A draft is not an order
# yet -- a supplier confirming it would be accepting something nobody sent --
# and anything later has already moved past confirmation.
CONFIRMABLE = ("placed",)


class OutOfScope(ValueError):
    """The link does not cover this row, or the link is no longer valid.

    A ValueError so it reads as a refusal of the request rather than a crash,
    and its own type so a test (and a reader) can tell a scope refusal from an
    ordinary validation one.
    """


@dataclass
class Scope:
    """Everything a link may name, re-read from the database."""

    link: UpdateLink
    contracts: object
    supply_points: object
    items: object
    shipments: object
    payments: object


def scope_for(link) -> Scope:
    """The rows this link covers. Filtered by programme as well as by the link.

    The link's own join rows are already programme-checked when it is issued;
    filtering again here costs nothing and means a row moved between
    programmes (which nothing does today) could not carry a link with it.
    """
    program_id = link.program_id
    contracts = Contract.objects.filter(program_id=program_id, update_links=link).select_related(
        "commodity", "item", "supplier"
    )
    points = SupplyPoint.objects.filter(program_id=program_id, update_links=link)

    # Products the link can name: what its contracts are for, and what has
    # ever rested at its supply points. Not the programme's catalogue -- the
    # link reads nothing outside its scope, product names included.
    point_ids = list(points.values_list("pk", flat=True))
    item_ids = set(contracts.exclude(item=None).values_list("item_id", flat=True))
    item_ids |= set(
        Movement.objects.filter(program_id=program_id)
        .filter(Q(to_supply_point_id__in=point_ids) | Q(from_supply_point_id__in=point_ids))
        .exclude(item=None)
        .values_list("item_id", flat=True)
    )
    items = Item.objects.filter(pk__in=item_ids).select_related("commodity")
    shipments = Shipment.objects.filter(contract__in=contracts).select_related("contract")
    payments = Payment.objects.filter(invoice__contract__in=contracts).select_related("invoice__contract")
    return Scope(link, contracts, points, items, shipments, payments)


def _require(queryset, obj, what):
    if obj is None or not queryset.filter(pk=obj.pk).exists():
        raise OutOfScope(f"this link does not cover that {what}")
    return obj


def _unit(basis, item=None, contract=None):
    """The unit a quantity was given in, from the product's own ladder.

    The form asks "packs or single units?" rather than for a unit name, so a
    supplier cannot type "ctn" where the ledger holds "carton" and split one
    balance into two that never add up.
    """
    commodity = (item.commodity if item is not None else None) or (contract.commodity if contract else None)
    if basis == "base":
        unit = (item.base_unit if item else "") or (commodity.base_unit if commodity else "")
    else:
        unit = (
            (item.pack_unit if item else "")
            or (contract.quantity_unit if contract else "")
            or (commodity.pack_unit if commodity else "")
        )
    if not unit:
        raise ValueError(f"this product has no {'single' if basis == 'base' else 'pack'} unit on file")
    return unit


def _iso(value):
    return value.isoformat() if value else None


def _provenance(link):
    """Who told us: the supplier, or the partner the link was issued to.

    A link issued to the organisation that runs one of its supply points, or
    that is the buyer on one of its orders, belongs to a partner, and what it
    records is the partner's word. Every other link is a supplier's.
    """
    is_partner = (
        link.supply_points.filter(managed_by_org_id=link.org_id).exists()
        or link.contracts.filter(buyer_org_id=link.org_id).exists()
    )
    return {"source": "partner_reported" if is_partner else SOURCE, "recorded_by_org_id": link.org_id}


def _drop_empty(data):
    return {key: value for key, value in data.items() if value not in (None, "")}


# ---- the actions -----------------------------------------------------------
#
# Each takes the scope and the form's cleaned data and returns
# (operation name, payload, audit action). None of them writes anything.


def _confirm_order(scope, data):
    contract = _require(scope.contracts, data.get("contract"), "order")
    if contract.status == "draft":
        raise ValueError(f"order {contract} has not been placed yet, so there is nothing to confirm")
    if contract.status not in CONFIRMABLE:
        raise ValueError(f"order {contract} is already {contract.status.replace('_', ' ')}")
    # A status change, not a new record: who recorded the order stays who
    # recorded it. The supplier's part is kept on the submission and the audit.
    payload = {"contract_id": contract.pk, "data": {"status": "confirmed"}}
    return "contract_update", payload, Action.UPDATE


def _confirm_payment(scope, data):
    payment = _require(scope.payments, data.get("payment"), "payment")
    received_on = data.get("received_on") or timezone.localdate()
    # The payee's own word, which is what payment_confirm records: the date
    # they say it arrived. Until it is set, payment_unconfirmed keeps firing.
    return "payment_confirm", {"payment_id": payment.pk, "confirmed_on": received_on.isoformat()}, Action.UPDATE


def _record_shipment(scope, data):
    contract = _require(scope.contracts, data.get("contract"), "order")
    status = data.get("status") or "dispatched"
    if status not in SUPPLIER_SHIPMENT_STATUSES:
        raise ValueError(f"a dispatch cannot be recorded as {status!r}")
    line = _drop_empty(
        {
            "item_id": contract.item_id,
            "batch": data.get("batch"),
            "expiry": _iso(data.get("expiry")),
            "quantity": str(data["quantity"]),
            "quantity_unit": _unit(data.get("unit_basis"), contract.item, contract),
        }
    )
    shipment = _drop_empty(
        {
            "contract_id": contract.pk,
            "reference": data.get("reference"),
            "status": status,
            "dispatched_on": _iso(data.get("dispatched_on")),
            "expected_on": _iso(data.get("expected_on")),
            "carrier": data.get("carrier"),
        }
    )
    return (
        "shipment_record",
        {"data": {**shipment, "lines": [line], **_provenance(scope.link)}},
        Action.CREATE,
    )


def _update_shipment(scope, data):
    shipment = _require(scope.shipments, data.get("shipment"), "dispatch")
    status = data.get("status")
    if status not in SUPPLIER_SHIPMENT_STATUSES:
        raise ValueError(f"a dispatch cannot be moved to {status!r}")
    changes = _drop_empty({"status": status, "expected_on": _iso(data.get("expected_on"))})
    payload = {
        "shipment_id": shipment.pk,
        # `contract_id` and `source` are required by the shipment schema on an
        # update too; neither is a change. Who recorded the dispatch stays who
        # recorded it; the supplier's part is on the submission and the audit.
        "data": {"contract_id": shipment.contract_id, "source": shipment.source, **changes},
    }
    return "shipment_update", payload, Action.UPDATE


def _record_receipt(scope, data):
    contract = _require(scope.contracts, data.get("contract"), "order")
    point = _require(scope.supply_points, data.get("supply_point"), "supply point")
    line = _drop_empty(
        {
            "item_id": contract.item_id,
            "batch": data.get("batch"),
            "expiry": _iso(data.get("expiry")),
            "quantity_accepted": str(data["quantity_accepted"]),
            "quantity_rejected": str(data["quantity_rejected"]) if data.get("quantity_rejected") else None,
            "rejection_reason": data.get("rejection_reason"),
            "quantity_unit": _unit(data.get("unit_basis"), contract.item, contract),
        }
    )
    shipment = _require(scope.shipments, data["shipment"], "dispatch") if data.get("shipment") else None
    receipt = _drop_empty(
        {
            "contract_id": contract.pk,
            "shipment_id": shipment.pk if shipment else None,
            "supply_point_id": point.pk,
            "reference": data.get("reference"),
            "received_on": _iso(data.get("received_on") or timezone.localdate()),
        }
    )
    return "receipt_record", {"data": {**receipt, "lines": [line], **_provenance(scope.link)}}, Action.CREATE


def _record_stock_count(scope, data):
    point = _require(scope.supply_points, data.get("supply_point"), "supply point")
    item = _require(scope.items, data.get("item"), "product")
    count = _drop_empty(
        {
            "supply_point_id": point.pk,
            "item_id": item.pk,
            "commodity_slug": item.commodity.slug,
            "batch": data.get("batch"),
            # A physical count is an observation: it is kept beside the ledger
            # and the difference shows as a variance. A supplier cannot post an
            # override, which would rewrite our ledger on their word.
            "kind": "physical_count",
            "counted_on": _iso(data.get("counted_on") or timezone.localdate()),
            "quantity": str(data["quantity"]),
            "quantity_unit": _unit(data.get("unit_basis"), item),
        }
    )
    return "stock_count_record", {"data": {**count, **_provenance(scope.link)}}, Action.CREATE


def _record_release(scope, data):
    source_point = _require(scope.supply_points, data.get("from_supply_point"), "supply point")
    destination = _require(scope.supply_points, data.get("to_supply_point"), "supply point")
    if source_point.pk == destination.pk:
        raise ValueError("a release has to go somewhere else: pick a different destination")
    item = _require(scope.items, data.get("item"), "product")
    movement = _drop_empty(
        {
            "kind": "transfer",
            "occurred_on": _iso(data.get("occurred_on") or timezone.localdate()),
            "from_supply_point_id": source_point.pk,
            "to_supply_point_id": destination.pk,
            "item_id": item.pk,
            "commodity_slug": item.commodity.slug,
            "batch": data.get("batch"),
            "quantity": str(data["quantity"]),
            "quantity_unit": _unit(data.get("unit_basis"), item),
            "reference": data.get("reference"),
        }
    )
    return "movement_record", {"data": {**movement, **_provenance(scope.link)}}, Action.CREATE


ACTIONS = {
    "confirm_order": _confirm_order,
    "confirm_payment": _confirm_payment,
    "record_shipment": _record_shipment,
    "update_shipment": _update_shipment,
    "record_receipt": _record_receipt,
    "record_stock_count": _record_stock_count,
    "record_release": _record_release,
}


def _quantity(value, unit):
    return quantity_phrase(value, unit)


def _describe_receipt(rid):
    from connect_labs.supply_chain.models import Receipt

    receipt = Receipt.objects.filter(pk=rid).select_related("supply_point").first()
    if receipt is None:
        return ""
    parts = []
    for line in receipt.lines.all():
        text = f"{_quantity(line.quantity_accepted, line.quantity_unit)} accepted"
        if line.quantity_rejected:
            reason = f" ({line.rejection_reason})" if line.rejection_reason else ""
            text += f", {quantity_digits(line.quantity_rejected)} rejected{reason}"
        if line.batch:
            text += f", batch {line.batch}"
        parts.append(text)
    ref = f"{receipt.reference}: " if receipt.reference else ""
    return ref + "; ".join(parts) + f" at {receipt.supply_point.name}"


def _describe_movement(rid):
    movement = Movement.objects.filter(pk=rid).select_related("from_supply_point", "to_supply_point").first()
    if movement is None:
        return ""
    text = _quantity(movement.quantity, movement.quantity_unit)
    if movement.from_supply_point and movement.to_supply_point:
        text += f" from {movement.from_supply_point.name} to {movement.to_supply_point.name}"
    if movement.batch:
        text += f", batch {movement.batch}"
    return text


def _describe_count(rid):
    from connect_labs.supply_chain.models import StockCount

    count = StockCount.objects.filter(pk=rid).select_related("supply_point").first()
    return f"{_quantity(count.quantity, count.quantity_unit)} at {count.supply_point.name}" if count else ""


def _describe_payment(rid):
    payment = Payment.objects.filter(pk=rid).first()
    if payment is None or payment.confirmed_by_payee_on is None:
        return ""
    ref = f" ({payment.reference})" if payment.reference else ""
    received = payment.confirmed_by_payee_on.isoformat()
    return f"{payment.currency} {money_digits(payment.amount)}{ref} received on {received}"


def _describe_contract(rid):
    contract = Contract.objects.filter(pk=rid).first()
    return f"{contract.reference or contract} is {contract.status.replace('_', ' ')}" if contract else ""


def _describe_shipment(rid):
    """What a dispatch carried, and where it stood: "AWB-31: 40 cartons, batch
    B-12 — dispatched". The quantity and batch are what the supplier typed and
    what the programme will receive against; a status alone read back neither."""
    shipment = Shipment.objects.filter(pk=rid).prefetch_related("lines").first()
    if shipment is None:
        return ""
    parts = []
    for line in shipment.lines.all():
        text = _quantity(line.quantity, line.quantity_unit)
        if line.batch:
            text += f", batch {line.batch}"
        parts.append(text)
    status = shipment.status.replace("_", " ")
    name = shipment.reference or "Dispatch"
    return f"{name}: {'; '.join(parts)} — {status}" if parts else f"{name} is {status}"


_DESCRIBERS = {
    "receipt_record": _describe_receipt,
    "movement_record": _describe_movement,
    "stock_count_record": _describe_count,
    "payment_confirm": _describe_payment,
    "contract_update": _describe_contract,
    "shipment_record": _describe_shipment,
    "shipment_update": _describe_shipment,
}


def _read_back(operation, result_id) -> str:
    describer = _DESCRIBERS.get(operation)
    if describer is None or result_id is None:
        return ""
    return describer(result_id)


def describe(submission) -> str:
    """What one submission put on the record, in the supplier's own terms.

    Read back from the row the write produced -- so it repeats what the
    programme holds, not what the browser sent -- AT THE MOMENT OF THE WRITE,
    and kept on the submission. Re-reading the row later made an early
    submission report the row's current state: a dispatch recorded as
    dispatched read "at customs" once somebody moved it on.

    Submissions made before the snapshot existed are read back live, the best
    there is for them. Empty when the row is gone.
    """
    if submission.summary:
        return submission.summary
    return _read_back(submission.operation, submission.result_id)


def _contract_of(operation, result_id):
    """The order a write touched, or None. Kept on the submission so the order
    page can filter in the database."""
    from connect_labs.supply_chain.models import Receipt

    if result_id is None:
        return None
    if operation == "contract_update":
        return result_id
    if operation == "receipt_record":
        return Receipt.objects.filter(pk=result_id).values_list("contract_id", flat=True).first()
    if operation == "payment_confirm":
        return Payment.objects.filter(pk=result_id).values_list("invoice__contract_id", flat=True).first()
    if operation in ("shipment_record", "shipment_update"):
        return Shipment.objects.filter(pk=result_id).values_list("contract_id", flat=True).first()
    return None


def updates_for_contract(contract) -> list[dict]:
    """Every submission through any link that touched this order, newest first.

    The programme-side half of the update link: the order page says what the
    supplier reported, through which organisation's link, and when -- rather
    than leaving the confirmation to be inferred from a status word.

    Asked of the database: a submission carries the order it touched, so this
    is one query however many links and submissions the programme has.
    """
    submissions = UpdateLinkSubmission.objects.filter(
        link__program_id=contract.program_id, contract=contract
    ).select_related("link__org")
    return [
        {
            "org": submission.link.org.name,
            "title": submission.action.replace("_", " "),
            "detail": describe(submission),
            "at": submission.submitted_at,
        }
        for submission in submissions
    ]


def link_access(link):
    """The data access a link's writes run under.

    SYSTEM, because there is no user: the page authenticates nobody, and the
    token is the authority. `labs/access/scopes.py` asks that every such entry
    point be greppable and pinned (test_scope_authorisation.py lists this
    module). With no user and no request, `stamp_provenance` leaves the
    payload alone -- so the `source` and `recorded_by_org_id` set above are
    what the row records, and they are set here, from the link, never from
    anything the browser sent.
    """
    from connect_labs.supply_chain.data_access import SupplyDataAccess

    return SupplyDataAccess(program_id=link.program_id, caller=SYSTEM)


def submit(link, action, data) -> dict:
    """Carry out one action through its ordinary operation. Returns the operation's result."""
    link = UpdateLink.objects.select_related("org").filter(pk=link.pk).first()
    if link is None or not link.is_usable:
        raise OutOfScope("this link is no longer valid")
    handler = ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"no action {action!r}")

    operation, payload, audit_action = handler(scope_for(link), data)
    result = call_operation(operation, link_access(link), payload)

    result_id = result.get("id") if isinstance(result, dict) else None
    now = timezone.now()
    UpdateLinkSubmission.objects.create(
        link=link,
        action=action,
        operation=operation,
        result_type=operation.split("_", 1)[0],
        result_id=result_id,
        summary=_read_back(operation, result_id),
        contract_id=_contract_of(operation, result_id),
        submitted_at=now,
    )
    UpdateLink.objects.filter(pk=link.pk).update(last_used_at=now)
    audit_record(
        audit_action,
        resource_type=f"supply_{operation}",
        resource_id=result_id,
        program_id=link.program_id,
        labs_only=synthetic_scopes.is_synthetic(link.program_id),
        metadata={
            "via": "supply_update_link",
            "update_link_id": link.pk,
            "org_id": link.org_id,
            "action": action,
            "operation": operation,
        },
    )
    return result
