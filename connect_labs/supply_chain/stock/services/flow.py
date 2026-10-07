"""Where one item went: from what arrived, through the stores, to each worker, to the visits.

The ledger already holds every step; this reads it as a flow. One grouped
query sums every movement of the item by route and week, so the page costs
the same for twenty workers or six hundred. Each route's total is then
carried forward week by week, so the page can draw any week as it stood
without asking again.

Routes, read from a movement's two ends:

  nothing -> point       stock arriving: from the order its receipt or
                         shipment belongs to, else "received, no order linked"
  point -> point         a transfer, an issue, a distribution down the chain
  worker -> nothing      `consumption`: given out at visits (a reversal, which
                         comes back the other way, takes its quantity off)
  point -> nothing       `loss` / `expiry`: lost or expired; anything else
                         leaving the network: sent elsewhere

An `adjustment` is a correction to a balance, not stock going anywhere, so it
is left out of the drawing and counted in `adjusted`. Counts are not
movements and are not drawn either. A quantity in a unit the item cannot be
converted into is left out and named in `left_out`, never guessed.

Facts only: nothing here ranks a worker or judges a store (September design
§22). Workers are ordered by the store that restocks them, then by name.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncWeek
from django.urls import reverse

from connect_labs.supply_chain.models import Contract, Movement, SupplyPoint
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.stock.services.belief import unit_of
from connect_labs.supply_chain.values import Quantity

ZERO = Decimal(0)

GIVEN = "given"
LOST = "lost"
ELSEWHERE = "elsewhere"
UNLINKED = "unlinked"
SINKS = {
    GIVEN: "Given out at visits",
    LOST: "Lost or expired",
    ELSEWHERE: "Sent outside the network",
}
# Most weeks a page draws: a programme older than this starts its slider here.
MAX_WEEKS = 104


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _route(row) -> tuple[str, str, int] | None:
    """(from key, to key, sign) for one grouped row, or None when it is not drawn."""
    kind, frm, to = row["kind"], row["from_supply_point_id"], row["to_supply_point_id"]
    if kind == "adjustment":
        return None
    if frm is not None and to is not None:
        return f"p{frm}", f"p{to}", 1
    if frm is not None:
        if kind == "consumption":
            return f"p{frm}", GIVEN, 1
        return f"p{frm}", LOST if kind in ("loss", "expiry") else ELSEWHERE, 1
    if to is not None:
        if kind == "consumption":
            # A reversal: a rejected visit's stock put back in the bag.
            return f"p{to}", GIVEN, -1
        contract = row["receipt__contract_id"] or row["shipment__contract_id"]
        return (f"c{contract}" if contract else UNLINKED), f"p{to}", 1
    return None


def _depths(points) -> dict[int, int]:
    by_id = {p.pk: p for p in points}
    depths = {}
    for point in points:
        depth, node, seen = 0, point, set()
        while node.parent_id and node.parent_id in by_id and node.pk not in seen:
            seen.add(node.pk)
            node = by_id[node.parent_id]
            depth += 1
        depths[point.pk] = depth
    return depths


def _contract_label(contract) -> str:
    supplier = contract.supplier.name if contract.supplier_id else "a supplier"
    return f"Order from {supplier}" + (f" ({contract.reference})" if contract.reference else "")


def flow(program_id: int, item, *, on_date: date | None = None) -> dict:  # noqa: C901
    on_date = on_date or date.today()
    unit = unit_of(item)
    rows = list(
        Movement.objects.filter(program_id=program_id, item=item, occurred_on__lte=on_date)
        .annotate(week=TruncWeek("occurred_on"))
        .values(
            "kind",
            "from_supply_point_id",
            "to_supply_point_id",
            "quantity_unit",
            "receipt__contract_id",
            "shipment__contract_id",
            "week",
        )
        .annotate(total=Sum("quantity"))
    )

    first = min((r["week"] for r in rows), default=None)
    if first is None:
        return {"item_id": item.pk, "item_name": item.name, "unit": unit, "as_of": on_date.isoformat(), "weeks": []}
    first = max(
        _monday(first if isinstance(first, date) else first.date()), _monday(on_date) - timedelta(weeks=MAX_WEEKS - 1)
    )
    weeks = []
    monday = first
    while monday <= on_date:
        weeks.append(monday)
        monday += timedelta(weeks=1)
    index = {week: i for i, week in enumerate(weeks)}

    weekly: dict[tuple[str, str], list[Decimal]] = {}
    left_out: dict[str, Decimal] = {}
    adjusted = ZERO
    for row in rows:
        week = row["week"].date() if hasattr(row["week"], "date") else row["week"]
        position = index.get(max(week, first), 0)
        converted = ledger.convert(row["total"], row["quantity_unit"], unit, item)
        if not isinstance(converted, Quantity):
            left_out[row["quantity_unit"]] = left_out.get(row["quantity_unit"], ZERO) + row["total"]
            continue
        if row["kind"] == "adjustment":
            adjusted += converted.amount
            continue
        route = _route(row)
        if route is None:
            continue
        frm, to, sign = route
        series = weekly.setdefault((frm, to), [ZERO] * len(weeks))
        series[position] += sign * converted.amount

    # Carried forward: each week is everything up to the end of it.
    links = []
    for (frm, to), series in sorted(weekly.items()):
        running, cumulative = ZERO, []
        for amount in series:
            running += amount
            cumulative.append(running)
        if any(cumulative):
            links.append({"source": frm, "target": to, "series": [_number(v) for v in cumulative]})

    keys = {k for link in links for k in (link["source"], link["target"])}
    point_ids = {int(k[1:]) for k in keys if k.startswith("p")}
    points = list(SupplyPoint.objects.filter(program_id=program_id, pk__in=point_ids).select_related("parent"))
    depths = _depths(points)
    store_depth = max((depths[p.pk] for p in points if p.kind != "user_held"), default=-1)
    worker_column = store_depth + 2
    nodes = []
    for point in points:
        is_worker = point.kind == "user_held"
        nodes.append(
            {
                "id": f"p{point.pk}",
                "name": point.name,
                "kind": "worker" if is_worker else "store",
                "column": worker_column if is_worker else depths[point.pk] + 1,
                "parent": f"p{point.parent_id}" if point.parent_id else None,
                "parent_name": point.parent.name if point.parent_id else "",
                "url": (
                    reverse("supply_chain:worker_detail", args=[point.pk]) + f"?item_id={item.pk}"
                    if is_worker
                    else reverse("supply_chain:network")
                ),
            }
        )
    contracts = {
        c.pk: c
        for c in Contract.objects.filter(pk__in={int(k[1:]) for k in keys if k.startswith("c")}).select_related(
            "supplier"
        )
    }
    for key in sorted(k for k in keys if k.startswith("c") or k == UNLINKED):
        contract = contracts.get(int(key[1:])) if key.startswith("c") else None
        nodes.append(
            {
                "id": key,
                "name": _contract_label(contract) if contract else "Received, no order linked",
                "kind": "source",
                "column": 0,
                "url": reverse("supply_chain:order_detail", args=[contract.pk]) if contract else "",
            }
        )
    sink_column = max((n["column"] for n in nodes), default=0) + 1
    for key, label in SINKS.items():
        if key in keys:
            nodes.append({"id": key, "name": label, "kind": "sink", "column": sink_column, "url": ""})

    return {
        "item_id": item.pk,
        "item_name": item.name,
        "unit": unit,
        "as_of": on_date.isoformat(),
        "weeks": [w.isoformat() for w in weeks],
        "nodes": nodes,
        "links": links,
        "adjusted": _number(adjusted),
        "left_out": {u: _number(v) for u, v in left_out.items()},
    }


def _number(value: Decimal):
    """A JSON number: whole quantities as ints, the rest to three places."""
    return int(value) if value == value.to_integral_value() else float(round(value, 3))
