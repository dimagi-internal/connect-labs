"""Fulfilment operations: parties, and the contract that names who is buying.

Registered into the one registry in operations.py, so the HTTP API and the
MCP server both get them without a second list to keep in step.

These replace the old `purchase_record` / `purchase_list` pair. A purchase
collapsed three different facts with three different dates -- a commitment, a
bill and a settlement -- into one row, which meant the system could not
answer "what have we committed but not paid", and could not represent the
arrangement this tier exists for: somebody else raising the purchase order.
"""

from connect_labs.supply_chain import records
from connect_labs.supply_chain.operations import (
    _CONTRACT_DATA,
    _CONTRACT_DATA_CREATE,
    ID,
    MONEY_NONZERO,
    QUANTITY,
    _data_with,
    figure,
    obj,
    record,
    register_operation,
)

_PARTY_DATA = _data_with(
    ("slug", "name", "kind"),
    slug={"type": "string", "minLength": 1},
    name={"type": "string", "minLength": 1},
    kind={"enum": list(records.PARTY_KINDS)},
    connect_organization_id=ID,
    roles={"type": "array", "items": {"type": "string"}},
    country={"type": "string", "maxLength": 2},
)


# ---- parties -----------------------------------------------------------


@register_operation(
    name="party_list",
    summary=(
        "List the organisations that can act in this programme's supply chain — us, "
        "local implementing partners, procurement agencies. Read this before creating "
        "a contract: the buyer of record must be one of them."
    ),
    input_schema=obj({}),
)
def party_list(access):
    return [record(p) for p in access.list_parties()]


@register_operation(
    name="party_upsert",
    summary=(
        "Create or update a party by slug. Set connect_organization_id to bind it to a "
        "Connect organisation, which is what lets that partner's own staff sign in and "
        "record their own shipments, receipts and stock counts. A party with no binding "
        "still works — we record on their behalf, marked as reported."
    ),
    input_schema=obj({"data": _PARTY_DATA}, required=("data",)),
    is_write=True,
)
def party_upsert(access, data):
    return record(access.upsert_party(data))


# ---- contracts ---------------------------------------------------------


@register_operation(
    name="contract_list",
    summary=(
        "List contracts for this programme, optionally filtered by round or status. "
        "A contract is a commitment; an award is only a decision, and the two are "
        "separate because the party that decides is often not the party that buys."
    ),
    input_schema=obj({"round_id": ID, "status": {"enum": list(records.CONTRACT_STATUSES)}}),
)
def contract_list(access, round_id=None, status=None):
    return [record(c) for c in access.list_contracts(round_id=round_id, status=status)]


@register_operation(
    name="contract_get",
    summary="Fetch one contract, with its buyer of record, price build-up and duty-relief state.",
    input_schema=obj({"contract_id": ID}, required=("contract_id",)),
)
def contract_get(access, contract_id):
    return record(access.get_contract(contract_id))


@register_operation(
    name="contract_create",
    summary=(
        "Record a contract or purchase order. buyer_of_record is required and has no "
        "default: import duty and VAT depend on who imports, so the same quoted price "
        "yields a different landed cost depending on whether we, a local partner or an "
        "agency is the buyer. Set duty_relief_claimed only alongside a duty_exemption "
        "document — a claimed relief with nothing behind it derives as Unconfirmed, not "
        "as zero. source says who told you this; it is required for everything below "
        "the contract because those are stages we do not witness."
    ),
    input_schema=obj({"data": _CONTRACT_DATA_CREATE}, required=("data",)),
    is_write=True,
)
def contract_create(access, data):
    return record(access.create_contract(data))


@register_operation(
    name="contract_update",
    summary=(
        "Update a contract — typically to record the purchase-order reference once the "
        "buyer supplies it, to attach the duty-exemption document, or to move its status "
        "as goods arrive."
    ),
    input_schema=obj({"contract_id": ID, "data": _CONTRACT_DATA}, required=("contract_id", "data")),
    is_write=True,
)
def contract_update(access, contract_id, data):
    return record(access.update_contract(contract_id, data))


# ---- shipments, receipts, invoices, documents --------------------------
#
# These are the operations a local partner uses when the partner is the buyer
# of record. They are the SAME operations we use -- what differs is `source`
# and `recorded_by_party` on the row. A partner-only write path would be a
# second set of rules to keep in step, and a second place for a bug to hide.

_DATE = {"type": "string", "format": "date"}

_SHIPMENT_LINE = _data_with(
    ("quantity", "quantity_unit"),
    item_id=ID,
    batch={"type": "string"},
    expiry=_DATE,
    quantity=QUANTITY,
    quantity_unit={"type": "string", "minLength": 1},
)

