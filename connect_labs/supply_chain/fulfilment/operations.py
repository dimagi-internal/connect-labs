"""Fulfilment operations: organisations, and the contract that names who is buying.

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

# The schema describes LabsOrg's own fields and nothing else. It used to
# REQUIRE a `kind` (programme_org | partner_org | supplier | agency) and accept
# a `roles` list, neither of which LabsOrg has: the schema enum-checked the
# value and `_columns()` then dropped it, so a caller got a 200 and their value
# went nowhere. `kind` was the per-programme role crammed onto the
# organisation, and it now lives on the purchase that has it
# (`Contract.buyer_of_record`) -- an organisation is not a partner in general,
# it is the partner on a particular contract.
_ORG_DATA = _data_with(
    ("slug", "name"),
    slug={"type": "string", "minLength": 1},
    name={"type": "string", "minLength": 1},
    short_name={"type": "string"},
    country={"type": "string", "maxLength": 2},
    connect_organization_id=ID,
    connect_organization_slug={"type": "string"},
    aliases={"type": "array", "items": {"type": "string"}},
    notes={"type": "string"},
)


# ---- organisations -----------------------------------------------------


@register_operation(
    name="org_merge",
    summary=(
        "Merge two organisation rows that turn out to be one organisation: every reference moves to "
        "the one you keep, the merged-away slug is kept as an alias, and the empty row is deleted. "
        "Refused when the two are linked to different Connect organisations."
    ),
    input_schema=obj({"keep_id": ID, "merge_id": ID}, required=("keep_id", "merge_id")),
    is_write=True,
)
def org_merge(access, keep_id, merge_id):
    from connect_labs.labs.org_merge import merge_orgs

    return merge_orgs(keep_id=keep_id, merge_id=merge_id)


@register_operation(
    name="org_list",
    summary=(
        "List the organisations on file — us, local implementing partners, procurement "
        "agencies. Read this before creating a contract: the buyer of record must be "
        "one of them. Organisations are labs-wide, not per programme: an organisation "
        "is the same body wherever it appears."
    ),
    input_schema=obj({}),
)
def org_list(access):
    return [record(o) for o in access.list_orgs()]


@register_operation(
    name="org_upsert",
    summary=(
        "Create or update an organisation by slug. Set connect_organization_id to bind it to a "
        "Connect organisation, which is what lets that partner's own staff sign in and record their "
        "own shipments, receipts and stock counts. Unbound still works — we record on their behalf."
    ),
    input_schema=obj({"data": _ORG_DATA}, required=("data",)),
    is_write=True,
)
def org_upsert(access, data):
    return record(access.upsert_org(data))


# ---- contracts ---------------------------------------------------------


@register_operation(
    name="contract_list",
    summary=(
        "List contracts for this programme, optionally filtered by round or status. "
        "A contract is a commitment; an award is only a decision, and the two are "
        "separate because the organisation that decides is often not the one that buys."
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
        "Record a contract or purchase order. buyer_of_record is required and has no default: import "
        "duty and VAT depend on who imports. Set duty_relief_claimed only alongside a duty_exemption "
        "document. source is required — it says who told you."
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
# and `recorded_by_org` on the row. A partner-only write path would be a
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
    recorded_by_org_id=ID,
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
    recorded_by_org_id=ID,
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
    recorded_by_org_id=ID,
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
    recorded_by_org_id=ID,
)

_DOCUMENT_DATA = _data_with(
    ("kind", "source"),
    kind={"enum": list(records.DOCUMENT_KINDS)},
    title={"type": "string"},
    filename={"type": "string"},
    content_type={"type": "string"},
    content_base64={"type": "string"},
    external_url={"type": "string"},
    source={"enum": list(records.SOURCES)},
    recorded_by_org_id=ID,
    # One `<name>_id` per thing a document can evidence, generated from the
    # single declaration. Written out by hand before, so adding a target
    # meant remembering this list too -- and forgetting it fails as
    # "additionalProperties" rather than as anything that names the cause.
    **{f"{name}_id": ID for name in records.DOCUMENT_LINKS},
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
        "Record a dispatch. Lines carry batch and expiry, which a receipt later matches against. A "
        "shipment posts nothing to stock: goods in transit are real, but they are not cover."
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
        "movement per accepted line. Rejected quantity is kept on the line and never enters the "
        "ledger. Give commodity_slug when there is no contract to take it from."
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
            # From the declaration, like the attach schema. Listed by hand,
            # this was the seventh place the same set of targets lived.
            **{f"{name}_id": ID for name in records.DOCUMENT_LINKS},
        }
    ),
)
def document_list(access, kind=None, **links):
    return [record(d) for d in access.list_documents(kind=kind, **links)]


@register_operation(
    name="document_attach",
    summary=(
        "Attach evidence: either upload the file as content_base64 or point at it with external_url. "
        "Give one, not both. Uploads are hashed, so a later copy can be checked against the one a "
        "derivation used. Over 12 MB, store it elsewhere and use external_url."
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
        "The all-in cost of a contract and the buyer it assumed. The buyer of record is an input, "
        "never a default: duty and VAT fall on the importer. A duty relief with no exemption document "
        "comes back unconfirmed, not zero. Set compare_buyers to cost it under all three."
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
        "The three-way match for a contract: ordered against received against invoiced, plus what is "
        "safe to pay now. Computed, never stored. payable_now is the value of what actually ARRIVED, "
        "never what was billed."
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
