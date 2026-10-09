"""Stock forecast: the children in treatment, and those still to enrol, against the stock where it is.

Design: docs/superpowers/specs/2026-10-09-supply-stock-forecast-design.md.

For every field worker, every store and the programme, week by week from an
anchor day: what the children already in treatment are still owed, plus what
the children we expect to enrol will need, against what is on hand there --
and the day it runs dry if nothing else arrives. Read-only; it writes nothing.

    need(point, week) = committed to open cases + projected new enrolments

Where each input comes from:

- **Cases** are Connect beneficiaries (`WorkerVisit.entity_id`), read by the
  opportunity's dispensing rule's `cases` block (dispensing.validate_cases):
  enrolled by the enrolment answer on an enrolment form, still OPEN while the
  latest outcome is an open one (or none) and the child was seen within
  `lost_after_days`. A child followed up with no enrolment in the data was
  enrolled before it began -- an open carry-over case, owed its course less
  what the data shows it received.
- **Received** is the standing consumption the visit reader posted for each of
  the child's visits: a rejected visit's sachets were put back, so they are
  not received.
- **Course size**, in order: measured (the median received by cases that
  completed, once there are `K_MIN` of them), the commodity's protocol
  (`course_definition.base_units_per_course`), the caller's `course_size`.
  Each answer says which it is; with none, nothing is committed or projected.
- **Delivery profile**: what a child in week k of treatment is given, measured
  from cases in treatment for all of week k when `K_MIN` of them were, else the
  protocol's daily figure, else the last week measured, else a tenth of the
  course a week. A course is laid along it, never past its size.
- **Enrolment**: each worker's enrolments over the last `ENROL_WEEKS` seven-day
  windows inside the rule's life; fewer than `MIN_ENROL_WEEKS` such windows is
  "not enough history" (None), never zero.
- **On hand** is `belief.network_tree` at the anchor -- the same figures as
  worker_stock and the Network page, so the three agree by construction.

The anchor is the day asked for (today by default), moved back to the last
visit when the visits stop earlier: a forecast from stale visits as if they
were today would owe every child a week too little. The result says so in
`data_to`.

Counts sum up the tree; rates and cover are never summed. A store's need is
what the workers below it cannot cover themselves; the programme's is what
its top stores cannot, against orders still to arrive by their expected day
(an overdue or undated order is listed and not counted).

Nothing here is specific to one commodity: the caller's `course_size` (the
template's config) is the last fallback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Sum

from connect_labs.supply_chain.models import DispensingRule, Movement, WorkerVisit
from connect_labs.supply_chain.stock.services import belief, ledger
from connect_labs.supply_chain.stock.services.dispensing import form_matches
from connect_labs.supply_chain.stock.services.visit_reader import REVERSING_STATUSES
from connect_labs.supply_chain.values import Quantity

ZERO = Decimal(0)
# Fewest cases a measured figure (a week of the profile, the course size) rests on.
K_MIN = 5
ENROL_WEEKS = 3
MIN_ENROL_WEEKS = 2
HISTORY_WEEKS = 8
MAX_HORIZON_WEEKS = 26
DEFAULT_HORIZON_WEEKS = 8

MEASURED, PROTOCOL, DEFAULT, STEADY, UNKNOWN = "measured", "protocol", "default", "steady", "unknown"


@dataclass
class Case:
    """One child, as the visits show them up to the anchor."""

    key: tuple
    opportunity_id: int
    supply_point_id: int
    enrolled_on: date
    enrol_point_id: int | None
    carry_over: bool
    last_seen: date
    outcome: str | None
    received: Decimal = ZERO
    # (day, sachets) of each visit, for the delivery profile.
    visits: list = field(default_factory=list)
    status: str = "open"  # open | exited | lost


def _wire(value: Decimal | None) -> str | None:
    if value is None:
        return None
    rounded = value.quantize(Decimal("0.1"))
    return format(rounded.to_integral_value(), "f") if rounded == rounded.to_integral_value() else str(rounded)


def _amount(value) -> Decimal | None:
    return value.amount if isinstance(value, Quantity) else None


def _flatten(roots) -> list:
    out, stack = [], list(roots)
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(node.children)
    return out


# ---- reading the cases ---------------------------------------------------------


def _sachets_by_visit(program_id, item, visit_ids, unit, on_date) -> dict:
    """{visit_id: sachets} the visit gave out, net of reversals, in the item's unit."""
    found: dict[str, dict] = {}
    rows = (
        Movement.objects.for_program(program_id)
        .as_of(on_date)
        .standing_consumption()
        .filter(item=item, visit_id__in=visit_ids)
        .values("visit_id", "quantity_unit")
        .annotate(total=Sum("quantity"))
    )
    for row in rows:
        units = found.setdefault(row["visit_id"], {})
        units[row["quantity_unit"]] = units.get(row["quantity_unit"], ZERO) + row["total"]
    out = {}
    for visit_id, units in found.items():
        amount = _amount(ledger.collapse(units, item, unit))
        if amount is not None:
            out[visit_id] = amount
    return out


