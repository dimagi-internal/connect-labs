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

SOURCE = "supplier_reported"

# The statuses a supplier may move a dispatch to. `planned` is ours: it is
# what an order looks like before anything has left.
SUPPLIER_SHIPMENT_STATUSES = ("dispatched", "in_transit", "at_customs", "cleared", "delivered", "lost")

# An order the supplier can still confirm. Anything later has already moved
# past confirmation, and "confirming" a received order would move it back.
CONFIRMABLE = ("draft", "placed")


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
    return {"source": SOURCE, "recorded_by_org_id": link.org_id}


def _drop_empty(data):
    return {key: value for key, value in data.items() if value not in (None, "")}


# ---- the actions -----------------------------------------------------------
#
# Each takes the scope and the form's cleaned data and returns
# (operation name, payload, audit action). None of them writes anything.


def _confirm_order(scope, data):
    contract = _require(scope.contracts, data.get("contract"), "order")
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
