"""Every supply capability, declared exactly once.

The HTTP API (api_views.py) and the MCP server (mcp_tools.py) are both
generated from this registry, so the two surfaces cannot drift: there is no
second list to keep in step. A Django view must not mutate a record except by
calling one of these.

Handlers take a SupplyDataAccess first and return JSON-serialisable dicts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jsonschema

from connect_labs.supply_chain.values import Money


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    input_schema: dict
    handler: Callable[..., Any]
    is_write: bool = False


_REGISTRY: dict[str, Operation] = {}


def register_operation(*, name: str, summary: str, input_schema: dict, is_write: bool = False):
    def decorator(fn):
        if name in _REGISTRY:
            raise ValueError(f"operation {name!r} already registered")
        _REGISTRY[name] = Operation(
            name=name,
            summary=summary,
            input_schema=input_schema,
            handler=fn,
            is_write=is_write,
        )
        return fn

    return decorator


def all_operations() -> dict[str, Operation]:
    return dict(_REGISTRY)


def get_operation(name: str) -> Operation:
    return _REGISTRY[name]


def call_operation(name: str, access, payload: dict | None = None) -> Any:
    operation = get_operation(name)
    payload = payload or {}
    jsonschema.validate(payload, operation.input_schema)
    return operation.handler(access, **payload)


# ---- serialisation helpers ---------------------------------------------


def _figure(value):
    if isinstance(value, Money):
        return {"amount": str(value.amount), "currency": value.currency}
    return {"unconfirmed": list(value.reasons)}


def _record(record) -> dict:
    return {"id": record.id, **record.data}


def _obj(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_ID = {"type": "integer"}


# A record's `data` is deliberately open — LabsRecords carry whatever a domain needs, and
# enumerating every field here would fight that. But the spec claims this system enforces
# that amounts are positive and enum fields hold enum values, and an operation schema is
# the only place that claim can bind all three surfaces at once (web form, HTTP API, MCP
# tool) while doubling as the documentation an agent reads before calling. So: constrain
# the fields whose wrong values are silent, leave the rest open.
def _data_with(**properties) -> dict:
    """An object schema that pins the named properties and permits the rest."""
    return {"type": "object", "properties": properties, "additionalProperties": True}


# A JSON Schema `pattern` is a no-op against a non-string instance — it constrains the
# string branch of a `["number", "string"]` union and nothing else. A schema of
# {"type": ["number", "string"], "pattern": ...} therefore lets `12.50` (a JSON float)
# straight through, and money is Decimal, never float: a float that reaches the handler
# has already lost the precision this whole design refuses to lose silently. So money
# and quantity get two different schemas, not one shared "positive number" schema:
#
#   _MONEY    — string only. A JSON number cannot carry a monetary amount without a
#               possible silent rounding, so the contract refuses it outright rather
#               than accept-and-round. The description is what a caller reads when its
#               float gets rejected.
#   _QUANTITY — a decimal string OR a JSON number, because "quantity": 3 is a reasonable
#               thing for a caller to write and rejecting it buys nothing — but positive
#               on BOTH branches, via `anyOf` rather than a shared `pattern`, so a zero or
#               negative quantity is refused whichever shape it arrives in.
_MONEY = {
    "type": "string",
    "pattern": r"^\d*\.?\d+$",
    "description": (
        'A monetary amount, as a decimal string (e.g. "12.50"), never a JSON number — '
        "money is Decimal, never float, and a float silently loses precision that a "
        "string does not."
    ),
}

_NONZERO_DECIMAL_STRING = r"^(?!0*\.?0*$)\d*\.?\d+$"

_QUANTITY = {
    "anyOf": [
        {"type": "string", "pattern": _NONZERO_DECIMAL_STRING},
        {"type": "number", "exclusiveMinimum": 0},
    ]
}

_NON_NEGATIVE_INT = {"type": "integer", "minimum": 0}

_QUOTE_DATA = _data_with(
    round_id=_ID,
    supplier_id=_ID,
    item_id=_ID,
    commodity_slug={"type": "string", "minLength": 1},
    as_quoted_amount=_MONEY,
    as_quoted_unit={"enum": ["per_base_unit", "per_pack", "per_lot_total", "per_metric_tonne"]},
    as_quoted_currency={"type": "string", "minLength": 3, "maxLength": 3},
    quantity_basis=_QUANTITY,
    pack_spec_source={"enum": ["stated_on_quote", "trade_item_confirmed", "not_stated"]},
    base_per_pack_stated=_NON_NEGATIVE_INT,
    base_unit_grams_stated=_NON_NEGATIVE_INT,
    freight_basis={"enum": ["included", "excluded", "not_specified"]},
    duties_basis={"enum": ["included", "excluded", "not_specified"]},
    freight_amount=_MONEY,
    duties_amount=_MONEY,
    fx_rate_to_usd=_MONEY,
    shelf_life_months_stated=_NON_NEGATIVE_INT,
    lead_time_days=_NON_NEGATIVE_INT,
)

_ROUND_DATA = _data_with(
    label={"type": "string", "minLength": 1},
    status={"enum": ["draft", "open", "closed", "awarded"]},
    lines={
        "type": "array",
        "items": _data_with(
            commodity_slug={"type": "string", "minLength": 1},
            quantity=_QUANTITY,
            quantity_unit={"type": "string", "minLength": 1},
        ),
    },
    delivery_point=_data_with(
        name={"type": "string"},
        city={"type": "string"},
        country={"type": "string"},
        country_name={"type": "string"},
        incoterm_requested={"type": "string"},
    ),
    reminder_interval_days=_NON_NEGATIVE_INT,
)

_ITEM_DATA = _data_with(
    sku={"type": "string", "minLength": 1},
    commodity_slug={"type": "string", "minLength": 1},
    base_per_pack=_NON_NEGATIVE_INT,
    pack_per_case=_NON_NEGATIVE_INT,
    base_unit_grams=_NON_NEGATIVE_INT,
    shelf_life_months=_NON_NEGATIVE_INT,
    status={"enum": ["active", "discontinued"]},
)

_PURCHASE_DATA = _data_with(
    round_id=_ID,
    supplier_id=_ID,
    commodity_slug={"type": "string", "minLength": 1},
    quantity=_QUANTITY,
    amount_paid=_MONEY,
    currency={"type": "string", "minLength": 3, "maxLength": 3},
)

_COMMODITY_DATA = _data_with(
    slug={"type": "string", "minLength": 1},
    name={"type": "string", "minLength": 1},
    category={
        "enum": [
            "therapeutic_food",
            "micronutrient",
            "antibiotic",
            "antimalarial",
            "diagnostic",
            "anthelmintic",
            "equipment",
            "consumable",
        ]
    },
    base_per_pack=_NON_NEGATIVE_INT,
    base_unit_grams=_NON_NEGATIVE_INT,
    shelf_life_months_minimum=_NON_NEGATIVE_INT,
    course_definition=_data_with(
        base_units_per_day=_QUANTITY,
        days_per_course=_NON_NEGATIVE_INT,
        base_units_per_course=_NON_NEGATIVE_INT,
        source={"type": "string"},
    ),
)

_OUTREACH_DATA = _data_with(
    round_id=_ID,
    supplier_id=_ID,
    channel={"enum": ["manual", "api", "mcp", "ses"]},
    responded={"type": "boolean"},
    response_kind={"enum": ["quote", "declined", "needs_info", "no_reply"]},
)

_SUPPLIER_DATA = _data_with(
    name={"type": "string", "minLength": 1},
    type={"enum": ["manufacturer", "distributor", "trader"]},
    status={
        "enum": [
            "identified",
            "contacted",
            "responsive",
            "quoting",
            "awarded",
            "declined",
            "unusable",
        ]
    },
)


# ---- reference ---------------------------------------------------------


@register_operation(
    name="commodity_list",
    summary=(
        "List every commodity in the catalogue, with its unit ladder, course "
        "definition and specification requirements."
    ),
    input_schema=_obj({}),
)
def commodity_list(access):
    return [_record(c) for c in access.list_commodities()]


@register_operation(
    name="commodity_upsert",
    summary=(
        "Create or update a commodity by slug. Include course_definition to "
        "unlock per-course and per-child cost figures."
    ),
    input_schema=_obj({"data": _COMMODITY_DATA}, required=("data",)),
    is_write=True,
)
def commodity_upsert(access, data):
    return _record(access.upsert_commodity(data))


@register_operation(
    name="item_list",
    summary=(
        "List the master item list — every trade item we can buy or track, with its "
        "commodity, pack configuration, GS1 keys and specification. An item is a "
        "specific branded product; a commodity is the type. Two suppliers' RUTF can "
        "be 144 and 150 to the carton, and only the item knows which."
    ),
    input_schema=_obj({}),
)
def item_list(access):
    return [_record(i) for i in access.list_items()]


@register_operation(
    name="item_get",
    summary="Fetch one trade item by record id, with its pack configuration and specification attributes.",
    input_schema=_obj({"item_id": _ID}, required=("item_id",)),
)
def item_get(access, item_id):
    item = access.get_item(item_id)
    return _record(item) if item else None


@register_operation(
    name="item_upsert",
    summary=(
        "Create or update a trade item by sku. Give base_per_pack and base_unit_grams "
        "as the manufacturer states them, not as the commodity assumes. Any GTIN is "
        "validated against its GS1 check digit and a bad one is refused."
    ),
    input_schema=_obj({"data": _ITEM_DATA}, required=("data",)),
    is_write=True,
)
def item_upsert(access, data):
    return _record(access.upsert_item(data))


@register_operation(
    name="supplier_list",
    summary=(
        "List suppliers, optionally filtered by a case-insensitive substring "
        "of name or contact email. Read this before creating a supplier to "
        "avoid making a duplicate."
    ),
    input_schema=_obj({"search": {"type": "string"}}),
)
def supplier_list(access, search=None):
    return [_record(s) for s in access.list_suppliers(search=search)]


@register_operation(
    name="supplier_get",
    summary="Fetch one supplier by record id, with contacts, qualifications and status.",
    input_schema=_obj({"supplier_id": _ID}, required=("supplier_id",)),
)
def supplier_get(access, supplier_id):
    supplier = access.get_supplier(supplier_id)
    return _record(supplier) if supplier else None


@register_operation(
    name="supplier_create",
    summary="Create a supplier. Call supplier_list first if there is any chance this supplier is already on file.",
    input_schema=_obj({"data": _SUPPLIER_DATA}, required=("data",)),
    is_write=True,
)
def supplier_create(access, data):
    return _record(access.create_supplier(data))


@register_operation(
    name="supplier_update",
    summary=(
        "Update a supplier's details — contacts, status, qualifications, or " "the connect_organization_id binding."
    ),
    input_schema=_obj({"supplier_id": _ID, "data": _SUPPLIER_DATA}, required=("supplier_id", "data")),
    is_write=True,
)
def supplier_update(access, supplier_id, data):
    return _record(access.update_supplier(supplier_id, data))