def _enrols(rule_cases, visit) -> bool:
    enrol = rule_cases["enrol"]
    form_json = {"form": {"@name": visit["form_name"], "@xmlns": visit["form_xmlns"]}}
    if not form_matches(enrol["forms"], form_json):
        return False
    return str((visit["answers"] or {}).get(enrol["path"], "")).strip() == enrol["equals"]


def read_cases(program_id, item, rules, anchor, unit) -> list[Case]:
    """Every case on these rules' opportunities as of the anchor, from the visits the reader kept."""
    by_opp = {rule.opportunity_id: rule.cases for rule in rules}
    visits = list(
        WorkerVisit.objects.filter(program_id=program_id, opportunity_id__in=list(by_opp), visit_date__lte=anchor)
        .exclude(entity_id="")
        .exclude(status__in=REVERSING_STATUSES)
        .order_by("visit_date", "id")
        .values(
            "visit_id",
            "entity_id",
            "opportunity_id",
            "supply_point_id",
            "visit_date",
            "answers",
            "form_name",
            "form_xmlns",
        )
    )
    sachets = _sachets_by_visit(program_id, item, [v["visit_id"] for v in visits], unit, anchor)
    grouped: dict[tuple, list] = {}
    for v in visits:
        grouped.setdefault((v["opportunity_id"], v["entity_id"]), []).append(v)

    cases = []
    for key, seen in grouped.items():
        rule_cases = by_opp[key[0]]
        outcome_path = rule_cases["outcome"]["path"]
        enrolling = next((v for v in seen if _enrols(rule_cases, v)), None)
        if enrolling is None:
            # Not enrolled in the data: a case only if it was followed up (a visit
            # off the enrolment forms with an outcome) -- a child screened and not
            # enrolled is not one.
            enrol_forms = rule_cases["enrol"]["forms"]
            followed = [
                v
                for v in seen
                if (v["answers"] or {}).get(outcome_path) not in (None, "")
                and not form_matches(enrol_forms, {"form": {"@name": v["form_name"], "@xmlns": v["form_xmlns"]}})
            ]
            if not followed:
                continue
            start, carry_over, enrol_point = followed[0]["visit_date"], True, None
            seen = [v for v in seen if v["visit_date"] >= start]
        else:
            start, carry_over, enrol_point = enrolling["visit_date"], False, enrolling["supply_point_id"]
            seen = [v for v in seen if v["visit_date"] >= start]
        outcomes = [v["answers"].get(outcome_path) for v in seen if (v["answers"] or {}).get(outcome_path)]
        case = Case(
            key=key,
            opportunity_id=key[0],
            supply_point_id=seen[-1]["supply_point_id"],
            enrolled_on=start,
            enrol_point_id=enrol_point,
            carry_over=carry_over,
            last_seen=seen[-1]["visit_date"],
            outcome=str(outcomes[-1]).strip() if outcomes else None,
        )
        for v in seen:
            given = sachets.get(v["visit_id"], ZERO)
            case.received += given
            case.visits.append((v["visit_date"], given))
        if case.outcome in rule_cases["outcome"]["exit"]:
            case.status = "exited"
        elif (anchor - case.last_seen).days > rule_cases["lost_after_days"]:
            case.status = "lost"
        cases.append(case)
    return cases


