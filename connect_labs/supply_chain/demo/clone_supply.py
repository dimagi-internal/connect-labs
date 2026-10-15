"""The RUTF supply chain behind a synthetic clone of the real RUTF opportunity.

A clone (`synthetic_clone_opp`) of the real RUTF deliver opportunity gives
visits shaped like the real ones -- the same workers' caseloads, the same form
paths, the RUTF each visit says it gave out -- with nothing real copied. What
the programme has not told us is how much RUTF each worker was GIVEN to give
out. This seeder supplies that, and says on every record that it is invented:

- the catalogue item, a central store and a partner store, and opening stock;
- the RUTF dispensing rule, on the released app's own paths
  (`PATHS` below, design §9), so visits post consumption;
- a worker supply point for every worker who submitted a visit;
- every two weeks, the last a week before the seed ran, a distribution topping
  each active worker up to a few weeks' cover at the pace their visits gave RUTF
  out over the last two weeks (the pace the Workers page measures); a fixed few
  are sent only the fortnight ahead, so they read below minimum
  (`plan_distributions`). On the seed day, whatever day it is, most workers hold
  24-34 days and the few under 15. The figures are INVENTED, and each
  distribution's note says so;
- the visit reader, run week by week to today, so the ledger, the as-of
  history and the belief views walk forward exactly as a live programme's
  would;
- the Supply tabs and home pages the demo is seen through (demo/clone_pages.py),
  on every run.

Synthetic only: every write goes through `call_operation`, whose guards refuse
a programme or opportunity that is not a registered labs-only one.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal

from django.utils import timezone

# The released RUTF deliver app's form names (`@name`) and the Screening's identifier
# (`@xmlns`): a form's name can be retranslated, its xmlns cannot.
FORM_SCREENING, FORM_VISIT = "Screening", "Visit Form"
XMLNS = {
    FORM_SCREENING: "http://openrosa.org/formdesigner/92026AF7-291B-4E25-A60B-82486FD0C799",
    FORM_VISIT: "http://openrosa.org/formdesigner/991BB731-417A-46D2-B1F5-1E4CD65C8D89",
}
# form_json paths, copied verbatim from the released app's definition (design §9).
PATHS = {
    # Screening: the deduction is already the total (the ration, or 1 for an appetite test alone).
    "rutf_screening_total": "form.screening_outcome.rutf_stock_deduction",
    # Visit Form: the ration plus the appetite test's sachet (1 only when no ration was given).
    "rutf_visit": "form.rutf_dispensing.rutf_sachets_dispensed",
    "appetite_visit": "form.var.appetite_test_stock_deduction",
    # The worker's own stock, when the app records it.
    "balance": "form.var.new_stock_balance",
    "received": "form.current_stock.sachets_received",
    "received_on": "form.current_stock.date_received",
    "remaining": "form.stock_balance.sachets_remaining",
}

# Deliveries come every CADENCE_DAYS, and the last one before the seed ran was
# LAST_DELIVERY_DAYS_AGO days before it: half-way through a cycle on whatever day it
# is seeded, so the demo never opens on a delivery morning (when everyone was just
# topped up) or the evening before one (when everyone is at their lowest).
CADENCE_DAYS = 14
LAST_DELIVERY_DAYS_AGO = 7
# Months of cover each store holds once the issues are out: the middle of its band
# (central 2-6, partner 1-3), so neither reads "below min" for want of invented stock.
CENTRAL_MONTHS, PARTNER_MONTHS = Decimal(4), Decimal(2)
SLUG, SKU = "rutf", "rutf-150"
NAME = "Ready-to-use therapeutic food (RUTF), 92 g sachet"
# Every delivery this module invents starts with this; the seeded clones are found by it
# (deliveries recorded before the sizing below changed say "programme", which it also matches).
INVENTED_PREFIX = "Invented for the demo: the program"
INVENTED = (
    "Invented for the demo: the program has not yet said how much RUTF each worker received. "
    "Sized to top the worker up to a few weeks' cover at the pace their last two weeks of visits gave it out."
)
# A worker's band is half a month to a month and a half (15-45 days) of cover at their
# own pace over their last 14 days -- the pace the Workers page measures.
#
# Most workers are topped up to TARGET_DAYS of cover at that pace each delivery; on the
# day the demo is seeded, a week after the last delivery, each holds TODAY_DAYS -- inside
# their band, a week and more clear of its minimum. About one in SHORT_EVERY is SHORT:
# sent only what the fortnight to the next delivery will take, so they sit under their
# minimum all fortnight and are nearly out when the next one comes, never below zero.
# Those few are what the headline, the alerts and the blockers have to show.
TARGET_DAYS, TODAY_DAYS = (32, 42), (24, 34)
SHORT_EVERY = 6
PACE_DAYS = 14  # the window the Workers page measures a worker's pace over (resupply.WORKER_WINDOW_DAYS)


def rule_lines() -> list[dict]:
    """RUTF as the released app records it: the Screening total, or the Visit Form ration plus appetite test."""

    def stated(key, form):
        return {"kind": "stated", "paths": [PATHS[key]], "unit": "sachet", "forms": [form]}

    return [
        stated("rutf_screening_total", FORM_SCREENING),
        stated("rutf_visit", FORM_VISIT),
        stated("appetite_visit", FORM_VISIT),
    ]


def rule_cases() -> dict:
    """A child's case as the released app records it, for the stock forecast.

    Enrolled by the Screening's own answer (the Visit Form repeats it, so the
    form filter matters); the case state's outcome after that. The clone's
    forms carry their name but no xmlns, so both are named.
    """
    return {
        "enrol": {
            "forms": [FORM_SCREENING, XMLNS[FORM_SCREENING]],
            "path": "form.screening_outcome.rutf_enrollment",
            "equals": "yes",
        },
        "outcome": {
            "path": "form.case_state.outcome_value",
            "open": ["enrolled"],
            "exit": ["recovered", "deceased", "non_response", "lost_for_follow_up", "visit_referred"],
            "complete": ["recovered"],
        },
        "lost_after_days": 21,
    }


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _day(visit) -> date | None:
    raw = str(visit.get("visit_date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _round_up(quantity: Decimal, step: int = 10) -> Decimal:
    return (quantity / step).to_integral_value(rounding=ROUND_CEILING) * step


COUNTED = (
    "Invented for the demo: the clone's visits carry no stock balance a worker could report "
    "(the app's running balance is a calculated field, which a clone does not reproduce). "
    "The ledger's own balance that Sunday; for a few workers, well under it."
)


def count_offsets(usernames, *, seed: int = 13) -> dict:
    """{username: sachets} each worker's weekly count is off from the ledger by.

    Most workers count exactly what the ledger holds; about one worker in six
    counts well below it -- the variance a real network shows (sachets given out
    without being recorded, or lost). Every non-zero offset raises a
    stock_variance check, which the map draws as a blocker, so small noise on
    every worker would bury the gaps that matter. Seeded, so a top-up and a
    reseed agree.
    """
    rng = random.Random(seed)
    offsets = {}
    for username in sorted(usernames):
        offsets[username] = -rng.randint(5, 15) if rng.random() < 1 / 6 else 0
    return offsets


def record_counts(op, *, program_id: int, opportunity_id: int, item, workers, day: date) -> int:
    """Each worker's invented self-reported count on `day`, from the ledger; returns how many."""
    from connect_labs.supply_chain.stock.services import ledger
    from connect_labs.supply_chain.values import Quantity

    if _reports_arrive(program_id):
        return 0  # the visits carry real balances, and the reader records those
    offsets = count_offsets(w.connect_username for w in workers)
    recorded = 0
    for worker in workers:
        held = ledger.balance(program_id, worker, item=item, unit="sachet", on_date=day)
        if not isinstance(held, Quantity):
            continue
        counted = max(Decimal(0), held.amount + offsets.get(worker.connect_username, 0))
        op(day, 18, "stock_count_record", data={
            "supply_point_id": worker.pk, "item_id": item.pk, "commodity_slug": SLUG,
            "kind": "self_reported", "counted_on": day.isoformat(), "quantity": str(counted),
            "quantity_unit": "sachet", "source": "partner_reported", "opportunity_id": opportunity_id,
            "connect_username": worker.connect_username, "note": COUNTED,
        })  # fmt: skip
        recorded += 1
    return recorded


