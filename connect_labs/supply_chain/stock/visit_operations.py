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


def parse_until(until):
    """`until` as a day, or None when not given; ValueError for anything that is not YYYY-MM-DD.

    Strict, because a replay's posts are permanent: an `until` read as "no
    limit" would post every visit to today and the history could never be
    rebuilt as intended.
    """
    if until is None:
        return None
    from datetime import date

    try:
        if len(str(until)) != 10:
            raise ValueError
        return date.fromisoformat(str(until))
    except ValueError:
        raise ValueError(f"until {until!r} is not a YYYY-MM-DD day") from None


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

    # Before any read or write: a bad day, or a real programme, stops the run here.
    day = parse_until(until)
    scopes.require_synthetic(access.program_id, "read visits into the stock ledger")
    visits = visit_source.fetch_visits(opportunity_id, access.access_token, force_refresh=refresh)
    return visit_reader.ingest_visit_consumption(access, opportunity_id=opportunity_id, visits=visits, until=day)


_WINDOW = {"type": "integer", "minimum": 30, "maximum": 730}


def _on(as_of):
    from datetime import date

    return date.fromisoformat(as_of) if as_of else None


@register_operation(
    name="worker_stock",
    summary=(
        "What we believe each field worker holds of one item, in its single unit: issued, dispensed (and how "
        "much of that rests on visits not yet approved or on protocol estimates), ledger on hand, the last "
        "count, the ledger on the count day and variance_reported_minus_ledger, days since checked, months "
        "of cover, days to stock-out, and receipts the worker reported that no recorded delivery explains. "
        "One row per worker, by name -- nothing is ranked. as_of reads a past day."
    ),
    input_schema=obj(
        {"item_id": ID, "opportunity_id": ID, "as_of": _DATE, "window_days": _WINDOW}, required=("item_id",)
    ),
)
def worker_stock(access, item_id, opportunity_id=None, as_of=None, window_days=90):
    from connect_labs.supply_chain.stock.services import belief

    item = access._resolve_item(item_id)
    rows = belief.worker_beliefs(
        access._require_program(), item, opportunity_id=opportunity_id, on_date=_on(as_of), window_days=window_days
    )
    return {
        "item_id": item.pk,
        "item_name": item.name,
        "unit": belief.unit_of(item),
        "as_of": as_of,
        "workers": [belief.wire(r) for r in rows],
    }


@register_operation(
    name="network_tree",
    summary=(
        "The network from central store to workers for one item, in its single unit. Each point carries its "
        "own figures (as worker_stock; its issued is what arrived at that one point). A store also carries "
        "subtree figures: received_from_outside (what entered the subtree from beyond it, each unit counted "
        "once), on hand, dispensed and its unapproved/estimated parts summed, cover recomputed from the "
        "subtree's own consumption (never summed), and how many workers below it are under their minimum. "
        "as_of reads a past day."
    ),
    input_schema=obj({"item_id": ID, "as_of": _DATE, "window_days": _WINDOW}, required=("item_id",)),
)
def network_tree(access, item_id, as_of=None, window_days=90):
    from connect_labs.supply_chain.stock.services import belief

    item = access._resolve_item(item_id)
    roots = belief.network_tree(access._require_program(), item, on_date=_on(as_of), window_days=window_days)
    return {
        "item_id": item.pk,
        "item_name": item.name,
        "unit": belief.unit_of(item),
        "as_of": as_of,
        "roots": [belief.wire(r) for r in roots],
    }


@register_operation(
    name="worker_stock_get",
    summary=(
        "One field worker's stock of one item: the figures worker_stock gives, their day-by-day timeline "
        "(issued up, dispensed down, counts as points), the visits behind each step (status, what the reader "
        "made of it, and the answers at the rule's paths), the stock that arrived, and any receipt the worker "
        "reported that no recorded delivery explains. as_of reads a past day."
    ),
    input_schema=obj(
        {"supply_point_id": ID, "item_id": ID, "as_of": _DATE, "window_days": _WINDOW},
        required=("supply_point_id", "item_id"),
    ),
)
def worker_stock_get(access, supply_point_id, item_id, as_of=None, window_days=90):
    from connect_labs.supply_chain.models import Movement, WorkerVisit
    from connect_labs.supply_chain.stock.services import belief, timeline
    from connect_labs.supply_chain.stock.services.visit_reader import APPROVED_STATUSES, outcome_key

    program_id = access._require_program()
    point = access._require_supply_point(supply_point_id)
    if point.kind != "user_held":
        raise ValueError(f"supply point {supply_point_id} is not a field worker's own holding")
    item = access._resolve_item(item_id)
    on_date = _on(as_of)
    visits = WorkerVisit.objects.filter(program_id=program_id, supply_point=point)
    if on_date is not None:
        visits = visits.filter(visit_date__lte=on_date)
    arrivals = (
        Movement.objects.for_program(program_id)
        .as_of(on_date)
        .filter(to_supply_point=point, item=item, kind__in=belief.ISSUE_KINDS)
        .select_related("from_supply_point")
    )
    key = outcome_key(item.pk)
    worker = belief.point_belief(program_id, point, item, on_date=on_date, window_days=window_days)
    return {
        "item_id": item.pk,
        "item_name": item.name,
        "as_of": as_of,
        "worker": belief.wire(worker),
        "timeline": timeline.worker_timeline(program_id, point, item, on_date=on_date),
        "visits": [
            {
                "visit_id": v.visit_id,
                "xform_id": v.xform_id,
                "visit_date": v.visit_date.isoformat(),
                "status": v.status,
                "approved": v.status in APPROVED_STATUSES,
                "form_name": v.form_name,
                "outcome": v.outcomes.get(key, ""),
                "answers": v.answers,
            }
            for v in visits.order_by("-visit_date", "-id")[:200]
        ],
        "arrivals": [
            {
                "occurred_on": m.occurred_on.isoformat(),
                "kind": m.kind,
                "quantity": str(m.quantity),
                "unit": m.quantity_unit,
                "from_supply_point_id": m.from_supply_point_id,
                "from_name": m.from_supply_point.name if m.from_supply_point else "",
                "reference": m.reference,
                "distribution_id": m.distribution_id,
            }
            for m in arrivals.order_by("-occurred_on", "-id")[:100]
        ],
        "unmatched_receipts": worker.unmatched_receipts,
    }
