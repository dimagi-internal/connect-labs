"""What we believe each worker holds -- and every store above them (design 2026-09-28 §5).

`network_stock` calls `resupply.plan` once per point: fine for thirty stores,
wrong for six hundred workers. This computes the same figures for a whole
set of points in a fixed number of grouped queries -- one pass per figure,
grouped by point and unit -- and then does the arithmetic in Python through
the SAME rules (`resupply.rate_from`, `resupply.cover`, `soh._variance`), so
the Workers page and a single point's resupply plan cannot disagree.

Per point, in the item's single unit:

  issued        receipts, issues, transfers, distributions and returns in
  dispensed     consumption out, net of reversals
    unapproved  of which on visits whose status is not (yet) approved
    estimated   of which from protocol lines -- both shown, never netted
  on hand       the ledger balance
  reported      the latest on-hand count (never a reported receipt) and its day
  variance      reported MINUS the ledger ON THE COUNT DAY (the grouped form
                of soh.stock_on_hand's own figure; a parity test pins them) -- the sign
                `stock_on_hand` has always used, so a negative variance means
                fewer counted than the ledger says. Against the count day, not
                today: stock that arrived after the count is not a discrepancy
                in it.
  cover         months of stock, days to stock-out, status against the band
  unmatched receipts
                receipts the worker reported with no delivery recorded into
                the point around that day -- a fact on the row, never a rank

Stores carry the same figures summed over their subtree, except that what
came in is `received_from_outside` (each unit once), never a hop-summed
`issued`, which stays a per-point figure. Counts sum up the hierarchy;
cover is recomputed at each level from the subtree's own
consumption and never summed -- two workers at one month each are not a
store at two months.

A reversal (a rejected visit's consumption put back) is a `consumption` INTO
the worker. It restores the balance and cancels the dispensing; it is never
an arrival, so it is not in `issued` and never matches a reported receipt.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Exists, F, Min, OuterRef, Q, Sum

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import Movement, StockCount, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.stock.services import ledger, resupply, soh
from connect_labs.supply_chain.stock.services.network import _latest_counts
from connect_labs.supply_chain.stock.services.visit_reader import APPROVED_STATUSES, outcome_key
from connect_labs.supply_chain.values import Quantity

ZERO = Decimal("0")
# What arrives at a point: stock somebody sent or brought. Not `consumption`
# (a reversal is consumption back in) and not `adjustment` (a correction).
ISSUE_KINDS = ("receipt", "issue", "transfer", "distribution", "return")
# Statuses that count a worker as under their minimum.
BELOW = ("stockout", "below_min", "negative")
# A receipt a worker reports is matched to any delivery recorded into the same
# point from this many days before it to this many days after.
RECEIPT_MATCH_DAYS_BEFORE = 14
RECEIPT_MATCH_DAYS_AFTER = 3


def unit_of(item) -> str:
    """The item's single unit (sachets): every figure here is in it, at every level."""
    return ledger._pack_spec(item)[1] or ledger.pack_unit_of(item) or ""


@dataclass
class Belief:
    """One point's figures. Quantities are Quantity or Unconfirmed, in `unit`."""

    point: SupplyPoint
    unit: str
    issued: object
    dispensed: object
    unapproved: object
    estimated: object
    no_answer_visits: int
    on_hand: object
    reported: Quantity | None
    reported_on: date | None
    reported_kind: str | None
    # The ledger balance at the end of the count day, which `variance` is against.
    ledger_on_count_day: object
    # reported - ledger_on_count_day (the existing stock_on_hand sign). None with no count.
    variance: object
    days_since_checked: int | None
    amc: object
    amc_basis: str
    months_of_stock: object
    days_to_stockout: object
    status: str
    unmatched_receipts: list = field(default_factory=list)
    workers: int = 0
    workers_below_min: int = 0
    subtree: dict | None = None
    children: list = field(default_factory=list)


# Per-unit sums that add up the hierarchy as they are.
_SUMMED = ("in", "out", "issued", "dispensed", "unapproved", "estimated", "c_window")


def _raw():
    return {
        **{key: {} for key in _SUMMED},
        "in_after_count": {},
        "out_after_count": {},
        "issued_from": {},
        "r_window": {},
        "c_earliest": None,
        "r_earliest": None,
        "dispenses": False,
        "no_answer": 0,
    }


