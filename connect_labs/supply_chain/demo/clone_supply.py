"""The RUTF supply chain behind a synthetic clone of the real RUTF opportunity.

A clone (`synthetic_clone_opp`) of the real RUTF deliver opportunity gives
visits shaped like the real ones -- the same workers' caseloads, the same form
paths, the RUTF each visit says it gave out -- with nothing real copied. What
the programme has not told us is how much RUTF each worker was GIVEN to give
out. This seeder supplies that, and says on every record that it is invented:

- the catalogue item, a central store and a partner store, and opening stock;
- the RUTF dispensing rule, on the released app's own paths
  (`stock_from_visits.PATHS`, design §9), so visits post consumption;
- a worker supply point for every worker who submitted a visit;
- every two weeks, a distribution to each active worker of roughly what the
  clone shows they then gave out over the next two weeks, varied per worker so
  some run short and some carry spare. The figures are INVENTED, and each
  distribution's note says so;
- the visit reader, run week by week to today, so the ledger, the as-of
  history and the belief views walk forward exactly as a live programme's
  would.

Synthetic only: every write goes through `call_operation`, whose guards refuse
a programme or opportunity that is not a registered labs-only one.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal

from django.utils import timezone

from connect_labs.supply_chain.demo.stock_from_visits import FORM_SCREENING, FORM_VISIT, PATHS

CADENCE_WEEKS = 2
# Months of cover each store holds once the issues are out: the middle of its band
# (central 2-6, partner 1-3), so neither reads "below min" for want of invented stock.
CENTRAL_MONTHS, PARTNER_MONTHS = Decimal(4), Decimal(2)
SLUG, SKU = "rutf", "rutf-150"
NAME = "Ready-to-use therapeutic food (RUTF), 92 g sachet"
INVENTED = (
    "Invented for the demo: the programme has not yet said how much RUTF each worker received. "
    "Sized from the RUTF the worker's visits gave out over the next two weeks."
)


def rule_lines() -> list[dict]:
    """RUTF as the released app records it: the Screening total, or the Visit Form ration plus appetite test."""

    def stated(key, form):
        return {"kind": "stated", "paths": [PATHS[key]], "unit": "sachet", "forms": [form]}

    return [
        stated("rutf_screening_total", FORM_SCREENING),
        stated("rutf_visit", FORM_VISIT),
        stated("appetite_visit", FORM_VISIT),
    ]


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


def plan_distributions(dispensed: dict, *, first: date, last: date, seed: int = 11) -> list[dict]:
    """{monday: {username: quantity}} every CADENCE_WEEKS, from what each worker then gave out.

    `dispensed` is {username: {monday: sachets}}. Each worker's issue covers the
    next CADENCE_WEEKS weeks times a factor of their own, between 1.0 and 1.6,
    so the ledger shows the spread a real network has: some carry spare, some
    run down to nearly nothing before the next issue -- but none gives out
    stock it was never given (a factor under 1 left workers below zero, which
    reads as broken data, not as a network). A worker with nothing to give out
    in the window gets nothing.
    """
    rng = random.Random(seed)
    factor = {u: Decimal(str(round(rng.uniform(1.0, 1.6), 2))) for u in sorted(dispensed)}
    issues = []
    monday = _monday(first)
    while monday <= last:
        lines = {}
        for username, weeks in dispensed.items():
            ahead = sum(weeks.get(monday + timedelta(weeks=k), Decimal(0)) for k in range(CADENCE_WEEKS))
            if ahead > 0:
                lines[username] = _round_up(ahead * factor[username])
        if lines:
            issues.append({"on": monday, "lines": lines})
        monday += timedelta(weeks=CADENCE_WEEKS)
    return issues


def seed(*, program_id: int, opportunity_id: int, reset: bool = False, today: date | None = None) -> dict:
    """Seed the clone's supply chain; idempotent unless `reset`. Server-side only."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import DispensingRule
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services import visit_source
    from connect_labs.supply_chain.stock.services.dispensing import evaluate
    from connect_labs.supply_chain.stock.services.workers import worker_slug

    # Refused before anything is read: a real programme, or an opportunity not filed under this one.
    scopes.require_synthetic(program_id, "seed a clone's supply chain")
    scopes.require_programme_opportunity(program_id, opportunity_id)

    today = today or timezone.localdate()
    access = SupplyDataAccess(
        access_token=visit_source.SYNTHETIC_TOKEN,
        program_id=program_id,
        opportunity_id=opportunity_id,
        caller=SYSTEM,
    )
    if DispensingRule.objects.filter(program_id=program_id, opportunity_id=opportunity_id).exists():
        if not reset:
            return {"seeded": False, "reason": "already seeded; pass --reset to rebuild it"}
        access.purge()

    visits = [v for v in visit_source.fetch_visits(opportunity_id, None) if _day(v) and _day(v) <= today]
    if not visits:
        raise ValueError(f"opportunity {opportunity_id} has no visits to read")
    first = min(_day(v) for v in visits)
    setup = _monday(first) - timedelta(days=7)

    def op(day, hour, name, **payload):
        at = timezone.make_aware(datetime.combine(day, time(hour)))
        with seed_overrides(program_id, channel="command", recorded_at=at):
            return call_operation(name, access, payload)

    op(setup, 9, "commodity_upsert", data={
        "slug": SLUG, "name": NAME, "category": "therapeutic_food",
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
    })  # fmt: skip

    # What each worker's visits gave out, week by week, by the rule just saved.
    saved = DispensingRule.objects.select_related("item__commodity").get(pk=rule["id"])
    dispensed: dict = defaultdict(lambda: defaultdict(Decimal))
    uuids = {}
    for visit in visits:
        username = str(visit.get("username") or "").strip()
        if not username:
            continue
        uuids.setdefault(username, str(visit.get("user_id") or ""))
        given = evaluate(saved.lines, visit.get("form_json") or {}, saved.item, rule_forms=tuple(saved.forms or ()))
        if given.quantity:
            dispensed[username][_monday(_day(visit))] += given.quantity
    issues = plan_distributions(dispensed, first=first, last=today)
    total_issued = sum(q for issue in issues for q in issue["lines"].values())

    # Stock into the network: every issue, plus what holds each store in the middle of
    # its band of months of cover at the rate the clone's visits give RUTF out.
    total_dispensed = sum(sum(w.values()) for w in dispensed.values())
    days = max((today - first).days, 1)
    monthly = total_dispensed / days * 30
    to_partner = _round_up(total_issued + monthly * PARTNER_MONTHS, 150)
    # The central store's cover is read against what IT sends out -- the one transfer to the
    # partner store, over the time since setup -- not against the visits' rate.
    central_monthly = to_partner / max(Decimal((today - setup).days) / 30, Decimal(1))
    opening = _round_up(to_partner + central_monthly * CENTRAL_MONTHS, 150)
    base = {"commodity_slug": SLUG, "item_id": item["id"], "quantity_unit": "sachet", "occurred_on": setup.isoformat()}
    op(setup, 10, "movement_record", data={
        **base, "kind": "receipt", "to_supply_point_id": central["id"], "quantity": str(opening),
        "source": "we_recorded", "note": INVENTED,
    })  # fmt: skip
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

    by_day = {issue["on"]: issue["lines"] for issue in issues}
    weeks = []
    monday = _monday(first)
    while monday <= today:
        lines = by_day.get(monday)
        if lines:
            op(monday, 8, "distribution_record", data={
                "supply_point_id": partner["id"], "opportunity_id": opportunity_id, "commodity_slug": SLUG,
                "distributed_on": monday.isoformat(), "source": "partner_reported", "note": INVENTED,
                "lines": [
                    {"connect_username": u, "item_id": item["id"], "quantity": str(q), "quantity_unit": "sachet"}
                    for u, q in sorted(lines.items())
                ],
            })  # fmt: skip
        # Read each week on its Sunday evening, as far as that Sunday (today for the current week).
        until = min(monday + timedelta(days=6), today)
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
        "weeks": weeks,
    }


