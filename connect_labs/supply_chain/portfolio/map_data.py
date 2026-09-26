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

from datetime import date, timedelta

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
    "tender": "source",
    "supplier": "source",
    "contract": "order",
    "shipment": "order",
    "invoice": "order",
    "payment": "order",
    "charge": "order",
    "supply_point": "deliver",
    "consignment": "deliver",
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
    if kind == "consignment":
        # Late stock bites where it is owed, not where it left.
        return (check.get("facts") or {}).get("to_supply_point_id")
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


# How far back the map offers movement that has already happened. The page
# chooses a window inside it (30, 90 or 180 days); routes arrive bucketed by
# week so choosing one needs no round trip.
FLOW_HISTORY_DAYS = 180
# Movement kinds that carry stock from one place to another. A consumption, a
# loss or an adjustment happens AT a place and has no route to draw.
_ROUTED = ("transfer", "distribution", "issue", "return")


def _flows(program_id, since):
    """Movement between two places, one row per route, commodity, unit and week.

    Aggregated in the database from the program's whole ledger -- not from a
    capped page of recent movements, which silently drops history on a busy
    program. Summed per unit within one commodity, never across either, so no
    carton is ever added to a jerry can.
    """
    from django.db.models import Count, Max, Sum
    from django.db.models.functions import TruncWeek

    from connect_labs.supply_chain.models import Movement

    rows = (
        Movement.objects.for_program(program_id)
        .filter(
            kind__in=_ROUTED,
            from_supply_point__isnull=False,
            to_supply_point__isnull=False,
            occurred_on__gte=since,
        )
        .annotate(week=TruncWeek("occurred_on"))
        .values("from_supply_point_id", "to_supply_point_id", "commodity__slug", "quantity_unit", "kind", "week")
        .annotate(total=Sum("quantity"), count=Count("id"), last_on=Max("occurred_on"))
        .order_by("week")
    )
    return [
        {
            "from_supply_point_id": row["from_supply_point_id"],
            "to_supply_point_id": row["to_supply_point_id"],
            "commodity_slug": row["commodity__slug"],
            "unit": row["quantity_unit"],
            "kind": row["kind"],
            "week": row["week"].isoformat(),
            "quantity": float(row["total"]),
            "count": row["count"],
            "last_on": row["last_on"].isoformat(),
        }
        for row in rows
    ]


def _holdings(program_id, names):
    """{supply_point_id: [commodity it has held or handled]}, balanced in the database.

    In minus out per point, commodity and unit -- the ledger's own sign
    convention, in two grouped queries over the whole ledger. `held` is a
    positive balance in at least one unit, so the commodity filter can tell
    "has some" from "has dealt in it".
    """
    from django.db.models import Sum

    from connect_labs.supply_chain.models import Movement

    ledger = Movement.objects.for_program(program_id)
    balance: dict[int, dict] = {}
    for field, sign in (("to_supply_point_id", 1), ("from_supply_point_id", -1)):
        rows = (
            ledger.filter(**{f"{field}__isnull": False})
            .values(field, "commodity__slug", "quantity_unit")
            .annotate(total=Sum("quantity"))
        )
        for row in rows:
            units = balance.setdefault(row[field], {}).setdefault(row["commodity__slug"], {})
            units[row["quantity_unit"]] = units.get(row["quantity_unit"], 0) + sign * float(row["total"])
    return {
        point_id: [
            {
                "slug": slug,
                "name": names.get(slug, slug),
                "held": any(v > 0 for v in units.values()),
                "balance": {unit: round(v, 4) for unit, v in units.items() if v},
            }
            for slug, units in sorted(commodities.items())
        ]
        for point_id, commodities in balance.items()
    }


def _cover(access, commodity_slug):
    """{supply_point_id: cover} -- what each place holds of ONE commodity, and for how long.

    `network_stock` reports cover for one item at a time (months of stock is
    a quantity over a rate, and neither means anything summed across trade
    items), so it is asked once per item OF THIS COMMODITY -- only when the
    page asks for it, because it is the most expensive thing the map shows
    (a resupply plan per place per item). Where a commodity comes in several
    items the one a place holds most of speaks for it. Every figure is the
    operation's own, so the map and the Stock page cannot disagree.
    """
    by_point: dict[int, dict] = {}
    for item in call_operation("item_list", access, {}):
        if item["commodity_slug"] != commodity_slug:
            continue
        for row in call_operation("network_stock", access, {"item_id": item["id"]})["points"]:
            on_hand = row.get("on_hand") or {}
            try:
                amount = float(on_hand.get("amount")) if on_hand.get("amount") is not None else None
            except (TypeError, ValueError):
                amount = None
            cover = {
                "item_id": item["id"],
                "item_name": item.get("name") or "",
                "on_hand": on_hand,
                "amount": amount,
                "status": row.get("status") or "unknown",
                "months_of_stock": row.get("months_of_stock"),
                "resupply_quantity": row.get("resupply_quantity"),
                "min_months_of_stock": row.get("min_months_of_stock"),
                "max_months_of_stock": row.get("max_months_of_stock"),
            }
            current = by_point.get(row["supply_point_id"])
            if current is None or (amount or 0) > (current["amount"] or 0):
                by_point[row["supply_point_id"]] = cover
    return by_point


