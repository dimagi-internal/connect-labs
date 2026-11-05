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

from django.db.models import Q, Sum
from django.db.models.functions import TruncWeek
from django.urls import reverse

from connect_labs.supply_chain.models import Contract, Movement, SupplyPoint
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.stock.services.belief import unit_of
from connect_labs.supply_chain.values import Quantity

ZERO = Decimal(0)

GIVEN = "given"
LOST = "lost"
TAKEN = "taken"
EXPIRED = "expired"
DAMAGED = "damaged"
ELSEWHERE = "elsewhere"
UNLINKED = "unlinked"
SINKS = {
    GIVEN: "Given out at visits",
    LOST: "Lost or missing",
    TAKEN: "Taken",
    EXPIRED: "Expired",
    DAMAGED: "Damaged",
    ELSEWHERE: "Sent outside the network",
}
# What a stock issue of each kind is drawn as when its stock leaves the shelf. Taken, expired and damaged
# are different problems (a diversion, a date, a breakage), so each is its own stream; the rest of the
# losses (missing, other, a loss recorded with no issue) stay "lost or missing".
ISSUE_STREAM = {"taken": TAKEN, "expired": EXPIRED, "damaged": DAMAGED}
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
        if kind == "expiry":
            return f"p{frm}", EXPIRED, 1
        if kind == "loss":
            return f"p{frm}", ISSUE_STREAM.get(row.get("stock_issue__kind"), LOST), 1
        return f"p{frm}", ELSEWHERE, 1
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