# ---- keeping it going ----------------------------------------------------------
#
# The clone's visits run on past today, and the hourly visit reader posts them as
# their days arrive. The deliveries the seeder invented stop at the day it ran, so a
# worker's stock would only ever fall. Each week, `top_up` records the deliveries
# that have fallen due since -- planned exactly as the seeder plans them, by the same
# seeded per-worker factors, so a top-up and a reseed agree -- and gives any worker
# who has appeared since a supply point of their own.


def top_up(*, program_id: int, opportunity_id: int, today: date | None = None) -> dict:
    """Record the invented deliveries that have fallen due on a seeded clone. Idempotent."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import scopes
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.history.context import seed_overrides
    from connect_labs.supply_chain.models import DispensingRule, Distribution, SupplyPoint
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services import visit_source
    from connect_labs.supply_chain.stock.services.dispensing import evaluate
    from connect_labs.supply_chain.stock.services.workers import worker_slug

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

    # Each issue covers the fortnight after it, so read the visits that far ahead.
    horizon = today + timedelta(weeks=CADENCE_WEEKS)
    visits = [v for v in visit_source.fetch_visits(opportunity_id, None) if _day(v) and _day(v) <= horizon]
    dispensed: dict = defaultdict(lambda: defaultdict(Decimal))
    uuids = {}
    for visit in visits:
        username = str(visit.get("username") or "").strip()
        if not username:
            continue
        uuids.setdefault(username, str(visit.get("user_id") or ""))
        given = evaluate(rule.lines, visit.get("form_json") or {}, rule.item, rule_forms=tuple(rule.forms or ()))
        if given.quantity:
            dispensed[username][_monday(_day(visit))] += given.quantity

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
    for username in sorted(set(uuids) - known):
        op(today, 13, "supply_point_upsert", data={
            "slug": worker_slug(opportunity_id, username), "name": username, "kind": "user_held",
            "opportunity_id": opportunity_id, "connect_username": username,
            "connect_user_uuid": uuids[username], "parent_supply_point_id": partner.pk,
            "source": "connect_visit", "min_months_of_stock": "0.5", "max_months_of_stock": "1.5",
        })  # fmt: skip
        added.append(username)

    done = set(
        Distribution.objects.filter(program_id=program_id, supply_point=partner).values_list(
            "distributed_on", flat=True
        )
    )
    recorded = []
    for issue in plan_distributions(dispensed, first=first, last=today):
        if issue["on"] in done:
            continue
        op(issue["on"], 8, "distribution_record", data={
            "supply_point_id": partner.pk, "opportunity_id": opportunity_id, "commodity_slug": SLUG,
            "distributed_on": issue["on"].isoformat(), "source": "partner_reported", "note": INVENTED,
            "lines": [
                {"connect_username": u, "item_id": rule.item_id, "quantity": str(q), "quantity_unit": "sachet"}
                for u, q in sorted(issue["lines"].items())
            ],
        })  # fmt: skip
        recorded.append({"on": issue["on"].isoformat(), "sachets": str(sum(issue["lines"].values()))})

    # The weekly count, for each Sunday since the last one recorded (none twice).
    from connect_labs.supply_chain.models import StockCount

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
        set(Distribution.objects.filter(note=INVENTED).values_list("program_id", "opportunity_id").distinct())
    )