# ---- course and profile --------------------------------------------------------


def course_size(cases, rules, item, fallback) -> dict:
    """{size, basis, completed_cases}: measured, else protocol, else the caller's, else unknown."""
    complete = {rule.opportunity_id: set(rule.cases["outcome"].get("complete") or []) for rule in rules}
    finished = [
        c.received
        for c in cases
        if not c.carry_over and c.status == "exited" and c.outcome in complete.get(c.opportunity_id, ())
    ]
    if len(finished) >= K_MIN:
        return {"size": Decimal(median(finished)), "basis": MEASURED, "completed_cases": len(finished)}
    protocol = (item.commodity.course_definition or {}).get("base_units_per_course") if item.commodity_id else None
    if protocol:
        return {"size": Decimal(str(protocol)), "basis": PROTOCOL, "completed_cases": len(finished)}
    if fallback:
        return {"size": Decimal(str(fallback)), "basis": DEFAULT, "completed_cases": len(finished)}
    return {"size": None, "basis": UNKNOWN, "completed_cases": len(finished)}


@dataclass
class Profile:
    """Sachets a child is given in each week of treatment."""

    measured: dict  # {week: (sachets, cases)}
    fallback: Decimal | None
    fallback_basis: str

    def week(self, k: int) -> Decimal | None:
        if k in self.measured:
            return self.measured[k][0]
        return self.fallback

    def basis(self, k: int) -> str:
        return MEASURED if k in self.measured else self.fallback_basis