def flow(program_id: int, item, *, on_date: date | None = None, point_ids=None) -> dict:  # noqa: C901
    """`point_ids` narrows the flow to the movements that start or end at those places (one partner's)."""
    on_date = on_date or date.today()
    unit = unit_of(item)
    movements = Movement.objects.filter(program_id=program_id, item=item, occurred_on__lte=on_date)
    if point_ids is not None:
        movements = movements.filter(Q(from_supply_point_id__in=point_ids) | Q(to_supply_point_id__in=point_ids))
    rows = list(
        movements.annotate(week=TruncWeek("occurred_on"))
        .values(
            "kind",
            "stock_issue__kind",
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
        "account": (acct := account(links, points, adjusted, mine=point_ids)),
        "household": _with_records(household(program_id, item, on_date, point_ids=point_ids), acct),
        "flw_stock": _flw_stock(program_id, item, on_date, point_ids),
    }


def _flw_stock(program_id, item, on_date, point_ids=None) -> dict:
    """With the field workers: the official figure and both sides (official_stock.flw_totals), as numbers."""
    from connect_labs.supply_chain.stock.services import official_stock

    t = official_stock.flw_totals(program_id, item, on_date, point_ids=point_ids)
    return {k: (_number(v) if isinstance(v, Decimal) else v) for k, v in t.items()}


def _with_records(home: dict | None, acct: dict) -> dict | None:
    """The household figures with the stock records' 'given out' beside them, so one line can say why they differ."""
    if home is None:
        return None
    given = next((st["amount"] for st in acct["stages"] if st["key"] == "given"), None)
    return {**home, "given_in_records": given}


# The stages of the account, in the order stock travels. A label is the whole
# message, so a reader never needs the diagram to know where stock is.
ACCOUNT_STAGES = (
    ("warehouse", "Still in warehouse", "Stock held at the warehouses and stores, not yet passed down."),
    ("phc", "At PHCs", "Stock held at health facilities, waiting for field workers to collect it."),
    ("flw", "With FLWs", "Stock held by field workers, not yet given to a child."),
    ("given", "Given out to children", "What field workers' visits recorded as given out."),
    ("lost", "Lost or missing", "Stock recorded as missing, or lost with no reason given."),
    ("taken", "Taken", "Stock someone took: diversions, recorded as stock issues."),
    ("expired", "Expired", "Stock that passed its expiry date and came off the shelf."),
    ("damaged", "Damaged", "Stock recorded as damaged."),
    ("elsewhere", "Sent outside the network", "Stock passed to someone outside this network."),
    ("shortfall", "Count shortfalls", "Net corrections made when a count did not match the records."),
)


# One colour per stage for the bar; the label beside it carries the meaning, so colour never has to.
STAGE_COLORS = {
    "warehouse": "#4f46e5",
    "phc": "#818cf8",
    "flw": "#06b6d4",
    "given": "#10b981",
    "lost": "#ef4444",
    "taken": "#b91c1c",
    "expired": "#f97316",
    "damaged": "#a16207",
    "elsewhere": "#f59e0b",
    "shortfall": "#9ca3af",
}


def account(links, points, adjusted, mine=None) -> dict:
    """The whole account in words: what arrived, where it is now and where it went, with shares.

    Read from the last week of every route, so it is the same total the
    diagram ends on. A count correction is a stream of its own (a shortfall
    when negative), never netted into another line.
    """
    kind_of = {f"p{p.pk}": p.kind for p in points}
    held: dict[str, Decimal] = {}
    arrived = given = lost = elsewhere = taken = expired = damaged = ZERO
    # With `mine`, only the places named count as where stock is; stock another partner sent in is an arrival.
    inside = {f"p{pk}" for pk in mine} if mine is not None else None
    for link in links:
        final = Decimal(str(link["series"][-1]))
        src, dst = link["source"], link["target"]
        if src.startswith("c") or src == UNLINKED or (inside is not None and src in kind_of and src not in inside):
            arrived += final
        elif src in kind_of:
            held[src] = held.get(src, ZERO) - final
        if dst == GIVEN:
            given += final
        elif dst == LOST:
            lost += final
        elif dst == TAKEN:
            taken += final
        elif dst == EXPIRED:
            expired += final
        elif dst == DAMAGED:
            damaged += final
        elif dst == ELSEWHERE:
            elsewhere += final
        elif dst in kind_of and (inside is None or dst in inside):
            held[dst] = held.get(dst, ZERO) + final
    stage = {"warehouse": ZERO, "phc": ZERO, "flw": ZERO}
    for key, amount in held.items():
        if inside is not None and key not in inside:
            continue
        kind = kind_of[key]
        stage["flw" if kind == "user_held" else "phc" if kind == "facility" else "warehouse"] += amount
    amounts = {
        **stage,
        "given": given,
        "lost": lost,
        "taken": taken,
        "expired": expired,
        "damaged": damaged,
        "elsewhere": elsewhere,
        "shortfall": -adjusted,
    }

    def share(amount):
        return int((amount / arrived * 100).quantize(Decimal(1))) if arrived > 0 else None

    return {
        "arrived": _number(arrived),
        "stages": [
            {
                "key": key,
                "label": label,
                "help": help_,
                "amount": _number(amounts[key]),
                "percent": share(amounts[key]),
                # Width on the stacked bar: exact, not the rounded percent; nothing for a stream with none.
                "share": float(round(amounts[key] / arrived * 100, 2)) if arrived > 0 and amounts[key] > 0 else 0,
                "color": STAGE_COLORS[key],
            }
            for key, label, help_ in ACCOUNT_STAGES
            # Only the streams that exist are streams; the first five always read.
            if key in ("warehouse", "phc", "flw", "given", "lost") or amounts[key] != 0
        ],
    }


def _first_kept(answers, paths):
    """The first of `paths` a visit kept an answer for (`answers` is {path: raw answer}), else None."""
    for path in paths or []:
        if answers.get(path) is not None:
            return answers[path]
    return None


def _reason_tokens(raw) -> list[str]:
    """The reasons a multi-select answer holds: CommCare stores one answer as space-separated choice names."""
    return [token for token in str(raw).split() if token] if raw is not None else []


def household(program_id: int, item, on_date: date, point_ids=None) -> dict | None:
    """What the delivery form says happened to the sachets, totalled; None when no rule asks it.

    Read from the answers each visit kept at the paths the rule is read at (`household_paths`), so a
    programme whose app does not ask a question simply has no figure for it (None, shown as "not
    recorded"), never a zero or a guess. A blank or non-numeric answer is no answer. A rejected or
    duplicate visit says nothing. `visits` is how many visits answered at least one.

    The reasons answer is a multi-select, so it is split into one reason per choice. A caregiver who
    answers "none" has told us nothing was misused: that is a RECORDED ZERO for the misused line at that
    visit (when the number itself was left blank), counted in `no_misuse_visits`, not a reason.

    The lines add up: eaten + leftover + missing + misused is what could be checked, and the rest of
    what was dispensed is each child's latest ration, which cannot be checked until their next visit
    (`not_yet_checked`, the manager dashboard's reading).
    """
    from connect_labs.supply_chain.models import DispensingRule, WorkerVisit
    from connect_labs.supply_chain.stock.services.dispensing import household_paths, read_number
    from connect_labs.supply_chain.stock.services.visit_reader import REVERSING_STATUSES

    rules = [
        (rule, paths)
        for rule in DispensingRule.objects.filter(program_id=program_id, item=item, status="active")
        if (paths := household_paths(rule))
    ]
    if not rules:
        return None
    totals = {key: ZERO for key in HOUSEHOLD_TOTALS}
    answered = {key: False for key in HOUSEHOLD_TOTALS}
    answered_visits = {key: 0 for key in HOUSEHOLD_TOTALS}
    reasons: dict[str, int] = {}
    no_misuse = 0
    visits = 0
    for rule, paths in rules:
        rows = (
            WorkerVisit.objects.filter(program_id=program_id, opportunity_id=rule.opportunity_id)
            .filter(visit_date__lte=on_date)
            .exclude(status__in=REVERSING_STATUSES)
            .values_list("answers", flat=True)
        )
        if point_ids is not None:
            rows = rows.filter(supply_point_id__in=point_ids)
        for answers in rows:
            answers = answers or {}
            got = False
            seen_here = set()
            for key in totals:
                amount = read_number(_first_kept(answers, paths.get(f"{key}_paths")))
                if amount is not None:
                    totals[key] += amount
                    answered[key] = got = True
                    answered_visits[key] += 1
                    seen_here.add(key)
            tokens = _reason_tokens(_first_kept(answers, paths.get("reason_paths")))
            if tokens:
                got = True
                real = [t for t in tokens if t.lower() != "none"]
                if not real:
                    # "None" is an answer: nothing misused. Record the zero if the number was left blank.
                    no_misuse += 1
                    if "misused" not in seen_here:
                        answered["misused"] = True
                        answered_visits["misused"] += 1
                for token in dict.fromkeys(real):
                    label = token.replace("_", " ")
                    reasons[label] = reasons.get(label, 0) + 1
            visits += got
    amounts = {key: (totals[key] if answered[key] else None) for key in totals}
    checked_keys = ("returned", "leftover", "missing", "misused")
    checked = sum((totals[k] for k in checked_keys if answered[k]), ZERO)
    dispensed = amounts["dispensed"]
    amounts["not_yet_checked"] = max(ZERO, dispensed - checked) if dispensed is not None else None
    out_rows = []
    for key, label, caption in HOUSEHOLD_LINES:
        amount = amounts[key]
        out_rows.append(
            {
                "key": key,
                "label": label,
                "caption": caption,
                "amount": _number(amount) if amount is not None else None,
                "visits": answered_visits.get(key),
            }
        )
    return {
        "visits": visits,
        **{key: (_number(v) if v is not None else None) for key, v in amounts.items()},
        "checked": _number(checked),
        "rows": out_rows,
        "no_misuse_visits": no_misuse,
        "reasons": [{"reason": r, "visits": n} for r, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


def appetite_by_worker(program_id: int, item, anchor: date, weeks: int = 3) -> dict[int, Decimal]:
    """{supply point id: sachets a week} used in appetite tests at the worker's visits, over the last `weeks` weeks.

    Read at the same household paths Where it went reads (`household_paths`, `appetite_paths`), so the Plan
    and Where it went cannot disagree about them. A worker with none recorded is simply absent.
    """
    from connect_labs.supply_chain.models import DispensingRule, WorkerVisit
    from connect_labs.supply_chain.stock.services.dispensing import household_paths, read_number
    from connect_labs.supply_chain.stock.services.visit_reader import REVERSING_STATUSES

    since = anchor - timedelta(days=7 * weeks - 1)
    out: dict[int, Decimal] = {}
    for rule in DispensingRule.objects.filter(program_id=program_id, item=item, status="active"):
        paths = (household_paths(rule) or {}).get("appetite_paths")
        if not paths:
            continue
        rows = (
            WorkerVisit.objects.filter(program_id=program_id, opportunity_id=rule.opportunity_id)
            .filter(visit_date__gte=since, visit_date__lte=anchor)
            .exclude(status__in=REVERSING_STATUSES)
            .values_list("supply_point_id", "answers")
        )
        for point_id, answers in rows:
            amount = read_number(_first_kept(answers or {}, paths))
            if amount is not None and point_id is not None:
                out[point_id] = out.get(point_id, ZERO) + amount
    return {point: total / weeks for point, total in out.items()}


# The household streams a rule's `<name>_paths` read. `not_yet_checked` is worked out, not read.
HOUSEHOLD_TOTALS = ("dispensed", "appetite", "returned", "leftover", "missing", "misused")
HOUSEHOLD_LINES = (
    ("dispensed", "Dispensed to children", "Sachets the delivery form says were handed over at the visits."),
    (
        "returned",
        "Eaten (empty sachets returned)",
        "Empty sachets the caregiver gave back: the sachets that were eaten.",
    ),
    ("leftover", "Leftover at the home", "Sachets still unopened at the home when the worker came back."),
    ("missing", "Missing", "Sachets the caregiver could not account for. A loss, counted on its own."),
    ("misused", "Misused", "Sachets used for something other than the child's treatment. A loss, counted on its own."),
    (
        "not_yet_checked",
        "Given at the latest visits, not yet checked",
        "Each child's latest ration cannot be checked until the next visit: dispensed less the four lines above.",
    ),
    (
        "appetite",
        "Used in appetite tests",
        "Sachets opened to test a child's appetite, taken off stock. Not part of the lines above.",
    ),
)


def _number(value: Decimal):
    """A JSON number: whole quantities as ints, the rest to three places."""
    return int(value) if value == value.to_integral_value() else float(round(value, 3))