_SHIPMENT_DATA = _data_with(
    ("contract_id", "source"),
    contract_id=ID,
    reference={"type": "string"},
    sscc={"type": "string", "maxLength": 18},
    status={"enum": list(records.SHIPMENT_STATUSES)},
    dispatched_on=_DATE,
    expected_on=_DATE,
    carrier={"type": "string"},
    lines={"type": "array", "items": _SHIPMENT_LINE},
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_RECEIPT_DATA = _data_with(
    ("supply_point_id", "received_on", "lines", "source"),
    contract_id=ID,
    shipment_id=ID,
    supply_point_id=ID,
    commodity_slug={"type": "string", "minLength": 1},
    reference={"type": "string"},
    received_on=_DATE,
    lines={
        "type": "array",
        "minItems": 1,
        "items": _data_with(
            ("quantity_accepted", "quantity_unit"),
            item_id=ID,
            batch={"type": "string"},
            expiry=_DATE,
            # Zero accepted is a real receipt: the consignment arrived and
            # was refused in full.
            quantity_accepted={"anyOf": [{"type": "string"}, {"type": "number", "minimum": 0}]},
            quantity_rejected={"anyOf": [{"type": "string"}, {"type": "number", "minimum": 0}]},
            rejection_reason={"type": "string"},
            quantity_unit={"type": "string", "minLength": 1},
        ),
    },
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_INVOICE_DATA = _data_with(
    ("contract_id", "source"),
    contract_id=ID,
    reference={"type": "string"},
    issued_on=_DATE,
    status={"enum": list(records.INVOICE_STATUSES)},
    currency={"type": "string", "minLength": 3, "maxLength": 3},
    amount=MONEY_NONZERO,
    quantity_billed=QUANTITY,
    quantity_unit={"type": "string"},
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_PAYMENT_DATA = _data_with(
    ("invoice_id", "paid_on", "amount", "source"),
    invoice_id=ID,
    paid_on=_DATE,
    amount=MONEY_NONZERO,
    currency={"type": "string", "minLength": 3, "maxLength": 3},
    method={"type": "string"},
    reference={"type": "string"},
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_DOCUMENT_DATA = _data_with(
    ("kind", "source"),
    kind={"enum": list(records.DOCUMENT_KINDS)},
    title={"type": "string"},
    filename={"type": "string"},
    content_type={"type": "string"},
    content_base64={"type": "string"},
    external_url={"type": "string"},
    contract_id=ID,
    shipment_id=ID,
    receipt_id=ID,
    invoice_id=ID,
    supply_point_id=ID,
    supplier_id=ID,
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)


@register_operation(
    name="shipment_list",
    summary="List dispatches against this programme's contracts, with their lines and batches.",
    input_schema=obj({"contract_id": ID, "status": {"enum": list(records.SHIPMENT_STATUSES)}}),
)
def shipment_list(access, contract_id=None, status=None):
    return [record(s) for s in access.list_shipments(contract_id=contract_id, status=status)]


@register_operation(
    name="shipment_record",
    summary=(
        "Record a dispatch. Lines carry batch and expiry, because that is where those facts "
        "first become knowable and a receipt later matches against them. A shipment posts "
        "nothing to stock: goods in transit are real but they are not cover, and counting "
        "them as on hand is how a network reads months of stock it does not have."
    ),
    input_schema=obj({"data": _SHIPMENT_DATA}, required=("data",)),
    is_write=True,
)
def shipment_record(access, data):
    return record(access.record_shipment(data))


@register_operation(
    name="shipment_update",
    summary=(
        "Update a shipment — most often its status as it moves: dispatched, in_transit, "
        "at_customs, cleared, delivered. Everything from dispatched to cleared counts as "
        "in transit and stays out of stock on hand."
    ),
    input_schema=obj({"shipment_id": ID, "data": _SHIPMENT_DATA}, required=("shipment_id", "data")),
    is_write=True,
)
def shipment_update(access, shipment_id, data):
    return record(access.update_shipment(shipment_id, data))


@register_operation(
    name="receipt_list",
    summary="List goods received notes, with accepted and rejected quantities per batch.",
    input_schema=obj({"contract_id": ID, "supply_point_id": ID}),
)
def receipt_list(access, contract_id=None, supply_point_id=None):
    return [record(r) for r in access.list_receipts(contract_id=contract_id, supply_point_id=supply_point_id)]


@register_operation(
    name="receipt_record",
    summary=(
        "Record a goods received note — the event that brings stock into existence. Posts one "
        "movement per accepted line. Rejected quantity is kept on the line as a record of what "
        "was refused and never enters the ledger, because goods turned away at the door were "
        "never stock. Give commodity_slug when there is no contract to take it from."
    ),
    input_schema=obj({"data": _RECEIPT_DATA}, required=("data",)),
    is_write=True,
)
def receipt_record(access, data):
    return record(access.record_receipt(data))


@register_operation(
    name="invoice_list",
    summary="List supplier invoices against this programme's contracts, with their payments.",
    input_schema=obj({"contract_id": ID, "status": {"enum": list(records.INVOICE_STATUSES)}}),
)
def invoice_list(access, contract_id=None, status=None):
    return [record(i) for i in access.list_invoices(contract_id=contract_id, status=status)]


@register_operation(
    name="invoice_record",
    summary=(
        "Record a supplier invoice. Give quantity_billed so the three-way match can compare it "
        "with what actually arrived — an invoice for more than was received is the single most "
        "common discrepancy, and the only thing that catches it is that comparison."
    ),
    input_schema=obj({"data": _INVOICE_DATA}, required=("data",)),
    is_write=True,
)
def invoice_record(access, data):
    return record(access.record_invoice(data))


@register_operation(
    name="invoice_update",
    summary="Update an invoice — typically to query or reject it, or to attach its document.",
    input_schema=obj({"invoice_id": ID, "data": _INVOICE_DATA}, required=("invoice_id", "data")),
    is_write=True,
)
def invoice_update(access, invoice_id, data):
    return record(access.update_invoice(invoice_id, data))


@register_operation(
    name="payment_record",
    summary=(
        "Record a settlement against an invoice. The invoice's status follows from what has "
        "been paid rather than being asserted, so 'paid' cannot be true of an invoice with "
        "money outstanding."
    ),
    input_schema=obj({"data": _PAYMENT_DATA}, required=("data",)),
    is_write=True,
)
def payment_record(access, data):
    return record(access.record_payment(data))


@register_operation(
    name="document_list",
    summary=(
        "List documents, optionally filtered by kind or by what they evidence. Two derivations "
        "depend on a document existing at all: a claimed duty relief, and a batch's conformity."
    ),
    input_schema=obj(
        {
            "kind": {"enum": list(records.DOCUMENT_KINDS)},
            "contract_id": ID,
            "shipment_id": ID,
            "receipt_id": ID,
            "invoice_id": ID,
            "supply_point_id": ID,
            "supplier_id": ID,
        }
    ),
)
def document_list(access, kind=None, **links):
    return [record(d) for d in access.list_documents(kind=kind, **links)]


@register_operation(
    name="document_attach",
    summary=(
        "Attach evidence: either upload the file as content_base64, or point at where it "
        "already lives with external_url. Give one, not both — two locations for one document "
        "is two documents that can disagree. Uploads are stored through the configured storage "
        "and hashed, so a later copy can be checked against the one a derivation was based on. "
        "Anything over 12 MB belongs somewhere durable with an external_url pointing at it."
    ),
    input_schema=obj({"data": _DOCUMENT_DATA}, required=("data",)),
    is_write=True,
)
def document_attach(access, data):
    return record(access.attach_document(data))


# ---- derived reads -----------------------------------------------------


@register_operation(
    name="contract_landed_cost",
    summary=(
        "The all-in cost of a contract, and the buyer it assumed. Import duty and VAT fall on "
        "the importer, so the same goods at the same price cost different amounts depending on "
        "whether we, a local partner or an agency is the buyer of record — the buyer is an "
        "input, never a default. A duty relief claimed with no exemption document attached "
        "comes back unconfirmed, not zero. Set compare_buyers to cost the same contract under "
        "each of the three, which is what the choice is actually worth."
    ),
    input_schema=obj({"contract_id": ID, "compare_buyers": {"type": "boolean"}}, required=("contract_id",)),
)
def contract_landed_cost(access, contract_id, compare_buyers=False):
    from connect_labs.supply_chain.fulfilment.services import landed

    contract = access._require_contract(contract_id)
    costed = landed.landed_total(contract)
    out = {
        "contract_id": contract.pk,
        "buyer_of_record": costed["buyer_of_record"],
        "currency": costed["currency"],
        "duty_relief_claimed": costed["duty_relief_claimed"],
        "duty_relief_evidenced": costed["duty_relief_evidenced"],
        **{key: figure(costed[key]) for key in ("goods", "freight", "duty", "vat", "landed_total")},
    }
    if compare_buyers:
        out["by_buyer"] = {buyer: figure(value) for buyer, value in landed.compare_buyers(contract).items()}
    return out


@register_operation(
    name="contract_match",
    summary=(
        "The three-way match for a contract: ordered against received against invoiced, plus "
        "what is safe to pay now. Computed, never stored — record a receipt and it recomputes. "
        "payable_now is the value of what actually ARRIVED, never what was billed, which is the "
        "control that catches a supplier invoicing for a consignment still sitting at customs."
    ),
    input_schema=obj({"contract_id": ID}, required=("contract_id",)),
)
def contract_match(access, contract_id):
    from connect_labs.supply_chain.fulfilment.services.match import three_way_match

    contract = access._require_contract(contract_id)
    matched = three_way_match(contract)
    return {
        "contract_id": matched["contract_id"],
        "currency": matched["currency"],
        "status": matched["status"],
        "matches": matched["matches"],
        **{
            key: figure(matched[key]) if matched[key] is not None else None
            for key in (
                "ordered",
                "received",
                "invoiced",
                "outstanding",
                "over_invoiced",
                "billed_amount",
                "paid_amount",
                "payable_now",
            )
        },
    }
