"""Stock from visits: dispensing rules, and the visit reader that applies them.

Registered into the single registry in operations.py, like stock/operations.py,
so the HTTP API and the MCP server both get them with no second list.
Design: docs/superpowers/specs/2026-09-28-supply-stock-from-visits-design.md.
"""

from connect_labs.supply_chain.operations import ID, QUANTITY, obj, record, register_operation

_DATE = {"type": "string", "format": "date"}
_PATHS = {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}
_UNIT = {"type": "string", "minLength": 1}
_FORMS = {"type": "array", "items": {"type": "string", "minLength": 1}}

# Every line kind may narrow itself to some forms (xmlns or name) and, when it
# reads a "given" answer, insist that other answers are present too.
_COMMON = {"forms": _FORMS, "requires_paths": _PATHS}

_STATED_LINE = obj(
    {"kind": {"const": "stated"}, "paths": _PATHS, "unit": _UNIT, **_COMMON},
    required=("kind", "paths", "unit"),
)
_PROTOCOL_LINE = obj(
    {
        "kind": {"const": "protocol"},
        "given_paths": _PATHS,
        "given_values": {"type": ["array", "null"], "items": {"type": "string", "minLength": 1}},
        "quantity": QUANTITY,
        "unit": _UNIT,
        **_COMMON,
    },
    required=("kind", "given_paths", "quantity", "unit"),
)
_VALUE_MAP_LINE = {
    **obj(
        {
            "kind": {"const": "value_map"},
            "paths": _PATHS,
            "map": {"type": "object", "minProperties": 1, "additionalProperties": QUANTITY},
            "unit": _UNIT,
            **_COMMON,
        },
        required=("kind", "paths", "map", "unit"),
    )
}
_REPORTS = obj(
    {
        "balance_paths": _PATHS,
        "receipt": obj({"quantity_paths": _PATHS, "date_paths": _PATHS}, required=("quantity_paths", "date_paths")),
    }
)
_RULE_DATA = obj(
    {
        "opportunity_id": ID,
        "item_id": ID,
        "resupply_point_id": ID,
        "active_from": _DATE,
        "lines": {
            "type": "array",
            "minItems": 1,
            "items": {"oneOf": [_STATED_LINE, _PROTOCOL_LINE, _VALUE_MAP_LINE]},
        },
        "forms": _FORMS,
        "reports": _REPORTS,
        "status": {"enum": ["active", "inactive"]},
    },
    required=("opportunity_id", "item_id", "resupply_point_id", "active_from", "lines"),
)


@register_operation(
    name="dispensing_rule_list",
    summary=(
        "The dispensing rules: per opportunity and item, which form answers say what a visit gave out. "
        "Inactive rules are left out unless include_inactive."
    ),
    input_schema=obj({"opportunity_id": ID, "include_inactive": {"type": "boolean"}}),
)
def dispensing_rule_list(access, opportunity_id=None, include_inactive=False):
    return [
        record(rule)
        for rule in access.list_dispensing_rules(opportunity_id=opportunity_id, include_inactive=include_inactive)
    ]


@register_operation(
    name="dispensing_rule_get",
    summary="One dispensing rule, with its lines, the forms it reads and the store its workers hang from.",
    input_schema=obj({"rule_id": ID}, required=("rule_id",)),
)
def dispensing_rule_get(access, rule_id):
    rule = access.get_dispensing_rule(rule_id)
    if rule is None:
        raise ValueError(f"dispensing rule {rule_id} does not exist in this programme")
    return record(rule)


@register_operation(
    name="dispensing_rule_upsert",
    summary=(
        "Create or edit what a visit on one opportunity gives out of one item (one rule per opportunity and "
        "item). Each line is one of: stated -- read a number from form paths (form.x, the app's /data/x); "
        "protocol -- the form says only that it was given (given_paths, optional given_values) and the "
        "quantity is fixed; value_map -- read the dose the app chose as text and map each answer to a "
        "quantity. requires_paths lists further answers that must be present; forms limits a line to named "
        "forms (xmlns or name). Protocol and value_map lines make every figure they feed ESTIMATED. Visits "
        "before active_from are never read. reports names where the worker's own app keeps its balance and "
        "receipts."
    ),
    input_schema=obj({"data": _RULE_DATA}, required=("data",)),
    is_write=True,
)
def dispensing_rule_upsert(access, data):
    return record(access.upsert_dispensing_rule(data))


@register_operation(
    name="visit_consumption_ingest",
    summary=(
        "Read an opportunity's visits and post what each gave out as consumption from the worker's own "
        "stock, per its dispensing rules. Idempotent per visit and item; a visit later rejected or marked "
        "duplicate gets a reversal. until replays history (visits after it wait; a later rejection reads "
        "as pending). Synthetic programmes only until the product owner says otherwise."
    ),
    input_schema=obj(
        {"opportunity_id": ID, "until": _DATE, "refresh": {"type": "boolean"}}, required=("opportunity_id",)
    ),
    is_write=True,
    internal=True,
)
def visit_consumption_ingest(access, opportunity_id, until=None, refresh=False):
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.stock.services import visit_reader, visit_source
    from connect_labs.supply_chain.stock.services.dispensing import read_date

    # Before any read: a real programme's visits are not even fetched.
    scopes.require_synthetic(access.program_id, "read visits into the stock ledger")
    visits = visit_source.fetch_visits(opportunity_id, access.access_token, force_refresh=refresh)
    return visit_reader.ingest_visit_consumption(
        access,
        opportunity_id=opportunity_id,
        visits=visits,
        until=read_date(until) if until else None,
    )
