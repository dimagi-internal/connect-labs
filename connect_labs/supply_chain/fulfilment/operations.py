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
from connect_labs.supply_chain.operations import _CONTRACT_DATA, ID, _data_with, obj, record, register_operation

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
    input_schema=obj({"data": _CONTRACT_DATA}, required=("data",)),
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
