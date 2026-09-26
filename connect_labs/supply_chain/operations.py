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

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import models, records, serializers
from connect_labs.supply_chain.reference import catalogue as reference_catalogue
from connect_labs.supply_chain.values import to_wire

# The published wire shape, per model. See serializers.py for why these are
# a contract and not an implementation detail.
_SERIALIZERS = {
    LabsOrg: serializers.org,
    models.Commodity: serializers.commodity,
    models.Item: serializers.item,
    models.Supplier: serializers.supplier,
    models.Tender: serializers.tender,
    models.Outreach: serializers.outreach,
    models.Quote: serializers.quote,
    models.Award: serializers.award,
    models.AwardApproval: serializers.approval,
    models.Contract: serializers.contract,
    models.Shipment: serializers.shipment,
    models.Charge: serializers.charge,
    models.Receipt: serializers.receipt,
    models.Invoice: serializers.invoice,
    models.Payment: serializers.payment,
    models.Document: serializers.document,
    models.SupplyPoint: serializers.supply_point,
    models.Movement: serializers.movement,
    models.StockCount: serializers.stock_count,
    models.Distribution: serializers.distribution,
    models.Consignment: serializers.consignment,
}

# Re-exported for the tier modules that build on them (see
# fulfilment/operations.py); declared here so one money/quantity contract
# binds every surface.


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    input_schema: dict
    handler: Callable[..., Any]
    is_write: bool = False
    # Engineer-only, and so kept OFF the MCP catalogue. Bulk loads, seeds and
    # imports are run deliberately by someone with a shell (ECS Exec against
    # the labs task, see docs/OUTBOUND_EMAIL.md for the recipe), the way every
    # other labs app does it -- `bootstrap_targeting`, `load_indicators`,
    # `marketplace_import`, `seed_semantic_registry` are all management
    # commands and none of them is an MCP tool.
    #
    # They stay in THIS registry rather than being deleted from it, because
    # their management commands go through `call_operation` and would
    # otherwise lose the schema validation and the provenance stamping that
    # every other write gets. One registry, one validation path, one
    # provenance choke point -- just not advertised to every MCP client.
    internal: bool = False


_REGISTRY: dict[str, Operation] = {}


def register_operation(*, name: str, summary: str, input_schema: dict, is_write: bool = False, internal: bool = False):
    def decorator(fn):
        if name in _REGISTRY:
            raise ValueError(f"operation {name!r} already registered")
        _REGISTRY[name] = Operation(
            name=name,
            summary=summary,
            input_schema=input_schema,
            handler=fn,
            is_write=is_write,
            internal=internal,
        )
        return fn

    return decorator


def agent_operations() -> dict[str, Operation]:
    """The operations an MCP client is offered — everything but the internals."""
    return {name: op for name, op in _REGISTRY.items() if not op.internal}


def all_operations() -> dict[str, Operation]:
    return dict(_REGISTRY)


def _stamp_provenance_import():
    """Imported lazily: identity reads models, and models import records."""
    from connect_labs.supply_chain.identity import stamp_provenance

    return stamp_provenance


def stamp_provenance(access, operation, payload):
    return _stamp_provenance_import()(access, operation, payload)


def get_operation(name: str) -> Operation:
    return _REGISTRY[name]


def call_operation(name: str, access, payload: dict | None = None) -> Any:
    """Validate a payload against its operation's schema, then dispatch.

    Top-level keys whose value is None are dropped before validation: a
    caller that names a parameter with no value has not supplied it, and a
    view or a script that computes `commodity_slug = request.GET.get(...)`
    and passes it through should not have to strip its own Nones. The
    alternative -- widening every optional parameter's schema to accept
    null -- weakens the contract an agent reads, to say the same thing.

    Only the top level. A None INSIDE a `data` payload is meaningful: it is
    how a nullable field gets cleared, and data_access._columns already
    distinguishes that from an omitted key.
    """
    operation = get_operation(name)
    payload = {key: value for key, value in (payload or {}).items() if value is not None}
    jsonschema.validate(payload, operation.input_schema)
    # After validation, so a malformed payload fails on its shape rather than
    # on who sent it; before dispatch, because this is the one choke point both
    # the HTTP adapter and the MCP tools pass through, and provenance derived
    # in two places is provenance that can disagree with itself.
    payload = stamp_provenance(access, operation, payload)
    return operation.handler(access, **payload)


