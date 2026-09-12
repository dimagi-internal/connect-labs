"""Network and stock operations.

Registered into the single registry in operations.py, so the HTTP API and the
MCP server both get them with no second list.

The reads here return *derived* figures, and every one of them can come back
`Unconfirmed` with reasons instead of a number. That is the contract an agent
reading this surface works against: it will never be handed a plausible zero
where the honest answer was "the supplier never stated the pack size".

Note what is absent: nothing ranks, prioritises or recommends. `resupply_plan`
classifies a point against its own min/max band and stops; deciding what to
do about a network of them, and in what order, belongs to a client.
"""

from connect_labs.supply_chain import records
from connect_labs.supply_chain.operations import (
    ID,
    MONEY,
    QUANTITY,
    _data_with,
    figure,
    obj,
    record,
    register_operation,
)
from connect_labs.supply_chain.stock.services import ledger, network, resupply, soh

_DATE = {"type": "string", "format": "date"}

_SUPPLY_POINT_DATA = _data_with(
    ("slug", "name", "kind", "source"),
    slug={"type": "string", "minLength": 1},
    name={"type": "string", "minLength": 1},
    kind={"enum": list(records.SUPPLY_POINT_KINDS)},
    opportunity_id=ID,
    parent_supply_point_id=ID,
    managed_by_party_id=ID,
    connect_username={"type": "string"},
    connect_user_id=ID,
    admin_area={"type": "string"},
    latitude={"type": "number"},
    longitude={"type": "number"},
    min_months_of_stock=MONEY,
    max_months_of_stock=MONEY,
    status={"enum": ["active", "inactive"]},
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_MOVEMENT_DATA = _data_with(
    ("kind", "occurred_on", "commodity_slug", "quantity", "quantity_unit", "source"),
    kind={"enum": list(records.MOVEMENT_KINDS)},
    occurred_on=_DATE,
    from_supply_point_id=ID,
    to_supply_point_id=ID,
    item_id=ID,
    commodity_slug={"type": "string", "minLength": 1},
    batch={"type": "string"},
    expiry=_DATE,
    # Not QUANTITY: an adjustment is the one kind allowed to be negative,
    # because that is how a stock count's variance gets into the ledger.
    quantity={"type": ["string", "number"]},
    quantity_unit={"type": "string", "minLength": 1},
    opportunity_id=ID,
    reference={"type": "string"},
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_STOCK_COUNT_DATA = _data_with(
    ("supply_point_id", "commodity_slug", "kind", "counted_on", "quantity", "quantity_unit", "source"),
    supply_point_id=ID,
    item_id=ID,
    commodity_slug={"type": "string", "minLength": 1},
    batch={"type": "string"},
    kind={"enum": list(records.STOCK_COUNT_KINDS)},
    counted_on=_DATE,
    # A count of zero is a real and important observation -- it is a
    # stockout -- so this is not the nonzero variant.
    quantity={"anyOf": [{"type": "string"}, {"type": "number", "minimum": 0}]},
    quantity_unit={"type": "string", "minLength": 1},
    reason={"type": "string"},
    opportunity_id=ID,
    form_submission_id={"type": "string"},
    visit_id={"type": "string"},
    connect_username={"type": "string"},
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)

_DISTRIBUTION_DATA = _data_with(
    ("supply_point_id", "opportunity_id", "commodity_slug", "distributed_on", "lines", "source"),
    supply_point_id=ID,
    opportunity_id=ID,
    commodity_slug={"type": "string", "minLength": 1},
    distributed_on=_DATE,
    reference={"type": "string"},
    lines={
        "type": "array",
        "minItems": 1,
        "items": _data_with(
            ("quantity", "quantity_unit"),
            to_supply_point_id=ID,
            connect_username={"type": "string"},
            item_id=ID,
            batch={"type": "string"},
            quantity=QUANTITY,
            quantity_unit={"type": "string", "minLength": 1},
        ),
    },
    source={"enum": list(records.SOURCES)},
    recorded_by_party_id=ID,
)


# ---- supply points -----------------------------------------------------


@register_operation(
    name="supply_point_list",
    summary=(
        "List the places stock can rest in this programme — central and regional "
        "stores, facilities, and each field worker's own holding (kind=user_held). "
        "Filter by opportunity to get one opportunity's network. A worker is a supply "
        "point, which is why stock held by a worker needs no separate concept."
    ),
    input_schema=obj(
        {
            "opportunity_id": ID,
            "kind": {"enum": list(records.SUPPLY_POINT_KINDS)},
            "include_inactive": {"type": "boolean"},
        }
    ),
)
def supply_point_list(access, opportunity_id=None, kind=None, include_inactive=False):
    return [
        record(p)
        for p in access.list_supply_points(opportunity_id=opportunity_id, kind=kind, include_inactive=include_inactive)
    ]


@register_operation(
    name="supply_point_get",
    summary="Fetch one supply point, with its parent, its manager and its min/max months-of-stock band.",
    input_schema=obj({"supply_point_id": ID}, required=("supply_point_id",)),
)
def supply_point_get(access, supply_point_id):
    return record(access.get_supply_point(supply_point_id))


@register_operation(
    name="supply_point_upsert",
    summary=(
        "Create or update a supply point by slug. A kind=user_held point must name the "
        "Connect user whose stock it is (connect_username), because nothing could ever "
        "post stock to it otherwise. Set min_months_of_stock and max_months_of_stock to "
        "make the resupply band data rather than a rule in code."
    ),
    input_schema=obj({"data": _SUPPLY_POINT_DATA}, required=("data",)),
    is_write=True,
)
def supply_point_upsert(access, data):
    return record(access.upsert_supply_point(data))


# ---- the ledger --------------------------------------------------------


@register_operation(
    name="movement_list",
    summary=(
        "List stock movements, newest first. The ledger is append-only: a correction is "
        "an adjustment movement naming its cause, never an edit, which is what makes a "
        "balance on any past date reproducible."
    ),
    input_schema=obj(
        {
            "supply_point_id": ID,
            "item_id": ID,
            "kind": {"enum": list(records.MOVEMENT_KINDS)},
            "since": _DATE,
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
        }
    ),
)
def movement_list(access, supply_point_id=None, item_id=None, kind=None, since=None, limit=500):
    return [
        record(m)
        for m in access.list_movements(
            supply_point_id=supply_point_id, item_id=item_id, kind=kind, since=since, limit=limit
        )
    ]


@register_operation(
    name="movement_record",
    summary=(
        "Post one movement to the ledger — a transfer between stores, a loss, an expiry "
        "write-off. Use receipt_record or distribution_record for those events instead, so "
        "the ledger keeps its link back to the paperwork. A movement must touch at least "
        "one supply point. Only kind=adjustment may carry a negative quantity."
    ),
    input_schema=obj({"data": _MOVEMENT_DATA}, required=("data",)),
    is_write=True,
)
def movement_record(access, data):
    return record(access.record_movement(data))


# ---- counts ------------------------------------------------------------


@register_operation(
    name="stock_count_list",
    summary="List reported stock counts — worker self-reports, physical stock takes, and overrides.",
    input_schema=obj(
        {
            "supply_point_id": ID,
            "item_id": ID,
            "kind": {"enum": list(records.STOCK_COUNT_KINDS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
        }
    ),
)
def stock_count_list(access, supply_point_id=None, item_id=None, kind=None, limit=500):
    return [
        record(c)
        for c in access.list_stock_counts(supply_point_id=supply_point_id, item_id=item_id, kind=kind, limit=limit)
    ]


@register_operation(
    name="stock_count_record",
    summary=(
        "Record what somebody says is actually on hand. kind=self_reported for a worker's "
        "periodic form, physical_count for a stock take, override to assert a figure over "
        "the ledger. An observation is simply kept, so the variance against the ledger "
        "stays visible; an override additionally posts the difference as an adjustment and "
        "requires a reason. A quantity of zero is a real observation — it is a stockout."
    ),
    input_schema=obj({"data": _STOCK_COUNT_DATA}, required=("data",)),
    is_write=True,
)
def stock_count_record(access, data):
    return record(access.record_stock_count(data))


# ---- distributions ----------------------------------------------------


@register_operation(
    name="distribution_list",
    summary="List resupply runs from a store out to field workers, with their lines.",
    input_schema=obj(
        {
            "opportunity_id": ID,
            "supply_point_id": ID,
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        }
    ),
)
def distribution_list(access, opportunity_id=None, supply_point_id=None, limit=200):
    return [
        record(d)
        for d in access.list_distributions(opportunity_id=opportunity_id, supply_point_id=supply_point_id, limit=limit)
    ]


@register_operation(
    name="distribution_get",
    summary="Fetch one distribution run, with every line and the movements it posted.",
    input_schema=obj({"distribution_id": ID}, required=("distribution_id",)),
)
def distribution_get(access, distribution_id):
    return record(access.get_distribution(distribution_id))


@register_operation(
    name="distribution_record",
    summary=(
        "Record a resupply run out to field workers. One header, one line per worker; each "
        "line posts a movement, so a worker's stock on hand is a ledger balance rather than "
        "a separate figure. Name each worker by to_supply_point_id or by connect_username — "
        "whichever you have. This is the operation a local partner uses to tell us what they "
        "distributed."
    ),
    input_schema=obj({"data": _DISTRIBUTION_DATA}, required=("data",)),
    is_write=True,
)
def distribution_record(access, data):
    return record(access.record_distribution(data))


# ---- derived reads ----------------------------------------------------


@register_operation(
    name="stock_on_hand",
    summary=(
        "What is at one supply point: the ledger balance, the last reported count, and the "
        "variance between them. Both are returned because they routinely disagree and the "
        "disagreement is the finding. `basis` says which to plan on. A variance whose units "
        "cannot be reconciled comes back unconfirmed rather than as a number — the store "
        "counts packs, the field counts base units, and the bridge is the pack specification."
    ),
    input_schema=obj(
        {"supply_point_id": ID, "item_id": ID, "unit": {"type": "string"}}, required=("supply_point_id",)
    ),
)
def stock_on_hand(access, supply_point_id, item_id=None, unit=None):
    point = access._require_supply_point(supply_point_id)
    item = access._resolve_item(item_id)
    result = soh.stock_on_hand(access.program_id, point, item=item, unit=unit)
    return {
        "supply_point_id": point.pk,
        "ledger": figure(result["ledger"]),
        "reported": figure(result["reported"]) if result["reported"] else None,
        "variance": figure(result["variance"]) if result["variance"] else None,
        "basis": result["basis"],
        "as_of": result["as_of"].isoformat() if result["as_of"] else None,
        "reported_kind": result["reported_kind"],
        "reported_source": result["reported_source"],
    }


@register_operation(
    name="stock_position",
    summary=(
        "The four figures that are not the same figure: on hand, in transit, committed and "
        "available. In-transit stock is real but it is NOT cover — counted as on hand, a "
        "network reads months of stock it does not have and nobody reorders while stores "
        "run dry. Committed is allocated but not yet moved, so the same carton is not "
        "promised twice."
    ),
    input_schema=obj({"supply_point_id": ID, "item_id": ID}, required=("supply_point_id",)),
)
def stock_position(access, supply_point_id, item_id=None):
    point = access._require_supply_point(supply_point_id)
    item = access._resolve_item(item_id)
    position = ledger.position(access.program_id, point, item=item)
    return {"supply_point_id": point.pk, **{key: figure(value) for key, value in position.items()}}


@register_operation(
    name="stock_by_batch",
    summary=(
        "What is held at a supply point, per batch, soonest expiry first — the "
        "first-expired-first-out issue order, and the index a recall needs to name which "
        "location received which lot. A batch with no stated expiry sorts last rather than "
        "sorting as though it never expires."
    ),
    input_schema=obj({"supply_point_id": ID, "item_id": ID}, required=("supply_point_id",)),
)
def stock_by_batch(access, supply_point_id, item_id=None):
    point = access._require_supply_point(supply_point_id)
    item = access._resolve_item(item_id)
    rows = ledger.balance_by_batch(access.program_id, point, item=item)
    return [
        {
            "batch": row["batch"],
            "expiry": row["expiry"].isoformat() if row["expiry"] else None,
            "quantity": str(row["quantity"]),
            "unit": row["unit"],
        }
        for row in rows
    ]


@register_operation(
    name="resupply_plan",
    summary=(
        "Consumption rate and cover for one supply point: average monthly consumption over a "
        "stated window, months of stock, days to stockout, reorder point, and how much to "
        "send to reach the top of its band. An AMC over a window shorter than 30 days is "
        "refused as unconfirmed — a fortnight extrapolated to a month is how a supply chain "
        "talks itself into a stockout. `status` classifies against the point's own min/max "
        "band and stops short of recommending anything."
    ),
    input_schema=obj(
        {
            "supply_point_id": ID,
            "item_id": ID,
            "window_days": {"type": "integer", "minimum": 1, "maximum": 730},
        },
        required=("supply_point_id",),
    ),
)
def resupply_plan(access, supply_point_id, item_id=None, window_days=resupply.DEFAULT_WINDOW_DAYS):
    point = access._require_supply_point(supply_point_id)
    item = access._resolve_item(item_id)
    plan = resupply.plan(access.program_id, point, item=item, window_days=window_days)
    return {
        "supply_point_id": point.pk,
        "amc_window_days": plan["amc_window_days"],
        "status": plan["status"],
        "min_months_of_stock": str(plan["min_months_of_stock"]) if plan["min_months_of_stock"] is not None else None,
        "max_months_of_stock": str(plan["max_months_of_stock"]) if plan["max_months_of_stock"] is not None else None,
        **{
            key: _plain(plan[key])
            for key in ("on_hand", "amc", "months_of_stock", "days_to_stockout", "reorder_point", "resupply_quantity")
        },
    }


@register_operation(
    name="network_stock",
    summary=(
        "Stock on hand and cover across a whole network in one call — every field worker on "
        "an opportunity, or every store in a programme. This is the network manager's view: "
        "who is about to run out, who has never reported, and how much to send. A point whose "
        "figure cannot be computed appears carrying its reason, never as a zero and never "
        "omitted, because a view that quietly drops what it could not compute is how a "
        "stockout goes unnoticed."
    ),
    input_schema=obj(
        {
            "opportunity_id": ID,
            "item_id": ID,
            "kind": {"enum": list(records.SUPPLY_POINT_KINDS)},
            "window_days": {"type": "integer", "minimum": 1, "maximum": 730},
        }
    ),
)
def network_stock(access, opportunity_id=None, item_id=None, kind=None, window_days=resupply.DEFAULT_WINDOW_DAYS):
    item = access._resolve_item(item_id)
    rows = network.network_stock(
        access.program_id,
        opportunity_id=opportunity_id,
        item=item,
        kind=kind,
        window_days=window_days,
    )
    return {
        "summary": network.summarise(rows),
        "points": [
            {
                **{
                    key: row[key]
                    for key in (
                        "supply_point_id",
                        "name",
                        "kind",
                        "connect_username",
                        "admin_area",
                        "opportunity_id",
                        "status",
                    )
                },
                "reported_on": row["reported_on"].isoformat() if row["reported_on"] else None,
                "reported_kind": row["reported_kind"],
                "min_months_of_stock": str(row["min_months_of_stock"])
                if row["min_months_of_stock"] is not None
                else None,
                "max_months_of_stock": str(row["max_months_of_stock"])
                if row["max_months_of_stock"] is not None
                else None,
                **{
                    key: _plain(row[key])
                    for key in (
                        "on_hand",
                        "reported",
                        "amc",
                        "months_of_stock",
                        "days_to_stockout",
                        "resupply_quantity",
                    )
                },
            }
            for row in rows
        ],
    }


def _plain(value):
    """A derived figure on the wire, whatever kind it is.

    Quantities and money go through `figure`; a bare Decimal (months of
    stock, days to stockout) becomes a string for the same reason money
    does, and None stays None.
    """
    from decimal import Decimal

    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    return figure(value)
