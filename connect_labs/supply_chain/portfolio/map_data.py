"""Where a portfolio's stock is, and what is standing in its way -- as one payload.

The map is the portfolio page turned on its side: the same programs, the same
access rule, the same operations, laid out by place instead of by chain. It
adds no figure of its own. Every number on it is one an existing operation
already returns (`network_stock`, `checks_list`), so the map and the
program's own Stock and Checks pages cannot disagree.

Three rules carry over from the portfolio unchanged, and each is load-bearing:

**Access.** Programs come from `reachable_programmes` -- the portfolio's own
rule, which is the program picker's list. The map has no second rule.

**Nothing located is dropped in silence.** A point without coordinates is not
on the map, and a map that simply omitted it would say "nothing here" about a
store that may be stocked out. Every such point is returned in `unplaced`,
carrying the same status and blockers a placed one would, and the page lists
them beside the map with a link to set the location.

**Nothing is summed across programs.** Each point carries its own figure in
its own unit. What the page counts is PLACES -- "3 stores stocked out" -- which
is a count of rows, not a quantity, and implies no conversion between a carton
of co-pack and a jerry can of chlorine.

Blockers are the domain's own checks, attributed to the place they bite:

  - a check about a supply point sits on that point;
  - a check about an order or a shipment sits on the point the order delivers to;
  - anything else (a quote, an award, a catalogue gap) has no place, and is
    returned per program under `unlocated_checks` so the page can still say
    it exists.
"""

from django.urls import reverse

from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import records
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

# A shipment in one of these has left and has not arrived. The domain's own
# list, so "moving" on the map means what `position()` means by in transit.
IN_TRANSIT = set(records.IN_TRANSIT_STATUSES)

# Which part of the chain a check's subject belongs to, in the three phases
# every other supply screen uses. Most of what stands in the way is not at a
# place -- a quote that cannot be compared, a product failing its spec -- so
# the map's panel groups blockers by stage, and only the place-bound ones
# also light up a point.
STAGE_OF = {
    "quote": "source",
    "award": "source",
    "commodity": "source",
    "item": "source",
    "round": "source",
    "supplier": "source",
    "contract": "order",
    "shipment": "order",
    "invoice": "order",
    "payment": "order",
    "charge": "order",
    "supply_point": "deliver",
    "distribution": "deliver",
    "movement": "deliver",
}

# An order that is settled, called off or not yet placed has nothing on its way.
_NOT_OPEN = ("draft", "received", "closed", "cancelled")


def _supplier_location(supplier):
    """Where a supplier's goods leave from, as finely as its record allows, or None.

    Read from the supplier's own city and country through Pulse's resolver --
    a town in the gazetteer is a pin, a country is its centre -- and returned
    with that precision, because a supplier's city is not its warehouse and
    the map draws it as a stand-in. Isolated here because suppliers are about
    to become global profiles: when a profile carries a location of its own,
    this is the one function that should read it instead.
    """
    from connect_labs.microplans.core import iso as iso_codes
    from connect_labs.pulse.hq_location import resolve

    country = supplier.get("country") or ""
    if not country:
        return None
    found = resolve(iso_codes.country_name(country) or country, "", supplier.get("city") or "")
    if found is None:
        return None
    return {"lat": found.lat, "lng": found.lon, "precision": found.precision, "label": found.label}


def _access(request, program_id):
    """A data-access object for one program, deliberately without `request`.

    For the reason `PortfolioView._row` gives: passing the request merges the
    session's selected opportunity into every program's scope. The caller
    still carries it, so the scope is authorised a second time below.
    """
    return SupplyDataAccess(
        access_token=(request.session.get("labs_oauth") or {}).get("access_token"),
        program_id=program_id,
        user=request.user,
        caller=Caller(request=request, user=request.user),
    )


def _placed(point) -> bool:
    lat, lng = point.get("latitude"), point.get("longitude")
    return lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180


def _attribute(check, contracts, shipments):
    """The supply point a check bites at, or None when it has no place."""
    subject = check.get("subject") or {}
    kind, subject_id = subject.get("type"), subject.get("id")
    if kind == "supply_point":
        return subject_id
    contract_id = None
    if kind == "contract":
        contract_id = subject_id
    elif kind == "shipment":
        contract_id = (shipments.get(subject_id) or {}).get("contract_id")
    if contract_id is None:
        contract_id = (check.get("facts") or {}).get("contract_id")
    if contract_id is None:
        return None
    return (contracts.get(contract_id) or {}).get("delivery_supply_point_id")