def _reports_arrive(program_id: int) -> bool:
    """Whether any count came from somewhere other than this module: then none is invented."""
    from connect_labs.supply_chain.models import StockCount

    return StockCount.objects.filter(program_id=program_id).exclude(note=COUNTED).exists()


def _workers(program_id: int, opportunity_id: int):
    from connect_labs.supply_chain.models import SupplyPoint

    return list(
        SupplyPoint.objects.filter(program_id=program_id, opportunity_id=opportunity_id, kind="user_held")
        .exclude(connect_username="")
        .order_by("connect_username")
    )


def _draw(username: str, seed: int) -> random.Random:
    """A worker's own seeded draw, so their figures do not move when someone joins the roster."""
    digest = hashlib.sha256(f"{seed}:{username}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def short_workers(usernames, *, seed: int = 11) -> set:
    """The workers sent only the fortnight ahead: one in SHORT_EVERY, at least one when there are a few.

    A fixed share, not a coin per worker, so the clone shows "a few" below minimum
    whatever its roster -- a coin per worker gave anything from none to a third.
    Chosen by each worker's own seeded draw, so a top-up and a reseed agree.
    """
    names = sorted(usernames)
    count = round(len(names) / SHORT_EVERY) or (1 if len(names) >= 3 else 0)
    return set(sorted(names, key=lambda u: _draw(u, seed).random())[:count])


def target_days(usernames, *, seed: int = 11) -> dict:
    """{username: (days at each delivery, days on the seed day)} for every worker who is not short."""
    short = short_workers(usernames, seed=seed)
    targets = {}
    for username in sorted(usernames):
        if username not in short:
            rng = _draw(username, seed + 1)
            targets[username] = (rng.randint(*TARGET_DAYS), rng.randint(*TODAY_DAYS))
    return targets


def delivery_days(first: date, today: date) -> list[date]:
    """Every delivery day up to the seed: each CADENCE_DAYS, the last LAST_DELIVERY_DAYS_AGO before `today`.

    Anchored to `today`, not to a weekday or the first visit, so the seed day falls at
    the same point in the cycle whenever it runs. The first is on or before the first
    visit, so nobody gives out stock before they have been given any.
    """
    last = max(today - timedelta(days=LAST_DELIVERY_DAYS_AGO), first)
    days = [last]
    while days[-1] > first:
        days.append(days[-1] - timedelta(days=CADENCE_DAYS))
    return sorted(days)


def _given(by_day: dict, start: date, end: date) -> Decimal:
    """What a worker gave out from `start` to `end`, both days in."""
    return sum((q for day, q in by_day.items() if start <= day <= end), Decimal(0))


def pace(by_day: dict, on: date) -> Decimal:
    """A worker's sachets a day over the PACE_DAYS ending on `on` -- the pace the Workers page shows that day."""
    return _given(by_day, on - timedelta(days=PACE_DAYS - 1), on) / PACE_DAYS


def _ahead(by_day: dict, on: date, known_until: date, rate: Decimal) -> Decimal:
    """What a worker will give out in the fortnight from `on`: the visits we have, then `rate` for the rest."""
    end = on + timedelta(days=CADENCE_DAYS - 1)
    known = _given(by_day, on, min(end, known_until))
    unknown = max((end - max(known_until, on - timedelta(days=1))).days, 0)
    return known + rate * unknown


def size_delivery(by_day: dict, *, on: date, held: Decimal, known_until: date, days) -> Decimal:
    """Sachets to send a worker on `on`, who holds `held` that morning; `days` of cover, or None for a short one.

    Topped up to `days` of cover at the pace their last fortnight measured (their
    first fortnight's, before they have one), and never to less than the fortnight
    ahead will take, so nobody gives out stock they were never given. A short worker
    is sent only that fortnight. A worker with nothing to give out gets nothing.
    """
    behind = pace(by_day, on - timedelta(days=1))
    ahead = _ahead(by_day, on, known_until, behind)
    if not ahead and not behind:
        return Decimal(0)
    rate = behind or ahead / CADENCE_DAYS
    wanted = ahead if days is None else max(rate * days, ahead)
    return _round_up(max(wanted - held, Decimal(0)))


def _balances(by_day: dict, issues: list, start: date, end: date) -> dict:
    """{day: what the worker holds that evening} from `start` to `end`, for deliveries [(day, sachets)]."""
    out, held = {}, Decimal(0)
    day = start
    while day <= end:
        held += sum((q for on, q in issues if on == day), Decimal(0)) - by_day.get(day, Decimal(0))
        out[day] = held
        day += timedelta(days=1)
    return out


def plan_distributions(dispensed: dict, *, first: date, today: date, known_until: date | None = None,
                       seed: int = 11) -> list[dict]:  # fmt: skip
    """[{on, lines: {username: sachets}}] for every delivery up to `today`, so that `today` reads as designed.

    `dispensed` is {username: {day: sachets}}, from every visit we have (`known_until`,
    which may run past today: a clone's visits do). Each delivery is sized as a
    real one would be (`size_delivery`), except the last: it is sized so that on
    `today` the worker holds TODAY_DAYS of cover at the pace the Workers page then
    measures -- or, short, the rest of their fortnight. When what they held before it
    is already more than that (their caseload has fallen since an earlier, bigger
    delivery), the earlier deliveries are trimmed, newest first and never below what
    the worker gave out after them, until it is not.

    Until 2026-10-13 every delivery was sized to the higher of the fortnight before
    and the fortnight after it. A clone's visits open with the enrolment burst --
    every child's first ration in its first fortnight -- so the early deliveries
    were sized at two or three times the pace that followed, the stock never ran
    down, and on the seed day workers held 21 to 109 days of cover with none below
    their minimum. The last delivery fell on the seed day itself whenever it was
    seeded on the right weekday, topping everyone up that morning.
    """
    known_until = known_until or today
    days = delivery_days(first, today)
    targets = target_days(dispensed, seed=seed)
    plan: dict = {username: [] for username in dispensed}
    for username, by_day in dispensed.items():
        issues = plan[username]
        target = targets.get(username)
        for n, on in enumerate(days):
            held = _balances(by_day, issues, days[0], on - timedelta(days=1)).get(on - timedelta(days=1), Decimal(0))
            if n < len(days) - 1:
                quantity = size_delivery(
                    by_day, on=on, held=held, known_until=known_until, days=target[0] if target else None
                )
            else:
                quantity, wanted_today = _last_delivery(by_day, on=on, held=held, today=today,
                                                        known_until=known_until, target=target)  # fmt: skip
            issues.append((on, quantity))
        _trim_to(by_day, issues, days[0], today, wanted_today)
    out = []
    for on in days:
        lines = {u: q for u, issues in plan.items() for d, q in issues if d == on and q > 0}
        if lines:
            out.append({"on": on, "lines": lines})
    return out


def _last_delivery(by_day, *, on, held, today, known_until, target) -> tuple[Decimal, Decimal]:
    """(sachets to send on `on`, what the worker should hold on `today`) for the last delivery before the seed."""
    now = pace(by_day, today)
    ahead = _ahead(by_day, on, known_until, now)
    since = _given(by_day, on, today)
    wanted_today = max(ahead - since, Decimal(0)) if target is None else now * target[1]
    quantity = max(wanted_today + since - held, ahead - held, Decimal(0))
    return (_round_up(quantity) if quantity > 0 else Decimal(0)), wanted_today


def _trim_to(by_day: dict, issues: list, start: date, today: date, wanted: Decimal) -> None:
    """Take back what earlier deliveries over-sent, newest first, until `today` holds no more than `wanted` + a step.

    A delivery is cut by no more than the least the worker held on any day after it,
    so no balance goes below zero, and in whole steps of ten.
    """
    for i in range(len(issues) - 1, -1, -1):
        balances = _balances(by_day, issues, start, today)
        excess = balances.get(today, Decimal(0)) - wanted
        if excess < 10:
            return
        on, quantity = issues[i]
        floor = min((held for day, held in balances.items() if day >= on), default=Decimal(0))
        cut = (min(excess, quantity, floor) // 10) * 10
        if cut > 0:
            issues[i] = (on, quantity - cut)


def _dispensed(rule, visits) -> tuple[dict, dict]:
    """({username: {day: sachets}}, {username: user uuid}) for `visits`, by `rule`."""
    from connect_labs.supply_chain.stock.services.dispensing import evaluate

    dispensed: dict = defaultdict(lambda: defaultdict(Decimal))
    uuids = {}
    for visit in visits:
        username = str(visit.get("username") or "").strip()
        if not username:
            continue
        uuids.setdefault(username, str(visit.get("user_id") or ""))
        given = evaluate(rule.lines, visit.get("form_json") or {}, rule.item, rule_forms=tuple(rule.forms or ()))
        if given.quantity:
            dispensed[username][_day(visit)] += given.quantity
    return dispensed, uuids


def _record_delivery(op, partner_id: int, opportunity_id: int, item_id: int, issue: dict) -> None:
    op(issue["on"], 8, "distribution_record", data={
        "supply_point_id": partner_id, "opportunity_id": opportunity_id, "commodity_slug": SLUG,
        "distributed_on": issue["on"].isoformat(), "source": "partner_reported", "note": INVENTED,
        "lines": [
            {"connect_username": u, "item_id": item_id, "quantity": str(q), "quantity_unit": "sachet"}
            for u, q in sorted(issue["lines"].items())
        ],
    })  # fmt: skip


def seed(
    *, program_id: int, opportunity_id: int, reset: bool = False, today: date | None = None, pages: bool = True
) -> dict:
    """Seed the clone's supply chain; idempotent unless `reset`. Server-side only.

    Every run, reset or not, also makes or refreshes the Supply tabs and home pages it
    is seen through (`pages`; demo/clone_pages.py).
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import DispensingRule
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services import visit_source
    from connect_labs.supply_chain.stock.services.workers import worker_slug

    # Refused before anything is read: a real programme, or an opportunity not filed under this one.
    scopes.require_synthetic(program_id, "seed a clone's supply chain")
    scopes.require_programme_opportunity(program_id, opportunity_id)
    from connect_labs.supply_chain.runner import declare_dimagi_if_undeclared

    # A clone is Dimagi's demo, so Dimagi runs it -- the buyer of record its upstream names (#2458).
    declare_dimagi_if_undeclared(program_id, "seeded as a Dimagi synthetic clone")

    today = today or timezone.localdate()
    access = SupplyDataAccess(
        access_token=visit_source.SYNTHETIC_TOKEN,
        program_id=program_id,
        opportunity_id=opportunity_id,
        caller=SYSTEM,
    )
    if DispensingRule.objects.filter(program_id=program_id, opportunity_id=opportunity_id).exists():
        if not reset:
            return {
                "seeded": False,
                "reason": "already seeded; pass --reset to rebuild it",
                "pages": _pages(program_id, opportunity_id, pages),
            }
        access.purge()

    # Every visit the clone has, past today included: a delivery is sized to the fortnight
    # after it. Only those up to today are read into the ledger now.
    every = [v for v in visit_source.fetch_visits(opportunity_id, None) if _day(v)]
    visits = [v for v in every if _day(v) <= today]
    if not visits:
        raise ValueError(f"opportunity {opportunity_id} has no visits to read")
    first = min(_day(v) for v in visits)
    known_until = max(_day(v) for v in every)
    setup = delivery_days(first, today)[0] - timedelta(days=7)

    def op(day, hour, name, **payload):
        at = timezone.make_aware(datetime.combine(day, time(hour)))
        with seed_overrides(program_id, channel="command", recorded_at=at):
            return call_operation(name, access, payload)

    op(setup, 9, "commodity_upsert", data={
        "slug": SLUG, "name": NAME, "category": "therapeutic_food",
        # The programme's protocol: 2 sachets a day (14 a week) for up to 12
        # weekly visits, inside 16 weeks of enrolment -- 168 sachets a full
        # course, which every child is meant to finish. The stock forecast
        # reads both: the course size, and the daily figure for the weeks of
        # treatment the visits cannot yet measure.
        "course_definition": {
            "base_units_per_day": "2", "days_per_course": 84, "base_units_per_course": 168,
            "source": "programme protocol: 2 sachets a day (14 a week), up to 12 weekly visits within 16 weeks",
        },
        "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150,
    })  # fmt: skip
    item = op(setup, 9, "item_upsert", data={
        "sku": SKU, "name": NAME, "commodity_slug": SLUG,
        "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150,
    })  # fmt: skip
    central = op(setup, 9, "supply_point_upsert", data={
        "slug": "central-store", "name": "Central store", "kind": "central_store",
        "source": "we_recorded", "min_months_of_stock": "2", "max_months_of_stock": "6",
        "note": INVENTED,
    })  # fmt: skip
    partner = op(setup, 9, "supply_point_upsert", data={
        "slug": "partner-store", "name": "Partner store", "kind": "regional_store",
        "parent_supply_point_id": central["id"], "source": "partner_reported",
        "min_months_of_stock": "1", "max_months_of_stock": "3", "note": INVENTED,
    })  # fmt: skip
    rule = op(setup, 12, "dispensing_rule_upsert", data={
        "opportunity_id": opportunity_id, "item_id": item["id"], "resupply_point_id": partner["id"],
        "active_from": first.isoformat(), "lines": rule_lines(),
        "reports": {
            "balance_paths": [PATHS["balance"], PATHS["remaining"]],
            "receipt": {"quantity_paths": [PATHS["received"]], "date_paths": [PATHS["received_on"]]},
        },
        "cases": rule_cases(),
    })  # fmt: skip

    # What each worker's visits gave out, day by day, by the rule just saved.
    saved = DispensingRule.objects.select_related("item__commodity").get(pk=rule["id"])
    dispensed, uuids = _dispensed(saved, every)
    seen = {str(v.get("username") or "").strip() for v in visits} - {""}
    dispensed = {u: by_day for u, by_day in dispensed.items() if u in seen}
    uuids = {u: uuid for u, uuid in uuids.items() if u in seen}
    issues = plan_distributions(dispensed, first=first, today=today, known_until=known_until)
    total_issued = sum(q for issue in issues for q in issue["lines"].values())

    # Stock into the network: every issue, plus what holds the partner store in the middle
    # of its band at the pace its workers now give RUTF out (the sum of their 14-day paces,
    # which is what its subtree cover is read against).
    total_dispensed = sum(_given(by_day, first, today) for by_day in dispensed.values())
    monthly = sum((pace(by_day, today) for by_day in dispensed.values()), Decimal(0)) * 30
    to_partner = _round_up(total_issued + monthly * PARTNER_MONTHS, 150)
    # The central store's cover is read against what IT sends out -- the one transfer to the
    # partner store, over the time since setup -- not against the visits' rate.
    central_monthly = to_partner / max(Decimal((today - setup).days) / 30, Decimal(1))
    opening = _round_up(to_partner + central_monthly * CENTRAL_MONTHS, 150)
    base = {"commodity_slug": SLUG, "item_id": item["id"], "quantity_unit": "sachet", "occurred_on": setup.isoformat()}
    # The opening stock arrives on an order: the tender, award, shipment and receipt that brought it
    # (invented, and each says so) -- so Sourcing, Orders and "Where it went" have something upstream.
    from connect_labs.supply_chain.demo.clone_upstream import seed_upstream

    upstream = seed_upstream(
        op, program_id=program_id, central_id=central["id"], item=saved.item, opening_sachets=opening,
        setup=setup, today=today,
    )  # fmt: skip
    op(setup, 11, "movement_record", data={
        **base, "kind": "transfer", "from_supply_point_id": central["id"], "to_supply_point_id": partner["id"],
        "quantity": str(to_partner), "source": "we_recorded", "note": INVENTED,
    })  # fmt: skip
    for username in sorted(uuids):
        op(setup, 13, "supply_point_upsert", data={
            "slug": worker_slug(opportunity_id, username), "name": username, "kind": "user_held",
            "opportunity_id": opportunity_id, "connect_username": username,
            "connect_user_uuid": uuids[username], "parent_supply_point_id": partner["id"],
            "source": "connect_visit", "min_months_of_stock": "0.5", "max_months_of_stock": "1.5",
        })  # fmt: skip

    weeks = []
    pending = list(issues)
    monday = _monday(first)
    while monday <= today:
        until = min(monday + timedelta(days=6), today)
        # This week's deliveries (the first week's also takes the one made before the first visit).
        while pending and pending[0]["on"] <= until:
            _record_delivery(op, partner["id"], opportunity_id, item["id"], pending.pop(0))
        # Read each week on its Sunday evening, as far as that Sunday (today for the current week).
        report = op(until, 20, "visit_consumption_ingest", opportunity_id=opportunity_id, until=until.isoformat())
        # Each worker counts their bag on the Sunday after the week's read (invented; see COUNTED).
        counts = record_counts(
            op, program_id=program_id, opportunity_id=opportunity_id, item=saved.item,
            workers=_workers(program_id, opportunity_id), day=until,
        )  # fmt: skip
        weeks.append(
            {
                "week_of": monday.isoformat(),
                "posted": report["posted"],
                "no_answer": report["no_answer"],
                "counts": counts,
            }
        )
        monday += timedelta(weeks=1)

    return {
        "seeded": True,
        "program_id": program_id,
        "opportunity_id": opportunity_id,
        "workers": len(uuids),
        "visits": len(visits),
        "first_visit": first.isoformat(),
        "sachets_dispensed": str(total_dispensed),
        "sachets_issued": str(total_issued),
        "distributions": len(issues),
        "upstream": upstream,
        "weeks": weeks,
        "pages": _pages(program_id, opportunity_id, pages),
    }


def _pages(program_id: int, opportunity_id: int, wanted: bool):
    """The Supply tabs and home pages the demo is seen through (demo/clone_pages.py), unless not `wanted`."""
    if not wanted:
        return None
    from connect_labs.supply_chain.demo.clone_pages import ensure_pages

    return ensure_pages(program_id=program_id, opportunity_id=opportunity_id)


# ---- keeping it going ----------------------------------------------------------
#
# The clone's visits run on past today, and the hourly visit reader posts them as
# their days arrive. The deliveries the seeder invented stop at the day it ran, so a
# worker's stock would only ever fall. Every day, `top_up` records the deliveries
# that have fallen due since -- every CADENCE_DAYS after the last one, each sized by
# `size_delivery` against what the ledger says the worker holds that morning, with the
# same seeded per-worker targets as the seed -- and gives any worker who has appeared
# since a supply point of their own. It runs daily (migration 0054): a short worker's
# delivery is only the fortnight they need, and a delivery recorded days late would
# leave them out in between.


def top_up(*, program_id: int, opportunity_id: int, today: date | None = None) -> dict:
    """Record the invented deliveries that have fallen due on a seeded clone. Idempotent."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import DispensingRule, Distribution, StockCount, SupplyPoint
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services import ledger, visit_source
    from connect_labs.supply_chain.stock.services.workers import worker_slug
    from connect_labs.supply_chain.values import Quantity

    scopes.require_synthetic(program_id, "top up a clone's supply chain")
    scopes.require_programme_opportunity(program_id, opportunity_id)
    today = today or timezone.localdate()
    rule = (
        DispensingRule.objects.select_related("item__commodity", "resupply_point")
        .filter(program_id=program_id, opportunity_id=opportunity_id, item__sku=SKU)
        .first()
    )
    if rule is None:
        return {"topped_up": False, "reason": "not seeded; run supply_seed_clone_supply first"}
    partner, first = rule.resupply_point, rule.active_from

    every = [v for v in visit_source.fetch_visits(opportunity_id, None) if _day(v)]
    known_until = max((_day(v) for v in every), default=today)
    dispensed, uuids = _dispensed(rule, every)

    access = SupplyDataAccess(
        access_token=visit_source.SYNTHETIC_TOKEN,
        program_id=program_id,
        opportunity_id=opportunity_id,
        caller=SYSTEM,
    )

    def op(day, hour, name, **payload):
        at = timezone.make_aware(datetime.combine(day, time(hour)))
        with seed_overrides(program_id, channel="command", recorded_at=at):
            return call_operation(name, access, payload)

    known = set(
        SupplyPoint.objects.filter(program_id=program_id, kind="user_held", opportunity_id=opportunity_id)
        .exclude(connect_username="")
        .values_list("connect_username", flat=True)
    )
    added = []
    seen = {str(v.get("username") or "").strip() for v in every if _day(v) <= today} - {""}
    for username in sorted(seen - known):
        op(today, 13, "supply_point_upsert", data={
            "slug": worker_slug(opportunity_id, username), "name": username, "kind": "user_held",
            "opportunity_id": opportunity_id, "connect_username": username,
            "connect_user_uuid": uuids[username], "parent_supply_point_id": partner.pk,
            "source": "connect_visit", "min_months_of_stock": "0.5", "max_months_of_stock": "1.5",
        })  # fmt: skip
        added.append(username)

    invented = Distribution.objects.filter(
        program_id=program_id, supply_point=partner, note__startswith=INVENTED_PREFIX
    ).order_by("-distributed_on")
    last = invented.values_list("distributed_on", flat=True).first()
    targets = target_days(dispensed)
    workers = {w.connect_username: w for w in _workers(program_id, opportunity_id)}
    recorded = []
    due = (last + timedelta(days=CADENCE_DAYS)) if last else first
    while due <= today:
        lines = {}
        for username, by_day in sorted(dispensed.items()):
            worker = workers.get(username)
            if worker is None:
                continue
            held = ledger.balance(program_id, worker, item=rule.item, unit="sachet", on_date=due - timedelta(days=1))
            held = max(held.amount, Decimal(0)) if isinstance(held, Quantity) else Decimal(0)
            target = targets.get(username)
            quantity = size_delivery(
                by_day, on=due, held=held, known_until=known_until, days=target[0] if target else None
            )
            if quantity > 0:
                lines[username] = quantity
        if lines:
            _record_delivery(op, partner.pk, opportunity_id, rule.item_id, {"on": due, "lines": lines})
            recorded.append({"on": due.isoformat(), "sachets": str(sum(lines.values()))})
        due += timedelta(days=CADENCE_DAYS)

    # The weekly count, for each Sunday since the last one recorded (none twice).
    counted = set(StockCount.objects.filter(program_id=program_id, note=COUNTED).values_list("counted_on", flat=True))
    sunday = _monday(first) + timedelta(days=6)
    counts = []
    while sunday < today:
        if sunday not in counted:
            n = record_counts(
                op, program_id=program_id, opportunity_id=opportunity_id, item=rule.item,
                workers=_workers(program_id, opportunity_id), day=sunday,
            )  # fmt: skip
            counts.append({"on": sunday.isoformat(), "counts": n})
        sunday += timedelta(weeks=1)
    return {"topped_up": True, "workers_added": added, "deliveries_recorded": recorded, "counts_recorded": counts}


def seeded_clones() -> list[tuple[int, int]]:
    """(program, opportunity) of every clone this module invented deliveries for."""
    from connect_labs.supply_chain.models import Distribution

    return sorted(
        set(
            Distribution.objects.filter(note__startswith=INVENTED_PREFIX)
            .values_list("program_id", "opportunity_id")
            .distinct()
        )
    )