# ---- serialisation helpers ---------------------------------------------


def figure(value):
    return to_wire(value)


def record(obj) -> dict:
    """One model instance as its published wire dict.

    Dispatches on the model class rather than taking a serializer argument,
    so a handler cannot accidentally serialise a quote as a contract and so
    adding a model without a serializer fails loudly here instead of
    returning a half-empty object to a caller.
    """
    if obj is None:
        return None
    serialize = _SERIALIZERS.get(type(obj))
    if serialize is None:
        raise TypeError(
            f"no serializer for {type(obj).__name__}: add one to serializers.py rather than "
            "returning a model instance, which is not JSON"
        )
    return serialize(obj)


def obj(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


ID = {"type": "integer"}

# For a relation where DETACHING is a real operation, not just a thing you
# forgot to set. Sending null clears it; omitting the key leaves it alone
# (data_access._columns makes that distinction). Used sparingly: a nullable
# column is not by itself a reason to let a caller null it.
NULLABLE_ID = {"type": ["integer", "null"]}


# A record's `data` is deliberately open — LabsRecords carry whatever a domain needs, and
# enumerating every field here would fight that. But the spec claims this system enforces
# that amounts are positive and enum fields hold enum values, and an operation schema is
# the only place that claim can bind all three surfaces at once (web form, HTTP API, MCP
# tool) while doubling as the documentation an agent reads before calling. So: constrain
# the fields whose wrong values are silent, leave the rest open.
def _data_with(_required: tuple[str, ...] = (), **properties) -> dict:
    """An object schema that pins the named properties and permits the rest.

    `_required` names the properties data_access indexes with `[]` rather
    than `.get()` -- the ones whose absence is a KeyError deep inside a data
    access method (a 500 that names no field) rather than a 400 naming
    exactly what is missing, right where an agent reading this schema as
    documentation would look for it.
    """
    schema: dict = {"type": "object", "properties": properties, "additionalProperties": True}
    if _required:
        schema["required"] = list(_required)
    return schema


# A JSON Schema `pattern` is a no-op against a non-string instance — it constrains the
# string branch of a `["number", "string"]` union and nothing else. A schema of
# {"type": ["number", "string"], "pattern": ...} therefore lets `12.50` (a JSON float)
# straight through, and money is Decimal, never float: a float that reaches the handler
# has already lost the precision this whole design refuses to lose silently. So money
# and quantity get two different schemas, not one shared "positive number" schema:
#
#   MONEY    — string only. A JSON number cannot carry a monetary amount without a
#               possible silent rounding, so the contract refuses it outright rather
#               than accept-and-tender. The description is what a caller reads when its
#               float gets rejected.
#   QUANTITY — a decimal string OR a JSON number, because "quantity": 3 is a reasonable
#               thing for a caller to write and rejecting it buys nothing — but positive
#               on BOTH branches, via `anyOf` rather than a shared `pattern`, so a zero or
#               negative quantity is refused whichever shape it arrives in.
MONEY = {
    "type": "string",
    "pattern": r"^\d*\.?\d+$",
    "description": (
        'A monetary amount, as a decimal string (e.g. "12.50"), never a JSON number — '
        "money is Decimal, never float, and a float silently loses precision that a "
        "string does not."
    ),
}

_NONZERO_DECIMAL_STRING = r"^(?!0*\.?0*$)\d*\.?\d+$"

# as_quoted_amount and amount_paid are the headline price -- a quote or a purchase at
# $0 is not a real fact this domain has a basis flag for (unlike free freight or a
# waived duty, which ARE representable facts and stay on the zero-accepting MONEY
# above). MONEY's own pattern accepts "0"/"0.00" and yields a *confirmed* Money(0)
# that sorts first in a comparison -- exactly the silent-wrong-answer this schema
# layer exists to refuse everywhere else.
MONEY_NONZERO = {
    "type": "string",
    "pattern": _NONZERO_DECIMAL_STRING,
    "description": (
        'A monetary amount, as a decimal string (e.g. "12.50"), never a JSON number — '
        "money is Decimal, never float, and a float silently loses precision that a "
        "string does not. Must be greater than zero."
    ),
}

QUANTITY = {
    "anyOf": [
        {"type": "string", "pattern": _NONZERO_DECIMAL_STRING},
        {"type": "number", "exclusiveMinimum": 0},
    ]
}

_NON_NEGATIVE_INT = {"type": "integer", "minimum": 0}

_QUOTE_DATA = _data_with(
    tender_id=ID,
    supplier_id=ID,
    item_id=ID,
    commodity_slug={"type": "string", "minLength": 1},
    as_quoted_amount=MONEY_NONZERO,
    as_quoted_unit={"enum": ["per_base_unit", "per_pack", "per_lot_total", "per_metric_tonne"]},
    as_quoted_currency={"type": "string", "minLength": 3, "maxLength": 3},
    quantity_basis=QUANTITY,
    pack_spec_source={"enum": ["stated_on_quote", "trade_item_confirmed", "not_stated"]},
    base_per_pack_stated=_NON_NEGATIVE_INT,
    base_unit_grams_stated=_NON_NEGATIVE_INT,
    freight_basis={"enum": ["included", "excluded", "not_specified"]},
    duties_basis={"enum": ["included", "excluded", "not_specified"]},
    freight_amount=MONEY,
    duties_amount=MONEY,
    fx_rate_to_usd=MONEY,
    shelf_life_months_stated=_NON_NEGATIVE_INT,
    lead_time_days=_NON_NEGATIVE_INT,
    # How the goods reach the buyer: delivered to the tender's places named
    # in delivery_point_keys, or collected from pickup_location. A collected
    # bid's buyer_transport_amount is the buyer's own transport cost.
    delivery_mode={"enum": list(records.DELIVERY_MODES)},
    delivery_point_keys={"type": "array", "items": {"type": "string"}},
    pickup_location={"type": "string"},
    buyer_transport_amount=MONEY,
)

# quote_record creates a new quote from nothing, so tender_id/commodity_slug
# must be given up front -- data_access.create_quote indexes both with `[]`.
# quote_correct is a PARTIAL update (data_access.supersede_quote merges
# {**existing.data, **data}): the existing record already has these, and
# requiring them on a correction would force a caller to re-supply values it
# is not correcting -- and a wrong resupplied value would merge straight
# into the record, corrupting the field the caller never meant to touch.
# So only the create-shaped schema carries the requirement; quote_correct
# keeps using the unrequired _QUOTE_DATA above.
# supplier_id joined tender_id and commodity_slug when quotes became a real
# table: the column is NOT NULL because a quote nobody can attribute cannot
# be compared, ranked or awarded. Requiring it here turns a Postgres
# constraint violation (a 500 naming a column) into a 400 naming the field.
_QUOTE_DATA_CREATE = {**_QUOTE_DATA, "required": ["tender_id", "commodity_slug", "supplier_id"]}

# A correction may CLEAR a figure, not only change it: a supplier revising its
# bid from "freight excluded, 9.00" to "freight included" means there is no
# freight amount any more, and a correction that could only merge would keep
# the 9.00 on the new version. So the optional figures also take null here.
# The price, its basis and currency stay non-null -- a quote without them is
# not a quote.
_CLEARABLE_ON_CORRECTION = (
    "quantity_basis",
    "buyer_transport_amount",
    "freight_amount",
    "duties_amount",
    "fx_rate_to_usd",
    "base_per_pack_stated",
    "base_unit_grams_stated",
    "shelf_life_months_stated",
    "lead_time_days",
)
_QUOTE_DATA_CORRECTION = {
    **_QUOTE_DATA,
    "properties": {
        key: ({"anyOf": [schema, {"type": "null"}]} if key in _CLEARABLE_ON_CORRECTION else schema)
        for key, schema in _QUOTE_DATA["properties"].items()
    },
}

_TENDER_DATA = _data_with(
    label={"type": "string", "minLength": 1},
    status={"enum": ["draft", "open", "closed", "awarded"]},
    lines={
        "type": "array",
        "items": _data_with(
            commodity_slug={"type": "string", "minLength": 1},
            quantity=QUANTITY,
            quantity_unit={"type": "string", "minLength": 1},
        ),
    },
    # Where the buyer will take delivery -- one or more places. A supplier's
    # bid names the places its price covers by `key` (made from the name when
    # not given). `delivery_point` (one place) is still accepted from callers
    # written before a tender could have several.
    delivery_points={
        "type": "array",
        "items": _data_with(
            key={"type": "string"},
            name={"type": "string"},
            city={"type": "string"},
            country={"type": "string"},
            country_name={"type": "string"},
        ),
    },
    delivery_point=_data_with(
        name={"type": "string"},
        city={"type": "string"},
        country={"type": "string"},
        country_name={"type": "string"},
        incoterm_requested={"type": "string"},
    ),
    pickup_accepted={"type": "boolean"},
    incoterm_requested={"type": "string"},
    # An organisation's own listing: who publishes it, its address
    # (/supply/market/t/<slug>/), its brief and its colour.
    owner_org_id=NULLABLE_ID,
    slug={"type": ["string", "null"], "maxLength": 80},
    brief={"type": "string"},
    hue={"enum": ["", *[code for code, _label in records.LISTING_HUES]]},
    reminder_interval_days=_NON_NEGATIVE_INT,
    visibility={"enum": list(records.TENDER_VISIBILITIES)},
)

_ITEM_DATA = _data_with(
    ("sku",),
    sku={"type": "string", "minLength": 1},
    commodity_slug={"type": "string", "minLength": 1},
    base_per_pack=_NON_NEGATIVE_INT,
    pack_per_case=_NON_NEGATIVE_INT,
    base_unit_grams=_NON_NEGATIVE_INT,
    shelf_life_months=_NON_NEGATIVE_INT,
    status={"enum": ["active", "discontinued"]},
    # A kit's contents. Each component names a product in this catalogue, how
    # many of its base units one kit holds, and optionally the component's own
    # stated specification -- which is what lets the zinc inside a co-pack be
    # checked against the zinc requirement rather than the co-pack's.
    components={
        "type": "array",
        "items": _data_with(
            ("commodity_slug", "quantity", "base_unit"),
            commodity_slug={"type": "string", "minLength": 1},
            quantity=QUANTITY,
            base_unit={"type": "string", "minLength": 1},
            spec_attributes={"type": "object"},
        ),
    },
    one_course_is={"enum": list(records.ONE_COURSE_IS)},
    stock_class={"enum": list(records.STOCK_CLASSES)},
    # Whether `components` are what one base unit holds (a co-pack) or one
    # pack (a test kit). Defaults to base.
    components_per={"enum": list(records.COMPONENTS_PER)},
)

# A contract is the commitment. buyer_of_record is required and has no
# default: import duty and VAT depend on who imports, so a landed cost
# derived without knowing the buyer carries an invisible assumption -- the
# exact failure this domain exists to refuse. See the design doc, 17.1.
_CONTRACT_DATA = _data_with(
    (),
    tender_id=ID,
    award_id=ID,
    supplier_id=ID,
    item_id=ID,
    commodity_slug={"type": "string", "minLength": 1},
    buyer_of_record={"enum": list(records.BUYER_OF_RECORD)},
    buyer_org_id=ID,
    reference={"type": "string"},
    status={"enum": list(records.CONTRACT_STATUSES)},
    currency={"type": "string", "minLength": 3, "maxLength": 3},
    quantity=QUANTITY,
    quantity_unit={"type": "string", "minLength": 1},
    unit_price=MONEY_NONZERO,
    unit_price_unit={"enum": ["per_base_unit", "per_pack", "per_lot_total", "per_metric_tonne"]},
    freight_basis={"enum": list(records.BASIS)},
    freight_amount=MONEY,
    duties_basis={"enum": list(records.BASIS)},
    duties_amount=MONEY,
    vat_basis={"enum": list(records.BASIS)},
    vat_amount=MONEY,
    duty_relief_claimed={"type": "boolean"},
    duty_relief_document_id=NULLABLE_ID,
    incoterm={"type": "string"},
    delivery_supply_point_id=ID,
    promised_lead_time_days=_NON_NEGATIVE_INT,
    consideration={"enum": list(records.CONSIDERATIONS)},
    payment_terms={"enum": list(records.PAYMENT_TERMS)},
    # Nullable because un-naming it is a real edit: the covering order was
    # recorded against the wrong short one.
    covers_shortfall_of_id=NULLABLE_ID,
    source={"enum": list(records.SOURCES)},
    recorded_by_org_id=ID,
)

# contract_create builds a row from nothing, so it must name the buyer of
# record and the supplier up front. contract_update is a partial merge --
# typically recording the purchase-order reference once the partner supplies
# it -- and requiring the whole set there would force a caller to re-send
# values it is not changing, where a wrong resend corrupts the field it never
# meant to touch.
_CONTRACT_DATA_CREATE = {
    **_CONTRACT_DATA,
    "required": ["commodity_slug", "supplier_id", "buyer_of_record", "buyer_org_id", "source"],
}

_COMMODITY_DATA = _data_with(
    ("slug",),
    slug={"type": "string", "minLength": 1},
    name={"type": "string", "minLength": 1},
    # `supplementary_food` and `oral_rehydration` are separate from
    # `therapeutic_food` because a CMAM programme buys all three and they are
    # not interchangeable: RUSF treats moderate malnutrition and RUTF severe,
    # and ReSoMal is dosed against dehydration, not against a treatment
    # course. Filing them under therapeutic_food would make "how much
    # therapeutic food did we buy" answer with a number nobody asked for.
    # Both still have a course, so neither joins the no-ration-table set in
    # checks.py.
    category={
        "enum": [
            "therapeutic_food",
            "supplementary_food",
            "oral_rehydration",
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
        base_units_per_day=QUANTITY,
        days_per_course=_NON_NEGATIVE_INT,
        base_units_per_course=_NON_NEGATIVE_INT,
        source={"type": "string"},
    ),
    # Catalogue-level identity. The specification reference says which
    # standard the product's figures come from; the UNICEF Supply Division
    # material number names the same product across every prequalified
    # manufacturer, which is what lets two suppliers' different SKUs be known
    # to be the same thing.
    spec_reference={"type": "string"},
    unicef_material_number={"type": "string"},
    spec_requirements={
        "type": "array",
        "items": _data_with(
            field={"type": "string"},
            operator={"enum": ["<=", ">=", "<", ">", "=="]},
            value={"type": ["number", "string"]},
            unit={"type": "string"},
            rationale={"type": "string"},
        ),
    },
)

_OUTREACH_DATA = _data_with(
    tender_id=ID,
    supplier_id=ID,
    channel={"enum": ["manual", "api", "mcp", "ses"]},
    responded={"type": "boolean"},
    response_kind={"enum": ["quote", "declined", "needs_info", "no_reply"]},
)

# Same split as _QUOTE_DATA_CREATE above, for the same reason:
# outreach_log creates a row from nothing (data_access.create_outreach
# indexes data["tender_id"]); outreach_update is a partial merge
# ({**existing.data, **data}) whose own summary is "typically to record
# that a supplier responded, and how" -- a payload that names only
# responded/response_kind must keep working.
_OUTREACH_DATA_CREATE = {**_OUTREACH_DATA, "required": ["tender_id"]}

_SUPPLIER_DATA = _data_with(
    name={"type": "string", "minLength": 1},
    org_id=ID,
    connect_organization_id={"type": ["integer", "null"], "minimum": 1},
    country={"type": "string", "maxLength": 2},
    city={"type": "string"},
    website={"type": "string"},
    description={"type": "string"},
    contacts={"type": "array", "items": {"type": "object"}},
    qualifications={"type": "array", "items": {"type": "object"}},
    type={"enum": list(records.SUPPLIER_TYPES)},
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
    input_schema=obj({}),
)
def commodity_list(access):
    return [record(c) for c in access.list_commodities()]


@register_operation(
    name="commodity_upsert",
    summary=(
        "Create or update a commodity by slug. Include course_definition to "
        "unlock per-course and per-child cost figures."
    ),
    input_schema=obj({"data": _COMMODITY_DATA}, required=("data",)),
    is_write=True,
)
def commodity_upsert(access, data):
    return record(access.upsert_commodity(data))


@register_operation(
    name="item_list",
    summary=(
        "The master item list — every trade item we can buy or track, with its commodity, pack "
        "configuration, GS1 keys and specification. An item is a specific branded product; a "
        "commodity is the type."
    ),
    input_schema=obj({}),
)
def item_list(access):
    return [record(i) for i in access.list_items()]


@register_operation(
    name="item_get",
    summary="Fetch one trade item by record id, with its pack configuration and specification attributes.",
    input_schema=obj({"item_id": ID}, required=("item_id",)),
)
def item_get(access, item_id):
    item = access.get_item(item_id)
    return record(item) if item else None


@register_operation(
    name="item_upsert",
    summary=(
        "Create or update a trade item by sku. Give base_per_pack and base_unit_grams "
        "as the manufacturer states them, not as the commodity assumes. Any GTIN is "
        "validated against its GS1 check digit and a bad one is refused."
    ),
    input_schema=obj({"data": _ITEM_DATA}, required=("data",)),
    is_write=True,
)
def item_upsert(access, data):
    return record(access.upsert_item(data))


@register_operation(
    name="catalogue_seed",
    summary=(
        "Add a starting catalogue for a malnutrition programme: supplementary food, the two "
        "therapeutic milks, rehydration salts, MUAC tapes, amoxicillin, anthropometry equipment, and "
        "a few trade items. Existing rows are left alone, never overwritten. Safe to re-run; dry_run "
        "shows what it would add."
    ),
    input_schema=obj({"dry_run": {"type": "boolean"}}),
    is_write=True,
    internal=True,
)
def catalogue_seed(access, dry_run=False):
    existing_products = {c.slug for c in access.list_commodities()}
    existing_items = {i.sku for i in access.list_items()}

    added_products, kept_products = [], []
    for product in reference_catalogue.PRODUCTS:
        if product["slug"] in existing_products:
            kept_products.append(product["slug"])
            continue
        if not dry_run:
            access.upsert_commodity(dict(product))
        added_products.append(product["slug"])

    # Trade items after the products, and only where the product is really
    # there. `Item.commodity` is not nullable, so one whose product is missing
    # raises deep in the ORM rather than saying which product it wanted.
    available = existing_products | set(added_products)
    added_items, kept_items, refused = [], [], []
    for item in reference_catalogue.TRADE_ITEMS:
        if item["sku"] in existing_items:
            kept_items.append(item["sku"])
            continue
        if item["commodity_slug"] not in available:
            refused.append(f"{item['sku']} needs the product '{item['commodity_slug']}', which this catalogue lacks")
            continue
        if not dry_run:
            access.upsert_item(dict(item))
        added_items.append(item["sku"])

    return {
        "dry_run": dry_run,
        "products_added": added_products,
        "products_already_present": kept_products,
        "trade_items_added": added_items,
        "trade_items_already_present": kept_items,
        "refused": refused,
    }


@register_operation(
    name="supplier_list",
    summary=(
        "List suppliers, optionally filtered by a case-insensitive substring "
        "of name or contact email. Read this before creating a supplier to "
        "avoid making a duplicate."
    ),
    input_schema=obj({"search": {"type": "string"}}),
)
def supplier_list(access, search=None):
    return [record(s) for s in access.list_suppliers(search=search)]


@register_operation(
    name="supplier_get",
    summary="Fetch one supplier by record id, with contacts, qualifications and status.",
    input_schema=obj({"supplier_id": ID}, required=("supplier_id",)),
)
def supplier_get(access, supplier_id):
    supplier = access.get_supplier(supplier_id)
    return record(supplier) if supplier else None


# `supplier_create` / `supplier_update`, not `supplier_upsert`. A supplier is a
# company linked into this program; the company lives on the organisation
# and is shared by every program that buys from it. `supplier_create` finds
# the company (by `org_id`, then Connect id, then the organisation its name
# already belongs to) and mints one only when nothing matches -- so creating
# "EHA Clinics" in a second program links the same EHA rather than making a
# second, and creating it twice in one program returns the first. Names
# still never merge two companies: "Nutriset" and "Nutriset Nigeria" have
# different slugs and stay two. test_supplier_company.py pins this.
@register_operation(
    name="supplier_create",
    summary=(
        "Add a supplier to this program. The company is shared across programs: pass org_id "
        "for an organisation you know, or a name (and connect_organization_id if it has one) and the "
        "existing company is found or a new one made. Adding a company already supplying this "
        "program returns that supplier rather than a duplicate."
    ),
    input_schema=obj({"data": _SUPPLIER_DATA}, required=("data",)),
    is_write=True,
)
def supplier_create(access, data):
    return record(access.create_supplier(data))


@register_operation(
    name="supplier_update",
    summary=(
        "Update a supplier. Company facts (name, country, type, city, contacts, qualifications, "
        "website, description, connect_organization_id) change the company in every program; "
        "status and notes are this program's own. A company Connect names cannot be renamed here."
    ),
    input_schema=obj({"supplier_id": ID, "data": _SUPPLIER_DATA}, required=("supplier_id", "data")),
    is_write=True,
)
def supplier_update(access, supplier_id, data):
    return record(access.update_supplier(supplier_id, data))


@register_operation(
    name="supplier_mark_reviewed",
    summary=(
        "Record that the program team has reviewed a supplier that registered itself on the supplier "
        "marketplace. Clears the 'self-registered, not yet reviewed' flag on its quotes; changes nothing else."
    ),
    input_schema=obj({"supplier_id": ID}, required=("supplier_id",)),
    is_write=True,
)
def supplier_mark_reviewed(access, supplier_id):
    return record(access.mark_supplier_reviewed(supplier_id))


@register_operation(
    name="supplier_market_invite",
    summary=(
        "Hand an unclaimed supplier company to its own people: a one-time, 30-day invitation that makes "
        "whoever opens it (signed in to labs) the company's first admin on the supplier marketplace. "
        "Refused for a company Connect knows (Connect's membership governs it) and for one that already "
        "has people on the marketplace (its own admins invite from then on). The raw link is returned "
        "once and never again. The email is only a note of who it was for."
    ),
    input_schema=obj({"supplier_id": ID, "email": {"type": "string"}}, required=("supplier_id",)),
    is_write=True,
)
def supplier_market_invite(access, supplier_id, email=""):
    from django.urls import reverse

    from connect_labs.marketplace import membership
    from connect_labs.marketplace.models import OrgMembership

    supplier = access.get_supplier(supplier_id)
    if supplier is None:
        raise ValueError(f"supplier {supplier_id} not found")
    org = supplier.org
    # A program team can link any company into its program, so a program's
    # invitation must not be a way to take one over. It may hand over only a
    # company nobody holds yet: not one Connect governs, and not one whose own
    # people are already on the marketplace.
    if org.connect_organization_id is not None:
        raise ValueError(
            f"{org.name} is a Connect organisation; its own members sign in and act for it already, "
            "so there is nothing to invite them to"
        )
    if OrgMembership.objects.filter(org=org).exists():
        raise ValueError(
            f"{org.name} already has people on the supplier marketplace; ask them to invite whoever else "
            "should act for it"
        )
    user = access.user if getattr(access.user, "is_authenticated", False) else None
    invite, raw = membership.issue_invite(org, email=email or "", issued_by=user)
    return {
        "org_id": supplier.org_id,
        "org_name": supplier.org.name,
        "email": invite.email,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
        "path": reverse("supply_chain:market_invite", args=[raw]),
    }


@register_operation(
    name="checks_list",
    summary=(
        "Run the domain's checks. Every finding is `missing` (a fact nobody supplied), `conflict` "
        "(two records disagree) or `threshold` (a derived figure past a bound in your own data), and "
        "carries its subject, the facts behind it, its age, and the `audience` that can answer it: "
        "supplier, partner or internal. Deliberately unranked and unworded — no priority, no "
        "severity, no drafted message."
    ),
    input_schema=obj(
        {
            "opportunity_id": ID,
            "kinds": {"type": "array", "items": {"type": "string"}},
            "categories": {"type": "array", "items": {"enum": ["missing", "conflict", "threshold"]}},
        }
    ),
)
def checks_list(access, opportunity_id=None, kinds=None, categories=None):
    from connect_labs.supply_chain.checks import CATEGORIES, KIND_CATEGORIES, run_checks

    found = run_checks(access, opportunity_id=opportunity_id, kinds=kinds, categories=categories)
    return {
        "kinds": KIND_CATEGORIES,
        "categories": list(CATEGORIES),
        "count": len(found),
        "by_kind": {kind: sum(1 for f in found if f["kind"] == kind) for kind in KIND_CATEGORIES},
        "by_category": {category: sum(1 for f in found if f["category"] == category) for category in CATEGORIES},
        "checks": found,
    }


@register_operation(
    name="chain_summary",
    summary=(
        "Stage counts across the chain: source, order and deliver. These are STATES — counts of rows "
        "— which is a different thing from checks_list, which reports gaps. Quantities appear only "
        "when you name a commodity_slug: cartons and vials have no meaningful total."
    ),
    input_schema=obj({"commodity_slug": {"type": "string"}, "opportunity_id": ID}),
)
def chain_summary(access, commodity_slug=None, opportunity_id=None):
    from connect_labs.supply_chain.summary import chain_summary as build

    summary = build(access, commodity_slug=commodity_slug, opportunity_id=opportunity_id)
    deliver = summary["deliver"]
    for key in ("on_hand", "in_transit", "consumed"):
        if deliver[key] is not None:
            deliver[key] = figure(deliver[key])
    return summary