def _check_wire(check, *, point_id=None, scope=""):
    """The fields the page shows; the facts travel whole, unworded."""
    from connect_labs.supply_chain.templatetags.supply_chain_extras import check_href

    href = check_href(check)
    return {
        "stage": STAGE_OF.get((check.get("subject") or {}).get("type"), "source"),
        "supply_point_id": point_id,
        "href": href + scope if href else "",
        "kind": check["kind"],
        "category": check["category"],
        "audience": check["audience"],
        "subject": check["subject"],
        "days_open": check["days_open"],
        "facts": check["facts"],
    }


def program_map(request, program_id, program) -> dict:
    """One program's places, blockers and consignments in motion."""
    access = _access(request, program_id)
    scope = f"?program_id={program_id}"

    points = {p["id"]: p for p in call_operation("supply_point_list", access, {})}
    stock = {row["supply_point_id"]: row for row in call_operation("network_stock", access, {})["points"]}
    checks = call_operation("checks_list", access, {})["checks"]
    contracts = {c["id"]: c for c in call_operation("contract_list", access, {})}
    shipments = {s["id"]: s for s in call_operation("shipment_list", access, {})}
    suppliers = {s["id"]: s for s in call_operation("supplier_list", access, {})}
    # Organisations are labs-wide rather than program-scoped (org_list says
    # so), so naming the ones that manage these points reveals nothing the
    # program does not already hold. Only the ids in play are read, rather
    # than the whole registry once per program.
    orgs = {
        o["id"]: o
        for o in LabsOrg.objects.filter(
            pk__in={p["managed_by_org_id"] for p in points.values() if p["managed_by_org_id"]}
        ).values("id", "name")
    }

    by_point: dict[int, list] = {}
    unlocated, every = [], []
    for check in checks:
        point_id = _attribute(check, contracts, shipments)
        wired = _check_wire(check, point_id=point_id if point_id in points else None, scope=scope)
        every.append(wired)
        if point_id in points:
            by_point.setdefault(point_id, []).append(wired)
        else:
            unlocated.append(wired)

    # Where a supplier's goods leave from, when the program has recorded one:
    # a supplier_site point managed by the supplier's own organisation. Without
    # one there is no origin to draw from, and the consignment is shown at its
    # destination only rather than from an invented place.
    origin_by_org = {
        p["managed_by_org_id"]: p["id"]
        for p in points.values()
        if p["kind"] == "supplier_site" and p["managed_by_org_id"] and _placed(p)
    }

    moving = []
    for shipment in shipments.values():
        if shipment["status"] not in IN_TRANSIT:
            continue
        contract = contracts.get(shipment["contract_id"]) or {}
        destination = contract.get("delivery_supply_point_id")
        supplier = suppliers.get(contract.get("supplier_id")) or {}
        moving.append(
            {
                "shipment_id": shipment["id"],
                "reference": shipment["reference"],
                "status": shipment["status"],
                "expected_on": shipment["expected_on"],
                "dispatched_on": shipment["dispatched_on"],
                "carrier": shipment["carrier"],
                "contract_id": shipment["contract_id"],
                "supplier": supplier.get("name") or "",
                "from_supply_point_id": origin_by_org.get(supplier.get("org_id")),
                "to_supply_point_id": destination,
                "url": reverse("supply_chain:shipment_detail", args=[shipment["id"]]) + scope,
                "order_url": reverse("supply_chain:order_detail", args=[shipment["contract_id"]]) + scope,
            }
        )

    placed_points, unplaced = [], []
    for point_id, point in points.items():
        if point["status"] != "active":
            continue
        row = stock.get(point_id) or {}
        manager = orgs.get(point["managed_by_org_id"]) or {}
        entry = {
            "id": point_id,
            "program_id": program_id,
            "name": point["name"],
            "kind": point["kind"],
            "admin_area": point["admin_area"],
            "opportunity_id": point["opportunity_id"],
            "parent_id": point["parent_supply_point_id"],
            "managed_by": manager.get("name") or "",
            "lat": point["latitude"],
            "lng": point["longitude"],
            # Whether the coordinates are the place itself or a stand-in (its
            # organisation's head office, its parent, its country's centre).
            # The map draws a stand-in as approximate and says which it is.
            "location": {
                "source": point.get("location_source") or "",
                "precision": point.get("location_precision") or "",
                "label": point.get("location_label") or "",
            },
            # A supplier's own site is where goods leave FROM; it keeps none of
            # the program's stock, so "cannot be computed" would be true and
            # misleading. Anything else with no row stays "unknown".
            "status": "origin" if point["kind"] == "supplier_site" else (row.get("status") or "unknown"),
            "on_hand": row.get("on_hand"),
            "months_of_stock": row.get("months_of_stock"),
            "days_to_stockout": row.get("days_to_stockout"),
            "reported": row.get("reported"),
            "reported_on": row.get("reported_on"),
            "min_months_of_stock": row.get("min_months_of_stock"),
            "max_months_of_stock": row.get("max_months_of_stock"),
            "expected_inbound": [
                {**expected, "order_url": reverse("supply_chain:order_detail", args=[expected["contract_id"]]) + scope}
                for expected in row.get("expected_inbound") or []
            ],
            "checks": by_point.get(point_id, []),
            "links": {
                "movements": reverse("supply_chain:movements") + f"{scope}&supply_point_id={point_id}",
                "edit": reverse("supply_chain:supply_point_edit", args=[point_id]) + scope,
                "network": reverse("supply_chain:network") + scope,
            },
        }
        (placed_points if _placed(point) else unplaced).append(entry)

    # Every order with something still to come, as a route from its supplier
    # to the store it is owed to. What is outstanding and when it was promised
    # come from network_stock's own expected_inbound, so the map and the
    # Stock page cannot disagree about what is owed.
    owed = {
        expected["contract_id"]: expected for row in stock.values() for expected in row.get("expected_inbound") or []
    }
    orders, supplier_ids = [], set()
    for contract in contracts.values():
        if contract["status"] in _NOT_OPEN:
            continue
        expected = owed.get(contract["id"]) or {}
        supplier_ids.add(contract["supplier_id"])
        orders.append(
            {
                "contract_id": contract["id"],
                "reference": contract["reference"],
                "status": contract["status"],
                "supplier_id": contract["supplier_id"],
                "to_supply_point_id": contract["delivery_supply_point_id"],
                "outstanding": expected.get("outstanding"),
                "item_name": expected.get("item_name") or "",
                # Already ISO on the wire; None when no date was ever promised.
                "expected_on": expected.get("expected_on"),
                "overdue": bool(expected.get("overdue")),
                "url": reverse("supply_chain:order_detail", args=[contract["id"]]) + scope,
            }
        )
    for shipment in moving:
        supplier_ids.add((contracts.get(shipment["contract_id"]) or {}).get("supplier_id"))
    located_suppliers = []
    for supplier_id in sorted(i for i in supplier_ids if i):
        supplier = suppliers.get(supplier_id)
        if not supplier:
            continue
        located_suppliers.append(
            {
                "id": supplier_id,
                "name": supplier["name"],
                "country": supplier.get("country") or "",
                "city": supplier.get("city") or "",
                "location": _supplier_location(supplier),
                "url": reverse("supply_chain:supplier_detail", args=[supplier_id]) + scope,
            }
        )

    return {
        "program_id": program_id,
        "name": program.get("name") or f"Program {program_id}",
        "summary": call_operation("chain_summary", access, {}),
        "checks": every,
        "orders": orders,
        "suppliers": located_suppliers,
        "home_url": reverse("supply_chain:home") + scope,
        "checks_url": reverse("supply_chain:checks") + scope,
        "new_point_url": reverse("supply_chain:supply_point_create") + scope,
        "points": placed_points,
        "unplaced": unplaced,
        "moving": moving,
        "unlocated_checks": unlocated,
    }