def delivery_profile(cases, anchor, course, per_day_protocol) -> Profile:
    """Measured week by week from cases seen in treatment for the whole week; see the module docstring."""
    sums: dict[int, Decimal] = {}
    counts: dict[int, int] = {}
    for case in cases:
        if case.carry_over:
            continue  # its week of treatment is unknown
        observed_weeks = ((anchor - case.enrolled_on).days + 1) // 7
        for k in range(observed_weeks):
            if case.last_seen < case.enrolled_on + timedelta(days=7 * k):
                break  # out of treatment by then: not a zero-sachet week of it
            counts[k] = counts.get(k, 0) + 1
            sums[k] = sums.get(k, ZERO) + sum(
                (given for day, given in case.visits if (day - case.enrolled_on).days // 7 == k), ZERO
            )
    measured = {k: (sums[k] / counts[k], counts[k]) for k in counts if counts[k] >= K_MIN}
    if per_day_protocol:
        return Profile(measured, Decimal(str(per_day_protocol)) * 7, PROTOCOL)
    later = [k for k in measured if k >= 1]
    if later:
        return Profile(measured, measured[max(later)][0], STEADY)
    if course:
        return Profile(measured, course / 10, DEFAULT)
    return Profile(measured, None, UNKNOWN)


def _lay(profile, course, left, first_week, weeks) -> list[Decimal]:
    """`left` sachets of a course laid along the profile from treatment week `first_week`, one figure a week."""
    out = []
    for i in range(weeks):
        per = profile.week(first_week + i)
        if per is None or left <= 0:
            out.append(ZERO)
            continue
        given = min(per, left)
        out.append(given)
        left -= given
    return out


# ---- per worker ----------------------------------------------------------------


def _windows(anchor):
    """The last ENROL_WEEKS seven-day windows ending at the anchor, newest first: (first day, last day)."""
    return [(anchor - timedelta(days=7 * (j + 1) - 1), anchor - timedelta(days=7 * j)) for j in range(ENROL_WEEKS)]


def enrolment_rate(cases_here, active_from, anchor) -> tuple[Decimal | None, int]:
    """(children a week, windows it rests on); None with too little history."""
    usable = [(lo, hi) for lo, hi in _windows(anchor) if active_from is None or lo >= active_from]
    if len(usable) < MIN_ENROL_WEEKS:
        return None, len(usable)
    enrolled = sum(1 for c in cases_here for lo, hi in usable if lo <= c.enrolled_on <= hi)
    return Decimal(enrolled) / len(usable), len(usable)


def runs_dry(on_hand: Decimal | None, needs: list[Decimal], anchor: date) -> date | None:
    """The first day cumulative need passes what is on hand, linear inside a week; None inside the horizon."""
    if on_hand is None:
        return None
    if on_hand <= 0:
        return anchor
    total = ZERO
    for i, need in enumerate(needs):
        if need > 0 and total + need > on_hand:
            days = math.ceil(float((on_hand - total) / need) * 7)
            return anchor + timedelta(days=7 * i + max(days, 1))
        total += need
    return None


def gap_opens(wanted: list[Decimal], available: list[Decimal], anchor: date) -> date | None:
    """The first day cumulative `wanted` passes cumulative `available`; linear inside the week it happens.

    The same arithmetic as `runs_dry`, with what is available allowed to grow
    as orders arrive.
    """
    before = ZERO
    for i, (want, have) in enumerate(zip(wanted, available)):
        if want > have:
            step = want - before
            cover = max(have - before, ZERO)
            days = math.ceil(float(cover / step) * 7) if step > 0 else 7
            return anchor + timedelta(days=7 * i + max(days, 1))
        before = want
    return None


def _cumulative(values):
    out, running = [], ZERO
    for value in values:
        running += value
        out.append(running)
    return out


# ---- the forecast --------------------------------------------------------------


def forecast(
    program_id,
    item,
    *,
    opportunity_ids=None,
    on_date: date | None = None,
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
    scenario=1,
    course_size_fallback=None,
) -> dict:
    """The forecast, as the `stock_forecast` operation returns it. See the module docstring."""
    from connect_labs.supply_chain.stock.services.network import _expected_inbound

    horizon_weeks = max(1, min(int(horizon_weeks), MAX_HORIZON_WEEKS))
    scenario = Decimal(str(scenario))
    unit = belief.unit_of(item)
    requested = on_date or date.today()
    scope = {int(o) for o in opportunity_ids} if opportunity_ids else None

    rules = list(DispensingRule.objects.filter(program_id=program_id, item=item, status="active").exclude(cases={}))
    if scope is not None:
        rules = [rule for rule in rules if rule.opportunity_id in scope]
    last_visit = (
        WorkerVisit.objects.filter(
            program_id=program_id, opportunity_id__in=[r.opportunity_id for r in rules], visit_date__lte=requested
        )
        .exclude(entity_id="")
        .order_by("-visit_date")
        .values_list("visit_date", flat=True)
        .first()
        if rules
        else None
    )
    anchor = min(requested, last_visit) if last_visit else requested
    # The ledger read on the same day the cases are: today reads as worker_stock does with no date.
    ledger_day = None if on_date is None and anchor == requested else anchor
    roots = belief.network_tree(program_id, item, on_date=ledger_day)
    nodes = _flatten(roots)

    cases = read_cases(program_id, item, rules, anchor, unit) if rules else []
    course = course_size(cases, rules, item, course_size_fallback)
    size = course["size"]
    per_day = (item.commodity.course_definition or {}).get("base_units_per_day") if item.commodity_id else None
    profile = delivery_profile(cases, anchor, size, per_day)
    weeks = [
        {
            "start": (anchor + timedelta(days=7 * i + 1)).isoformat(),
            "end": (anchor + timedelta(days=7 * (i + 1))).isoformat(),
        }
        for i in range(horizon_weeks)
    ]
    rule_by_opp = {rule.opportunity_id: rule for rule in rules}

    # Each cohort of new children follows the same profile: one child's weekly
    # need from their week of enrolment, cut at the course.
    one_child = _lay(profile, size, size, 0, horizon_weeks) if size is not None else None

    open_by_point: dict[int, list[Case]] = {}
    enrolled_by_point: dict[int, list[Case]] = {}
    for case in cases:
        if case.status == "open":
            open_by_point.setdefault(case.supply_point_id, []).append(case)
        if not case.carry_over and case.enrol_point_id is not None:
            enrolled_by_point.setdefault(case.enrol_point_id, []).append(case)

    history = _history(program_id, item, unit, [n.point.pk for n in nodes if n.point.kind == "user_held"], anchor)

    workers = []
    worker_figures: dict[int, dict] = {}
    for node in nodes:
        point = node.point
        if point.kind != "user_held":
            continue
        if scope is not None and point.opportunity_id not in scope:
            continue
        on_hand = _amount(node.on_hand)
        rule = rule_by_opp.get(point.opportunity_id)
        committed = [ZERO] * horizon_weeks
        new = [ZERO] * horizon_weeks
        owed = ZERO
        rate, rate_weeks = None, 0
        open_here = open_by_point.get(point.pk, [])
        if rule is not None and size is not None:
            basis = "cases"
            for case in open_here:
                left = max(size - case.received, ZERO)
                owed += left
                first = (anchor + timedelta(days=1) - case.enrolled_on).days // 7
                for i, given in enumerate(_lay(profile, size, left, first, horizon_weeks)):
                    committed[i] += given
            rate, rate_weeks = enrolment_rate(enrolled_by_point.get(point.pk, []), rule.active_from, anchor)
            if rate is not None and one_child is not None:
                cohort = rate * scenario
                for start in range(horizon_weeks):
                    for k in range(horizon_weeks - start):
                        new[start + k] += cohort * one_child[k]
        else:
            basis = "pace"
            pace = _amount(node.amc)
            weekly = pace / 30 * 7 if pace is not None else ZERO
            committed = [weekly] * horizon_weeks
        need = [c + n for c, n in zip(committed, new)]
        cumulative = _cumulative(need)
        shortfall = [max(ZERO, total - on_hand) if on_hand is not None else total for total in cumulative]
        worker_figures[point.pk] = {
            "need": need,
            "committed": committed,
            "new": new,
            "shortfall": shortfall,
            "rate": rate,
            "owed": owed,
        }
        workers.append(
            {
                "supply_point_id": point.pk,
                "name": point.name,
                "connect_username": point.connect_username,
                "opportunity_id": point.opportunity_id,
                "parent_supply_point_id": point.parent_id,
                "basis": basis,
                "on_hand": belief.wire(node)["on_hand"],
                "dispensed": belief.wire(node)["dispensed"],
                "open_cases": len(open_here),
                "carry_over_cases": sum(1 for c in open_here if c.carry_over),
                "owed": _wire(owed) if basis == "cases" else None,
                "enrolled_per_week": _wire(rate),
                "enrolment_weeks": rate_weeks,
                "weeks": [
                    {"committed": _wire(c), "new": _wire(n), "need": _wire(c + n)} for c, n in zip(committed, new)
                ],
                "need_total": _wire(cumulative[-1]),
                "runs_dry_on": _iso(runs_dry(on_hand, need, anchor)),
                "shortfall_by_end": _wire(shortfall[-1]),
                "given_out": [_wire(v) for v in history["by_point"].get(point.pk, [ZERO] * HISTORY_WEEKS)],
            }
        )
    workers.sort(key=lambda w: (w["runs_dry_on"] or "9999", w["name"]))

    stores, demand_of = _stores(nodes, worker_figures, horizon_weeks, anchor)
    programme = _programme(
        program_id, item, unit, roots, nodes, demand_of, worker_figures, horizon_weeks, anchor, _expected_inbound
    )
    programme["open_cases"] = sum(w["open_cases"] for w in workers)
    # Counts summed from the unrounded figures, never from the rounded ones on the wire.
    programme["owed"] = _wire(sum((f["owed"] for f in worker_figures.values()), ZERO)) if rules else None
    rates = [f["rate"] for f in worker_figures.values() if f["rate"] is not None]
    programme["enrolled_per_week"] = _wire(sum(rates, ZERO)) if rates else None

    return {
        "item_id": item.pk,
        "item_name": item.name,
        "unit": unit,
        "as_of": on_date.isoformat() if on_date else None,
        "anchor": anchor.isoformat(),
        "data_to": last_visit.isoformat() if last_visit else None,
        "horizon_weeks": horizon_weeks,
        "scenario": _wire(scenario),
        "cases_configured": bool(rules),
        "weeks": weeks,
        "basis": {
            "course": {**course, "size": _wire(size)},
            "profile": [
                {
                    "week": k,
                    "sachets": _wire(profile.week(k)),
                    "basis": profile.basis(k),
                    "cases": profile.measured.get(k, (None, 0))[1],
                }
                for k in range(horizon_weeks)
            ],
            "enrolment_weeks": ENROL_WEEKS,
            "lost_after_days": sorted({rule.cases["lost_after_days"] for rule in rules}),
            "scenario": _wire(scenario),
        },
        "history": [
            {"start": lo.isoformat(), "end": hi.isoformat(), "given_out": _wire(total)}
            for (lo, hi), total in zip(history["windows"], history["total"])
        ],
        "programme": programme,
        "cohorts": _cohorts(cases, size),
        "workers": workers,
        "stores": stores,
    }


def _iso(day):
    return day.isoformat() if day else None


def _history(program_id, item, unit, worker_ids, anchor) -> dict:
    """Given out at visits in each of the HISTORY_WEEKS seven-day windows before the anchor, oldest first."""
    windows = [
        (anchor - timedelta(days=7 * (j + 1) - 1), anchor - timedelta(days=7 * j)) for j in range(HISTORY_WEEKS)
    ][::-1]
    by_point: dict[int, list[Decimal]] = {}
    total = [ZERO] * HISTORY_WEEKS
    rows = (
        Movement.objects.for_program(program_id)
        .standing_consumption()
        .filter(
            item=item, from_supply_point_id__in=worker_ids, occurred_on__gte=windows[0][0], occurred_on__lte=anchor
        )
        .values("from_supply_point_id", "occurred_on", "quantity_unit")
        .annotate(sum=Sum("quantity"))
    )
    for row in rows:
        amount = _amount(ledger.collapse({row["quantity_unit"]: row["sum"]}, item, unit))
        if amount is None:
            continue
        for i, (lo, hi) in enumerate(windows):
            if lo <= row["occurred_on"] <= hi:
                by_point.setdefault(row["from_supply_point_id"], [ZERO] * HISTORY_WEEKS)[i] += amount
                total[i] += amount
                break
    return {"windows": windows, "by_point": by_point, "total": total}


def _stores(nodes, worker_figures, horizon_weeks, anchor):
    """Each store: what the workers (and stores) below it cannot cover, against its own stock."""
    children: dict[int, list] = {}
    for node in nodes:
        children.setdefault(node.point.parent_id, []).append(node)
    demand_of: dict[int, list[Decimal]] = {}
    need_of: dict[int, list[Decimal]] = {}

    def roll(node):
        """(cumulative demand it passes up, weekly need of the workers below), filling `demand_of`."""
        point = node.point
        if point.kind == "user_held":
            figures = worker_figures.get(point.pk)
            if figures is None:
                return [ZERO] * horizon_weeks, [ZERO] * horizon_weeks
            return figures["shortfall"], figures["need"]
        demand, need = [ZERO] * horizon_weeks, [ZERO] * horizon_weeks
        for child in children.get(point.pk, []):
            passed, child_need = roll(child)
            demand = [a + b for a, b in zip(demand, passed)]
            need = [a + b for a, b in zip(need, child_need)]
        demand_of[point.pk], need_of[point.pk] = demand, need
        own = _amount(node.on_hand) or ZERO
        return [max(ZERO, total - own) for total in demand], need

    for node in nodes:
        if node.point.parent_id is None or node.point.parent_id not in {n.point.pk for n in nodes}:
            roll(node)

    stores = []
    for node in nodes:
        point = node.point
        if point.kind == "user_held" or point.pk not in demand_of:
            continue
        own = _amount(node.on_hand)
        demand = demand_of[point.pk]
        increments = [b - a for a, b in zip([ZERO] + demand[:-1], demand)]
        workers_below = node.workers
        if not workers_below and not any(demand):
            continue
        stores.append(
            {
                "supply_point_id": point.pk,
                "name": point.name,
                "kind": point.kind,
                "parent_supply_point_id": point.parent_id,
                "own_on_hand": belief.wire(node)["on_hand"],
                "subtree_on_hand": (belief.wire(node)["subtree"] or {}).get("on_hand"),
                "workers": workers_below,
                "need": [_wire(v) for v in need_of[point.pk]],
                "demand_from_below": [_wire(v) for v in demand],
                "runs_dry_on": _iso(runs_dry(own, increments, anchor)) if any(demand) else None,
                "shortfall_by_end": _wire(max(ZERO, demand[-1] - (own or ZERO))),
            }
        )
    return stores, demand_of


def _programme(
    program_id, item, unit, roots, nodes, demand_of, worker_figures, horizon_weeks, anchor, expected_inbound
):
    """The whole network: what its top points cannot cover, against orders by their expected day."""
    # Wanted from the top points (cumulative, before their own stock), against
    # their own stock plus what arrives -- so a programme with one store runs dry
    # the day that store does.
    wanted, held = [ZERO] * horizon_weeks, ZERO
    for root in roots:
        point = root.point
        if point.kind == "user_held":
            figures = worker_figures.get(point.pk)
            if figures is None:
                continue
            passed = _cumulative(figures["need"])
        else:
            passed = demand_of.get(point.pk, [ZERO] * horizon_weeks)
        wanted = [a + b for a, b in zip(wanted, passed)]
        held += max(_amount(root.on_hand) or ZERO, ZERO)

    end = anchor + timedelta(days=7 * horizon_weeks)
    arriving = [ZERO] * horizon_weeks
    inbound = []
    for point_id, orders in expected_inbound(program_id, [n.point for n in nodes], item=item, as_of=anchor).items():
        for order in orders:
            outstanding = order["outstanding"]
            amount = (
                _amount(ledger.convert(outstanding.amount, outstanding.unit, unit, item))
                if isinstance(outstanding, Quantity)
                else None
            )
            expected = order["expected_on"]
            counted = amount is not None and expected is not None and anchor < expected <= end
            if counted:
                arriving[((expected - anchor).days - 1) // 7] += amount
            inbound.append(
                {
                    "contract_id": order["contract_id"],
                    "reference": order["reference"],
                    "supply_point_id": point_id,
                    "quantity": _wire(amount),
                    "expected_on": _iso(expected),
                    "counted": counted,
                    "overdue": bool(order["overdue"]),
                }
            )
    available = [held + total for total in _cumulative(arriving)]
    gap = [max(ZERO, w - a) for w, a in zip(wanted, available)]
    committed = [sum((f["committed"][i] for f in worker_figures.values()), ZERO) for i in range(horizon_weeks)]
    new = [sum((f["new"][i] for f in worker_figures.values()), ZERO) for i in range(horizon_weeks)]
    # Every point's own stock, counted once; workers outside the forecast's opportunities left out.
    on_hand = sum(
        (_amount(n.on_hand) or ZERO for n in nodes if n.point.kind != "user_held" or n.point.pk in worker_figures),
        ZERO,
    )
    return {
        "on_hand": _wire(on_hand),
        "weeks": [{"committed": _wire(c), "new": _wire(n), "need": _wire(c + n)} for c, n in zip(committed, new)],
        "need_total": _wire(sum(committed, ZERO) + sum(new, ZERO)),
        "wanted_from_top": [_wire(v) for v in wanted],
        "top_on_hand": _wire(held),
        "inbound": inbound,
        "inbound_by_week": [_wire(v) for v in arriving],
        "runs_dry_on": _iso(gap_opens(wanted, available, anchor)),
        "shortfall_by_end": _wire(gap[-1]),
    }


def _cohorts(cases, size) -> list[dict]:
    """Open cases by the Monday of the week they enrolled: how many, and what they are still owed."""
    out: dict[date, dict] = {}
    for case in cases:
        if case.status != "open":
            continue
        monday = case.enrolled_on - timedelta(days=case.enrolled_on.weekday())
        row = out.setdefault(monday, {"children": 0, "carry_over": 0, "owed": ZERO})
        row["children"] += 1
        row["carry_over"] += int(case.carry_over)
        if size is not None:
            row["owed"] += max(size - case.received, ZERO)
    return [
        {
            "week_of": monday.isoformat(),
            "children": row["children"],
            "carry_over": row["carry_over"],
            "owed": _wire(row["owed"]) if size is not None else None,
        }
        for monday, row in sorted(out.items())
    ]