def _add(bucket, unit, amount):
    bucket[unit] = bucket.get(unit, ZERO) + (amount or ZERO)


def _earlier(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _counted_later(program_id, item, on_date, side):
    """True on a movement dated on or before some on-hand count at its point (as of `on_date`).

    Its negation picks out the movements after the latest count, which is how
    the ledger on the count day is had without a query per point.
    """
    counts = StockCount.objects.filter(
        program_id=program_id,
        supply_point_id=OuterRef(side),
        item=item,
        kind__in=records.ON_HAND_COUNT_KINDS,
        counted_on__gte=OuterRef("occurred_on"),
    )
    if on_date is not None:
        counts = counts.filter(counted_on__lte=on_date)
    return Exists(counts)


def _raw_by_point(program_id, item, ids, on_date, start, end) -> dict:  # noqa: C901
    """Every per-point sum, in five grouped queries whatever the number of points.

    Balances are as of `on_date` (all of the ledger when it is None, as
    `ledger.balance` reads it); rates and dispensing stop at `end`, as
    `resupply.average_monthly_consumption` does.
    """
    raw = {pid: _raw() for pid in ids}
    moves = Movement.objects.for_program(program_id).as_of(on_date).filter(item=item)
    by_end = Q(occurred_on__lte=end)
    in_window = Q(occurred_on__gte=start, occurred_on__lte=end)

    inbound = (
        moves.filter(to_supply_point_id__in=ids)
        .annotate(counted_later=_counted_later(program_id, item, on_date, "to_supply_point_id"))
        .values("to_supply_point_id", "from_supply_point_id", "kind", "quantity_unit", "counted_later")
        .annotate(total=Sum("quantity"))
    )
    for row in inbound:
        bucket, unit, total = raw[row["to_supply_point_id"]], row["quantity_unit"], row["total"]
        _add(bucket["in"], unit, total)
        if not row["counted_later"]:
            _add(bucket["in_after_count"], unit, total)
        if row["kind"] in ISSUE_KINDS:
            _add(bucket["issued"], unit, total)
            _add(bucket["issued_from"].setdefault(row["from_supply_point_id"], {}), unit, total)

    outbound = (
        moves.filter(from_supply_point_id__in=ids)
        .annotate(counted_later=_counted_later(program_id, item, on_date, "from_supply_point_id"))
        .values("from_supply_point_id", "kind", "quantity_unit", "counted_later")
        .annotate(total=Sum("quantity"), by_end=Count("id", filter=by_end))
    )
    for row in outbound:
        bucket, unit, total = raw[row["from_supply_point_id"]], row["quantity_unit"], row["total"]
        _add(bucket["out"], unit, total)
        if not row["counted_later"]:
            _add(bucket["out_after_count"], unit, total)
        # resupply.demand_basis: any consumption by the end, reversed or not.
        if row["kind"] == "consumption" and row["by_end"]:
            bucket["dispenses"] = True

    # resupply._demand(RELEASES): what left for another point.
    releases = (
        moves.filter(from_supply_point_id__in=ids, kind__in=resupply.RELEASE_KINDS, to_supply_point__isnull=False)
        .exclude(to_supply_point_id=F("from_supply_point_id"))
        .filter(by_end)
    )
    for row in releases.values("from_supply_point_id", "quantity_unit").annotate(
        window=Sum("quantity", filter=in_window), earliest=Min("occurred_on")
    ):
        bucket = raw[row["from_supply_point_id"]]
        _add(bucket["r_window"], row["quantity_unit"], row["window"])
        bucket["r_earliest"] = _earlier(bucket["r_earliest"], row["earliest"])

    # "Of which unapproved" is the visit's status as it stands now; the as-of
    # rewind (history/) is what shows the status a visit had on a past day.
    unapproved = Exists(
        WorkerVisit.objects.filter(program_id=program_id, visit_id=OuterRef("visit_id"))
        .exclude(visit_id="")
        .exclude(status__in=APPROVED_STATUSES)
    )
    standing = (
        moves.standing_consumption()
        .filter(from_supply_point_id__in=ids)
        .filter(by_end)
        .annotate(unapproved=unapproved)
    )
    for row in standing.values("from_supply_point_id", "quantity_unit", "estimated", "unapproved").annotate(
        total=Sum("quantity"), window=Sum("quantity", filter=in_window), earliest=Min("occurred_on")
    ):
        bucket, unit = raw[row["from_supply_point_id"]], row["quantity_unit"]
        _add(bucket["dispensed"], unit, row["total"])
        _add(bucket["c_window"], unit, row["window"])
        bucket["c_earliest"] = _earlier(bucket["c_earliest"], row["earliest"])
        if row["estimated"]:
            _add(bucket["estimated"], unit, row["total"])
        if row["unapproved"]:
            _add(bucket["unapproved"], unit, row["total"])

    unanswered = WorkerVisit.objects.filter(
        program_id=program_id, supply_point_id__in=ids, **{f"outcomes__{outcome_key(item.pk)}": "no_answer"}
    ).filter(visit_date__lte=end)
    for row in unanswered.values("supply_point_id").annotate(n=Count("id")):
        raw[row["supply_point_id"]]["no_answer"] = row["n"]
    return raw


def _unmatched_by_point(program_id, item, ids, on_date) -> dict:
    """{point id: [reported receipt with no recorded delivery around it]}, in at most two queries."""
    receipts = StockCount.objects.filter(
        program_id=program_id, supply_point_id__in=ids, item=item, kind="reported_receipt"
    )
    if on_date is not None:
        receipts = receipts.filter(counted_on__lte=on_date)
    receipts = list(receipts.order_by("supply_point_id", "counted_on", "pk"))
    if not receipts:
        return {}
    arrivals: dict[int, set] = {}
    for point_id, day in (
        Movement.objects.for_program(program_id)
        .as_of(on_date)
        .filter(to_supply_point_id__in={r.supply_point_id for r in receipts}, item=item, kind__in=ISSUE_KINDS)
        .order_by()
        .values_list("to_supply_point_id", "occurred_on")
        .distinct()
    ):
        arrivals.setdefault(point_id, set()).add(day)

    from connect_labs.supply_chain.operations import figure

    found: dict[int, list] = {}
    for receipt in receipts:
        earliest = receipt.counted_on - timedelta(days=RECEIPT_MATCH_DAYS_BEFORE)
        latest = receipt.counted_on + timedelta(days=RECEIPT_MATCH_DAYS_AFTER)
        if any(earliest <= day <= latest for day in arrivals.get(receipt.supply_point_id, ())):
            continue
        found.setdefault(receipt.supply_point_id, []).append(
            {
                "reported_on": receipt.counted_on.isoformat(),
                "quantity": figure(Quantity(receipt.quantity, receipt.quantity_unit)),
                "form_submission_id": receipt.form_submission_id,
                # No delivery into this point is recorded anywhere in this span.
                "recorded_from": earliest.isoformat(),
                "recorded_to": latest.isoformat(),
            }
        )
    return found


def _balance(inbound, outbound) -> dict:
    return {u: inbound.get(u, ZERO) - outbound.get(u, ZERO) for u in set(inbound) | set(outbound)}


def _rate(item, unit, total, earliest, end, window_days, basis):
    if resupply._is_durable(item):
        return resupply.DURABLE
    return resupply.rate_from(ledger.collapse(total, item, unit), earliest, end, window_days, basis)


def _belief(point, raw, count, unmatched, item, unit, end, window_days) -> Belief:
    on_hand = ledger.collapse(_balance(raw["in"], raw["out"]), item, unit)
    if raw["dispenses"]:
        basis, total, earliest = resupply.CONSUMPTION, raw["c_window"], raw["c_earliest"]
    else:
        basis, total, earliest = resupply.RELEASES, raw["r_window"], raw["r_earliest"]
    amc = _rate(item, unit, total, earliest, end, window_days, basis)
    plan = resupply.cover(on_hand, amc, basis, point, item=item, window_days=window_days)

    reported = ledger_on_count_day = variance = None
    if count is not None:
        reported = Quantity(count.quantity, count.quantity_unit)
        before = _balance(raw["in"], raw["out"])
        after = _balance(raw["in_after_count"], raw["out_after_count"])
        ledger_on_count_day = ledger.collapse(
            {u: before.get(u, ZERO) - after.get(u, ZERO) for u in set(before) | set(after)}, item, unit
        )
        variance = soh._variance(ledger_on_count_day, reported, item)
    return Belief(
        point=point,
        unit=unit,
        issued=ledger.collapse(raw["issued"], item, unit),
        dispensed=ledger.collapse(raw["dispensed"], item, unit),
        unapproved=ledger.collapse(raw["unapproved"], item, unit),
        estimated=ledger.collapse(raw["estimated"], item, unit),
        no_answer_visits=raw["no_answer"],
        on_hand=on_hand,
        reported=reported,
        reported_on=count.counted_on if count else None,
        reported_kind=count.kind if count else None,
        ledger_on_count_day=ledger_on_count_day,
        variance=variance,
        days_since_checked=(end - count.counted_on).days if count else None,
        amc=plan["amc"],
        amc_basis=basis,
        months_of_stock=plan["months_of_stock"],
        days_to_stockout=plan["days_to_stockout"],
        status=plan["status"],
        unmatched_receipts=unmatched,
    )


def _window(on_date, window_days):
    end = on_date or date.today()
    return end - timedelta(days=window_days), end


def _compute(program_id, item, points, on_date, window_days):
    start, end = _window(on_date, window_days)
    unit = unit_of(item)
    ids = [p.pk for p in points]
    raw = _raw_by_point(program_id, item, ids, on_date, start, end)
    counts = _latest_counts(program_id, points, item=item, on_date=on_date)
    unmatched = _unmatched_by_point(program_id, item, ids, on_date)
    beliefs = {
        p.pk: _belief(p, raw[p.pk], counts.get(p.pk), unmatched.get(p.pk, []), item, unit, end, window_days)
        for p in points
    }
    return raw, beliefs, unit, end


def beliefs_for(program_id, item, points, *, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS) -> dict:
    """{supply point id: Belief} for these points, in a fixed number of queries."""
    points = list(points)
    if not points:
        return {}
    return _compute(program_id, item, points, on_date, window_days)[1]


def worker_beliefs(program_id, item, *, opportunity_id=None, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS):
    """Every active worker point, by name. No other order: nothing here ranks workers (§22)."""
    points = SupplyPoint.objects.filter(program_id=program_id, status="active", kind="user_held")
    if opportunity_id is not None:
        points = points.filter(opportunity_id=opportunity_id)
    points = list(points.order_by("name", "pk"))
    found = beliefs_for(program_id, item, points, on_date=on_date, window_days=window_days)
    return [found[p.pk] for p in points]


def point_belief(program_id, point, item, *, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS) -> Belief:
    return beliefs_for(program_id, item, [point], on_date=on_date, window_days=window_days)[point.pk]


def unmatched_receipts(program_id, point, item, *, on_date=None) -> list[dict]:
    """Receipts the worker reported that no recorded delivery explains (design §3.4)."""
    return _unmatched_by_point(program_id, item, [point.pk], on_date).get(point.pk, [])


def _merge(total, raw):
    for key in _SUMMED:
        for unit, amount in raw[key].items():
            _add(total[key], unit, amount)
    for sender, units in raw["issued_from"].items():
        for unit, amount in units.items():
            _add(total["issued_from"].setdefault(sender, {}), unit, amount)
    total["c_earliest"] = _earlier(total["c_earliest"], raw["c_earliest"])
    total["no_answer"] += raw["no_answer"]


def _subtree_figures(point, total, members, item, unit, end, window_days) -> dict:
    """A store and everything below it.

    Counts are summed. What came in is `received_from_outside` -- arrivals
    from beyond the subtree, each sachet once. There is deliberately no
    subtree `issued`: summed hop by hop, a sachet sent central -> partner ->
    worker would be in it three times and read as intake. The rate is the
    subtree's CONSUMPTION -- releases between points inside it move stock
    around, they do not use it -- and cover is recomputed from it by the
    point's own band, never summed or averaged from the children.
    """
    on_hand = ledger.collapse(_balance(total["in"], total["out"]), item, unit)
    outside: dict = {}
    for sender, units in total["issued_from"].items():
        if sender not in members:
            for u, amount in units.items():
                _add(outside, u, amount)
    amc = _rate(item, unit, total["c_window"], total["c_earliest"], end, window_days, resupply.CONSUMPTION)
    plan = resupply.cover(on_hand, amc, resupply.CONSUMPTION, point, item=item, window_days=window_days)
    return {
        "on_hand": on_hand,
        "received_from_outside": ledger.collapse(outside, item, unit),
        "dispensed": ledger.collapse(total["dispensed"], item, unit),
        "unapproved": ledger.collapse(total["unapproved"], item, unit),
        "estimated": ledger.collapse(total["estimated"], item, unit),
        "no_answer_visits": total["no_answer"],
        "amc": plan["amc"],
        "amc_basis": resupply.CONSUMPTION,
        "months_of_stock": plan["months_of_stock"],
        "days_to_stockout": plan["days_to_stockout"],
        "status": plan["status"],
    }


def network_tree(program_id, item, *, on_date=None, window_days=resupply.DEFAULT_WINDOW_DAYS) -> list[Belief]:
    """The network from its top points down, each store carrying its subtree's figures.

    Every active point appears exactly once. A point whose parent is not
    active (or not here) is a root; a parent loop is broken at the first
    point met again and the rest of the loop shown under it, never dropped.
    """
    points = list(
        SupplyPoint.objects.filter(program_id=program_id, status="active")
        .exclude(kind="in_transit")
        .order_by("name", "pk")
    )
    if not points:
        return []
    raw, beliefs, unit, end = _compute(program_id, item, points, on_date, window_days)
    children: dict = {}
    for p in points:
        children.setdefault(p.parent_id, []).append(p)
    visited: set = set()

    def roll(p):
        """(summed raw figures, member ids) of p's subtree; fills p's Belief on the way."""
        visited.add(p.pk)
        node = beliefs[p.pk]
        total, members = _raw(), {p.pk}
        _merge(total, raw[p.pk])
        for child in children.get(p.pk, []):
            if child.pk in visited:
                continue
            below_total, below_members = roll(child)
            _merge(total, below_total)
            members |= below_members
            below = beliefs[child.pk]
            node.children.append(below)
            if child.kind == "user_held":
                node.workers += 1
                node.workers_below_min += int(below.status in BELOW)
            node.workers += below.workers
            node.workers_below_min += below.workers_below_min
        if node.children:
            node.subtree = _subtree_figures(p, total, members, item, unit, end, window_days)
        return total, members

    present = set(beliefs)
    roots = [p for p in points if p.parent_id not in present]
    for root in roots:
        roll(root)
    for p in points:  # only a parent loop leaves anything unvisited
        if p.pk not in visited:
            roots.append(p)
            roll(p)
    return [beliefs[r.pk] for r in roots]


def wire(b: Belief) -> dict:
    """A Belief as the operations return it. Quantities as {amount, unit}; ratios as strings."""
    from connect_labs.supply_chain.operations import figure
    from connect_labs.supply_chain.stock.operations import _plain

    def band(value):
        return str(value) if value is not None else None

    def maybe(value):
        return figure(value) if value is not None else None

    subtree = None
    if b.subtree:
        subtree = {
            key: (value if key in ("status", "amc_basis", "no_answer_visits") else _plain(value))
            for key, value in b.subtree.items()
        }
    return {
        "supply_point_id": b.point.pk,
        "name": b.point.name,
        "kind": b.point.kind,
        "connect_username": b.point.connect_username,
        "opportunity_id": b.point.opportunity_id,
        "parent_supply_point_id": b.point.parent_id,
        "unit": b.unit,
        "issued": figure(b.issued),
        "dispensed": figure(b.dispensed),
        "unapproved": figure(b.unapproved),
        "estimated": figure(b.estimated),
        "no_answer_visits": b.no_answer_visits,
        "on_hand": figure(b.on_hand),
        "reported": maybe(b.reported),
        "reported_on": b.reported_on.isoformat() if b.reported_on else None,
        "reported_kind": b.reported_kind,
        "ledger_on_count_day": maybe(b.ledger_on_count_day),
        "variance_reported_minus_ledger": maybe(b.variance),
        "days_since_checked": b.days_since_checked,
        "amc": figure(b.amc),
        "amc_basis": b.amc_basis,
        "months_of_stock": _plain(b.months_of_stock),
        "days_to_stockout": _plain(b.days_to_stockout),
        "status": b.status,
        "min_months_of_stock": band(b.point.min_months_of_stock),
        "max_months_of_stock": band(b.point.max_months_of_stock),
        "unmatched_receipts": b.unmatched_receipts,
        "workers": b.workers,
        "workers_below_min": b.workers_below_min,
        "subtree": subtree,
        "children": [wire(child) for child in b.children],
    }