def portfolio_map(request, portfolio, reachable) -> dict:
    """Every reachable program in the portfolio's own order, plus what is hidden."""
    programs, hidden = [], 0
    for stated in portfolio.program_ids:
        try:
            program_id = int(stated)
        except (TypeError, ValueError):
            hidden += 1
            continue
        if program_id not in reachable:
            hidden += 1
            continue
        programs.append(program_map(request, program_id, reachable[program_id]))
    return {
        "portfolio": {"slug": portfolio.slug, "name": portfolio.name},
        "stated": len(portfolio.program_ids),
        "hidden": hidden,
        "programs": programs,
        "vocabulary": _vocabulary(),
    }


def _vocabulary():
    """The domain's own words, so the page does not keep a second copy of them.

    A check's label, an audience's and a category's are the ones the Checks
    page renders; the map reads them from here rather than re-wording them in
    JavaScript, where they would drift the first time one changed.
    """
    from connect_labs.supply_chain.checks import KIND_CATEGORIES
    from connect_labs.supply_chain.templatetags.supply_chain_extras import (
        AUDIENCE_LABELS,
        CATEGORY_LABELS,
        CHECK_LABELS,
    )

    return {
        "kinds": list(records.SUPPLY_POINT_KINDS),
        "check_categories": KIND_CATEGORIES,
        "check_labels": CHECK_LABELS,
        "audience_labels": AUDIENCE_LABELS,
        "category_labels": CATEGORY_LABELS,
    }