def portfolio_cover(request, portfolio, reachable, commodity_slug, *, everything=False) -> dict:
    """{program_id: {supply_point_id: cover}} for one commodity, over the same programs the page shows.

    The Stock colour mode and a place's panel fetch this when they need it.
    It answers only for programs `portfolio_programs` yields -- the page's own
    access rule -- so it can reveal nothing the page could not.
    """
    return {
        program_id: _cover(_access(request, program_id), commodity_slug)
        for program_id, _ in portfolio_programs(portfolio, reachable, everything=everything)[0]
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
    on_the_road = call_operation("consignment_list", access, {"status": "dispatched"})
    names = {c["slug"]: c["name"] for c in call_operation("commodity_list", access, {})}
    holdings = _holdings(program_id, names)
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
        # The road itself is not a place: the in-transit point is where the
        # ledger parks a consignment, and the consignment is drawn instead.
        if point["status"] != "active" or point["kind"] == "in_transit":
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
            "managed_by_org_id": point["managed_by_org_id"],
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
            # What it holds (or has dealt in) per the ledger, and what is owed
            # to it -- so the commodity filter finds a store about to receive
            # ORS as well as one holding it.
            "commodities": holdings.get(point_id, []),
            "owed_commodities": sorted(
                {
                    c["commodity_slug"]
                    for c in contracts.values()
                    if c["delivery_supply_point_id"] == point_id and c["status"] not in _NOT_OPEN
                }
            ),
            "links": {
                "dispatch": reverse("supply_chain:consignment_dispatch") + scope,
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
        "commodities": [{"slug": slug, "name": name} for slug, name in sorted(names.items(), key=lambda kv: kv[1])],
        "flows": _flows(program_id, date.today() - timedelta(days=FLOW_HISTORY_DAYS)),
        # Allocated to a place and not yet moved: a distribution line with no
        # movement, which the ledger calls committed. Drawn as on its way.
        # Our own stock on the road between two of our places (models.Consignment).
        "consignments": [
            {
                "consignment_id": c["id"],
                "reference": c["reference"],
                "from_supply_point_id": c["from_supply_point_id"],
                "to_supply_point_id": c["to_supply_point_id"],
                "commodity_slug": c["commodity_slug"],
                "quantity": c["quantity"],
                "quantity_unit": c["quantity_unit"],
                "dispatched_on": c["dispatched_on"],
                "expected_on": c["expected_on"],
                "overdue": bool(c["expected_on"] and c["expected_on"] < date.today().isoformat()),
                "receive_url": reverse("supply_chain:consignment_receive", args=[c["id"]]) + scope,
            }
            for c in on_the_road
        ],
        "committed": [
            {
                "distribution_id": d["id"],
                "reference": d["reference"],
                "from_supply_point_id": d["supply_point_id"],
                "to_supply_point_id": line["to_supply_point_id"],
                "quantity": line["quantity"],
                "quantity_unit": line["quantity_unit"],
                "since": d["distributed_on"],
            }
            for d in call_operation("distribution_list", access, {"limit": 500})
            for line in d["lines"]
            if line["movement_id"] is None
        ],
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


def portfolio_programs(portfolio, reachable, *, everything=False):
    """([(program_id, in_portfolio)], hidden) -- the ONE access rule the map has.

    The portfolio's own programs in its stated order, keeping only those the
    viewer can already reach; with `everything`, every other reachable program
    that has a supply point after them. Shared by the page and the cover
    endpoint, so the two cannot disagree about what a viewer may see.
    """
    from connect_labs.supply_chain.models import SupplyPoint

    programs, hidden, seen = [], 0, set()
    for stated in portfolio.program_ids:
        try:
            program_id = int(stated)
        except (TypeError, ValueError):
            hidden += 1
            continue
        if program_id not in reachable:
            hidden += 1
            continue
        seen.add(program_id)
        programs.append((program_id, True))
    if everything:
        with_points = set(
            SupplyPoint.objects.filter(program_id__in=list(reachable), status="active")
            .values_list("program_id", flat=True)
            .distinct()
        )
        programs += [(program_id, False) for program_id in sorted(with_points - seen)]
    return programs, hidden


def portfolio_map(request, portfolio, reachable, *, everything=False) -> dict:
    """Every reachable program in the portfolio's own order, plus what is hidden.

    With `everything`, every OTHER program the viewer can reach that has a
    supply point follows the portfolio's own -- "all the supply points we know
    about". The access rule is `portfolio_programs`: nothing outside
    `reachable` is ever read.
    """
    chosen, hidden = portfolio_programs(portfolio, reachable, everything=everything)
    programs = [
        {**program_map(request, program_id, reachable[program_id]), "in_portfolio": in_portfolio}
        for program_id, in_portfolio in chosen
    ]
    return {
        "portfolio": {"slug": portfolio.slug, "name": portfolio.name},
        "stated": len(portfolio.program_ids),
        "hidden": hidden,
        "everything": everything,
        "cover_url": reverse("supply_chain:portfolio_map_cover", args=[portfolio.slug])
        + ("?scope=all" if everything else ""),
        "programs": programs,
        "network": network_members(),
        "vocabulary": _vocabulary(),
    }


def network_members() -> list[dict]:
    """Every organisation in the directory that has a head office on the map.

    The same `OrgProfile` coordinates the Pulse network page draws, with their
    precision. Names only -- no contacts -- and the page is signed-in, which is
    the entitlement Pulse's own network page asks for.
    """
    from connect_labs.marketplace.models import OrgProfile

    rows = OrgProfile.objects.filter(lat__isnull=False, lon__isnull=False).select_related("org").order_by("org__name")
    return [
        {
            "org_id": row.org_id,
            "slug": row.org.slug,
            "name": row.org.name,
            "short": row.org.short_name,
            "lat": row.lat,
            "lng": row.lon,
            "precision": row.location_precision,
            "place": row.location_label,
            "country": row.country_iso3,
        }
        for row in rows
    ]


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
