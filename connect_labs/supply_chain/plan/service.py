"""The stock plan, worked out in plain steps from the forecast and the saved choices.

Supply adopts the CBI dashboards' method (Sophie Feintuch's RUTF feedback, "Combining Supply
with our CBI dashboards") but feeds it from Supply's own records: the visits, the handovers
and receipts on the ledger, not phone balances and a typed-in starting stock. The forecast
(`stock/services/forecast.py`) is the one source of every figure here; this module only
chooses which of its figures answer which question and says where each came from.

The three choices a partner may change are the plan (`StockPlan`):

  enrolment rate   new children per field worker a week; the default is each worker's own recent rate
  buffer           extra days of stock to hand out, for everyone or for one worker or facility
  target cases     how many children the programme means to treat in all (500 now)

What to issue one field worker at the next handout, week-long:

    children due a follow-up (sachets for the rest of their course, this week)
  + children expected to enrol (the enrolment rate times the first week's ration)
  + appetite tests                  (not in Supply's data today: shown as not counted)
  + buffer                          (buffer days / 7 of the two lines above)
  - what they hold                  (the official figure: the phone balance until the partner's handovers are
                                     recorded, then Supply's records; official_stock.official_flw_stock)
  = to issue (never below nothing, in whole sachets)

A child leaves the list when their course ends: the forecast stops supplying a child at
the course size (the expected course once enough children have finished, else the
protocol's), which is the dashboards' "exits from recovery or visit 12".
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.plan.models import StockPlan
from connect_labs.supply_chain.stock.services import flow as flowing
from connect_labs.supply_chain.stock.services import forecast as forecasting
from connect_labs.supply_chain.values import iso_day_text

ZERO = Decimal(0)
DEFAULT_TARGET_CASES = 500
DEFAULT_BUFFER_DAYS = 0
# Dosing is flagged when the children's real daily dose is this far from the protocol's.
DOSE_TOLERANCE_PERCENT = 10
# "Running short" is looked at over this many days.
SHORT_DAYS = 7

# What each saved choice is called, and what it falls back to.
INPUTS = {
    "enrolment_per_flw_per_week": {
        "label": "New children each field worker enrols a week",
        "default_text": "each worker's own recent rate",
    },
    "buffer_days": {"label": "Extra days of stock (buffer)", "default_text": f"{DEFAULT_BUFFER_DAYS} days"},
    "target_cases": {"label": "Target number of children", "default_text": f"{DEFAULT_TARGET_CASES:,}"},
    "target_cases_by_partner": {
        "label": "Target for each partner",
        "default_text": "one target for the whole programme",
    },
}


def _d(value) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("amount")
    return None if value is None else Decimal(str(value))


def _plain(value: Decimal | None, places=0) -> str:
    if value is None:
        return "not known"
    rounded = value.quantize(Decimal(1) if places == 0 else Decimal(10) ** -places)
    return f"{rounded:,}"


def run_out_text(iso: str | None, today: date | None = None, data_to: str | None = None) -> dict:
    """A run-out date as a page says it: "30 Oct 2026", and whether it is already behind us.

    The demo's visits stop on 23 Oct but the page is read on 5 Nov: a run-out date of 30 Oct is not in the
    future, so it reads "may already have run out" rather than as a date still to come. `data_to` is the last
    day the figures rest on; it is said beside the flag.
    """
    if not iso:
        return {"text": "not in the weeks ahead", "past": False}
    today = today or date.today()
    day = date.fromisoformat(iso[:10])
    text = iso_day_text(day)
    if day < today:
        upto = f" (figures run to {iso_day_text(data_to)})" if data_to else ""
        return {"text": f"{text}: may already have run out{upto}", "past": True}
    return {"text": text, "past": False}


def as_of_note(fc: dict) -> dict:
    """What day the figures run to: "Based on visits up to 23 Oct 2026"."""
    data_to = fc.get("data_to")
    return {
        "data_to": data_to,
        "text": (
            f"Based on visits up to {iso_day_text(data_to)}" if data_to else "No visits yet to base the figures on"
        ),
    }


def _whole_up(value: Decimal) -> int:
    return int(math.ceil(value)) if value > 0 else 0


# ---- the saved plan ------------------------------------------------------------


def saved_plan(program_id, item) -> StockPlan | None:
    return StockPlan.objects.filter(program_id=program_id, item=item).first()


def plan_values(plan: StockPlan | None) -> dict:
    """The plan as it applies: each input's value, the default beside it, and whether it is the default."""
    rate = plan.enrolment_per_flw_per_week if plan else None
    buffer_days = plan.buffer_days if plan else DEFAULT_BUFFER_DAYS
    by_point = dict(plan.buffer_days_by_point or {}) if plan else {}
    target = plan.target_cases if plan else DEFAULT_TARGET_CASES
    by_partner = {str(k): int(v) for k, v in (plan.target_cases_by_partner or {}).items()} if plan else {}
    return {
        "enrolment_per_flw_per_week": {
            "value": rate,
            "default": None,
            "default_text": INPUTS["enrolment_per_flw_per_week"]["default_text"],
            "is_default": rate is None,
        },
        "buffer_days": {
            "value": buffer_days,
            "default": DEFAULT_BUFFER_DAYS,
            "default_text": INPUTS["buffer_days"]["default_text"],
            "is_default": buffer_days == DEFAULT_BUFFER_DAYS,
        },
        "buffer_days_by_point": by_point,
        "target_cases_by_partner": by_partner,
        # What the programme means to treat in all: the partners' own targets added up when any is set.
        "target_effective": sum(by_partner.values()) if by_partner else target,
        "target_cases": {
            "value": target,
            "default": DEFAULT_TARGET_CASES,
            "default_text": INPUTS["target_cases"]["default_text"],
            "is_default": target == DEFAULT_TARGET_CASES,
        },
    }


def _buffer_for(plan_vals, point_id) -> int:
    return int(plan_vals["buffer_days_by_point"].get(str(point_id), plan_vals["buffer_days"]["value"]))


# ---- the handout window --------------------------------------------------------------

DEFAULT_HANDOUT_EVERY_DAYS = 7
APPETITE_LOOKBACK_DAYS = 28


def _weekly(weeks, key) -> list[Decimal]:
    return [_d(w[key]) or ZERO for w in weeks]


def need_between(weekly: list[Decimal], start: int, end: int) -> Decimal:
    """What the weekly figures add up to over days `start`+1 .. `end` after the forecast's first day.

    Each week's figure is spread evenly over its seven days; days past the forecast's horizon count nothing.
    """
    total = ZERO
    for index in range(start // 7, (end - 1) // 7 + 1 if end > start else start // 7):
        if index >= len(weekly):
            break
        days_in_week = min(end, 7 * (index + 1)) - max(start, 7 * index)
        if days_in_week > 0:
            total += weekly[index] * days_in_week / 7
    return total


def lasts_for(weekly: list[Decimal], offset: int, stock: Decimal) -> int | None:
    """Days after `offset` that `stock` lasts at the weekly figures; None when it outlasts the forecast."""
    left = stock
    for day in range(offset + 1, len(weekly) * 7 + 1):
        left -= weekly[(day - 1) // 7] / 7
        if left < Decimal("-0.0001"):
            return day - offset - 1
    return None


def handout_window(program_id, anchor: date, today: date) -> dict:
    """The handout this plan is for and the length of the stretch it must cover.

    The handout is the next one planned and not yet confirmed (Distribution), else today. It runs
    to the next handout: the partner's own rhythm when its profile states one, else a week. Both are
    counted from the handout, not from the last day the visits cover.
    """
    from connect_labs.supply_chain.models import Distribution

    planned = (
        Distribution.objects.filter(program_id=program_id, distributed_on__gte=today)
        .order_by("distributed_on")
        .first()
    )
    while planned is not None and planned.status == "confirmed":
        planned = (
            Distribution.objects.filter(
                program_id=program_id, distributed_on__gte=today, distributed_on__gt=planned.distributed_on
            )
            .order_by("distributed_on")
            .first()
        )
    handout_on = planned.distributed_on if planned else today
    return {
        "handout_on": handout_on,
        "offset_days": max(0, (handout_on - anchor).days),
        "planned": planned is not None,
    }


def cadence_by_point(program_id) -> dict:
    """{supply point id: days between handouts}: the partner that runs it says, else a week."""
    from connect_labs.supply_chain.models import PartnerProfile
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    every = {
        p.org_id: p.handout_every_days
        for p in PartnerProfile.objects.filter(program_id=program_id, handout_every_days__isnull=False)
    }
    return {pk: every.get(org) or DEFAULT_HANDOUT_EVERY_DAYS for pk, org in partner_by_point(program_id).items()}


def appetite_per_week(program_id, item, anchor: date) -> dict:
    """{supply point id: sachets a week used in appetite tests}, from the last four weeks of visits.

    Read at the appetite paths the dispensing rule names (the same ones Where it went totals); empty for
    a rule that names none, so a programme whose app does not record them is not given a made-up figure.
    """
    from datetime import timedelta

    from connect_labs.supply_chain.models import WorkerVisit
    from connect_labs.supply_chain.stock.services.dispensing import household_paths, read_number
    from connect_labs.supply_chain.stock.services.flow import _first_kept
    from connect_labs.supply_chain.stock.services.visit_reader import REVERSING_STATUSES

    totals: dict = {}
    for rule in DispensingRule.objects.filter(program_id=program_id, item=item, status="active"):
        paths = household_paths(rule).get("appetite_paths")
        if not paths:
            continue
        rows = (
            WorkerVisit.objects.filter(
                program_id=program_id,
                opportunity_id=rule.opportunity_id,
                visit_date__gt=anchor - timedelta(days=APPETITE_LOOKBACK_DAYS),
                visit_date__lte=anchor,
            )
            .exclude(status__in=REVERSING_STATUSES)
            .values_list("supply_point_id", "answers")
        )
        for point_id, answers in rows:
            amount = read_number(_first_kept(answers or {}, paths))
            if amount is not None:
                totals[point_id] = totals.get(point_id, ZERO) + amount
    weeks = Decimal(APPETITE_LOOKBACK_DAYS) / 7
    return {pk: total / weeks for pk, total in totals.items()}


# ---- what to issue ---------------------------------------------------------------


def _step(label, value, source, sign=""):
    return {"label": label, "value": value, "source": source, "sign": sign}


def issue_rows(  # noqa: C901
    fc: dict, plan_vals: dict, *, offset_days=0, cadence=None, appetite=None, handout_on=None, today=None
) -> list[dict]:
    """One row per field worker: the steps to what to issue at the handout, and how long that lasts.

    The need is counted from the handout (`offset_days` after the forecast's first day) to the next one
    (`cadence[point]` days later, a week when not stated), and what a worker holds is what the records
    show less what they will use before the handout.
    """
    cadence = cadence or {}
    appetite = appetite or {}
    ration = _d((fc["basis"]["profile"] or [{}])[0].get("sachets"))
    rate_source = (
        "the plan's rate"
        if plan_vals["enrolment_per_flw_per_week"]["value"] is not None
        else "this worker's own recent rate from their visits"
    )
    rows = []
    for w in fc["workers"]:
        known = w["stock_known"]
        length = int(cadence.get(w["supply_point_id"]) or DEFAULT_HANDOUT_EVERY_DAYS)
        period = "this week" if length == 7 else f"in the {length} days to the next handout"
        committed_weeks, new_weeks = _weekly(w["weeks"], "committed"), _weekly(w["weeks"], "new")
        need_weeks = [c + n for c, n in zip(committed_weeks, new_weeks)]
        end = offset_days + length
        due = need_between(committed_weeks, offset_days, end)
        new = need_between(new_weeks, offset_days, end)
        appetite_week = appetite.get(w["supply_point_id"])
        tests = (appetite_week or ZERO) * Decimal(length) / 7
        used_before = need_between(need_weeks, 0, offset_days)
        official = w["official_stock"]
        held_now = _d(official["amount"]) if known else None
        stock_source = (
            f"{'the phone balance' if official['source'] == 'phone' else 'Supply’s records'}: {official['note']}"
        )
        run = run_out_text(w["run_out"]["date"], today, fc.get("data_to"))
        held = max(ZERO, held_now - used_before) if known else None
        days = _buffer_for(plan_vals, w["supply_point_id"])
        buffer = (due + new + tests) * Decimal(days) / length
        want = due + new + tests + buffer
        issue = _whole_up(want - (held or ZERO)) if known else _whole_up(want)
        short = bool(known and held < due + new + tests)
        lasts = lasts_for(need_weeks, offset_days, (held or ZERO) + Decimal(issue)) if known else None
        if w["basis"] == "cases":
            children = w["open_cases"]
            steps = [
                _step(
                    "Children due a follow-up",
                    f"{_plain(due)} sachets for these {children} children's visits {period}",
                    "their visits: children enrolled and not yet finished",
                ),
                _step(
                    "New children expected",
                    f"{_plain(_d(w['enrolled_per_week']), 1)} a week, {_plain(new)} sachets",
                    f"{rate_source}, times the first week's ration of {_plain(ration)} sachets",
                    "+",
                ),
                _step(
                    "Appetite tests",
                    f"{_plain(tests)} sachets" if appetite_week is not None else "not counted",
                    (
                        f"about {_plain(appetite_week, 1)} a week, the average of the last four weeks of visits"
                        if appetite_week is not None
                        else "this opportunity's app has no appetite-test question set up"
                    ),
                    "+",
                ),
                _step(
                    "Buffer",
                    f"{days} extra {'day' if days == 1 else 'days'}, {_plain(buffer)} sachets",
                    "the plan: "
                    + (
                        "set for this place"
                        if str(w["supply_point_id"]) in plan_vals["buffer_days_by_point"]
                        else "the same for everyone"
                    )
                    + ", as that share of the need above",
                    "+",
                ),
                _step(
                    "Held at the handout",
                    "not known" if not known else f"{_plain(held)} sachets",
                    stock_source
                    + (
                        f", less about {_plain(used_before)} used between the last visit data and the handout"
                        if offset_days
                        else ""
                    ),
                    "-",
                ),
            ]
        else:
            children = None
            steps = [
                _step(
                    f"The {length} days at their present pace",
                    f"{_plain(due)} sachets",
                    "no case rule on this opportunity, so the pace of the last weeks of visits",
                ),
                _step(
                    "Buffer",
                    f"{days} extra days, {_plain(buffer)} sachets",
                    "the plan, as that share of the need above",
                    "+",
                ),
                _step(
                    "Held at the handout",
                    "not known" if not known else f"{_plain(held)} sachets",
                    stock_source,
                    "-",
                ),
            ]
        rows.append(
            {
                "supply_point_id": w["supply_point_id"],
                "name": w["name"],
                "parent_supply_point_id": w.get("parent_supply_point_id"),
                "basis": w["basis"],
                "children": children,
                "due": due,
                "new": new,
                # One number for "needs about": the parts added here, never in the template, where
                # `|add` on text joins them ("71" + "23" read "7123"). Appetite tests are part of it.
                "need_week": due + new + tests,
                "appetite": tests,
                "buffer_days": days,
                "buffer": buffer,
                "held": held,
                "held_on_records": held_now,
                "stock_known": known,
                "stock_source": official["source"],
                "stock_phone": _d(official["phone"]),
                "stock_phone_on": official["phone_on"],
                "stock_records": _d(official["records"]),
                "stock_differs": official["differs"],
                "stock_note": official["note"],
                "to_issue": issue,
                "window_days": length,
                # How long the stock lasts once handed out (days after the handout), and whether that
                # reaches the next handout. None: it outlasts the whole forecast.
                "lasts_days": lasts,
                "lasts_until": (handout_on + timedelta(days=lasts)) if lasts is not None and handout_on else None,
                "covers_next_handout": bool(known and (lasts is None or lasts >= length)),
                # The forecast's one run-out answer for this worker (forecast.worker_run_out).
                "runs_dry_on": w["run_out"]["date"],
                "runs_dry_text": run["text"],
                "runs_dry_past": run["past"],
                "run_out_method": w["run_out"]["method"],
                "short": short,
                "steps": steps,
                "result": (f"Issue {issue:,} sachets" if issue else "Nothing to issue: they hold enough"),
            }
        )
    return rows


def group_rows(program_id, rows: list[dict]) -> list[dict]:
    """The rows by partner, then by the place each worker collects from, with what each place must pack.

    A worker's place is the point above them (a facility or store). Totals are whole sachets to issue,
    summed for each place, then for each partner: what has to be packed, not one programme-wide figure.
    """
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.models import SupplyPoint
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    owner = partner_by_point(program_id)
    place_names = dict(SupplyPoint.objects.filter(program_id=program_id).values_list("pk", "name"))
    org_names = dict(LabsOrg.objects.filter(pk__in={o for o in owner.values() if o}).values_list("pk", "name"))
    partners: dict = {}
    for row in rows:
        org = owner.get(row["supply_point_id"])
        place = row.get("parent_supply_point_id")
        partner = partners.setdefault(
            org, {"org_id": org, "name": org_names.get(org, "No partner recorded"), "places": {}, "to_issue": 0}
        )
        group = partner["places"].setdefault(
            place, {"place_id": place, "name": place_names.get(place, "No place recorded"), "rows": [], "to_issue": 0}
        )
        group["rows"].append(row)
        group["to_issue"] += row["to_issue"]
        partner["to_issue"] += row["to_issue"]
    out = []
    for partner in sorted(partners.values(), key=lambda p: p["name"].lower()):
        partner["places"] = sorted(partner["places"].values(), key=lambda g: g["name"].lower())
        out.append(partner)
    return out


def running_short(rows: list[dict]) -> list[dict]:
    """Workers who hold less than next week's need, soonest to run dry first."""
    short = [r for r in rows if r["short"]]
    return sorted(short, key=lambda r: (r["runs_dry_on"] or "9999", r["name"]))


# ---- the break-even question -------------------------------------------------------


def break_even(fc: dict, plan_vals: dict, *, cases=None, item=None, program_id=None, today=None) -> dict:
    """Does the stock left cover the children still in treatment and those still to come, up to the target?

    Starts from the children (`children`), answers two cases (`cases`: a full carton each, and the maximum
    12 weeks each) and says when to start the next tender (`next_tender`); the figures at the protocol's
    own course are kept alongside, as they were.
    """
    out = _break_even(fc, plan_vals)
    if cases is not None:
        children = children_summary(fc, cases, out["target"])
        children["partners"] = (
            children_by_partner(program_id, cases, plan_vals["target_cases_by_partner"]) if program_id else []
        )
        on_order = out["on_order"]
        out["children"] = children
        out["cases"] = two_cases(fc, cases, children, out["held"], on_order, item)
        out["next_tender"] = next_tender(program_id, fc, today or date.today()) if program_id else None
    return out


def _break_even(fc: dict, plan_vals: dict) -> dict:
    programme = fc["programme"]
    course = _d(fc["basis"]["course"]["size"])
    ration = _d((fc["basis"]["profile"] or [{}])[0].get("sachets"))
    held = _d(programme["on_hand"]) or ZERO
    owed = _d(programme.get("owed"))
    seen = programme.get("children_seen") or 0
    target = plan_vals["target_effective"]
    to_come = max(0, target - seen)
    on_order = sum((_d(o["quantity"]) or ZERO for o in programme["inbound"] if o["counted"]), ZERO)
    out = {
        "target": target,
        "children_so_far": seen,
        "children_to_come": to_come,
        "in_treatment": programme.get("open_cases", 0),
        "held": held,
        "owed": owed,
        "course": course,
        "course_basis": fc["basis"]["course"]["basis"],
        "course_children": fc["basis"]["course"]["completed_cases"],
        "course_chosen": fc["basis"]["course"]["chosen"],
        "course_reason": fc["basis"]["course"]["reason"],
        "course_low": _d(fc["basis"]["course"]["low"]),
        "course_high": _d(fc["basis"]["course"]["high"]),
        "on_order": on_order,
        "ration_per_visit": ration,
        "max_visits": int(course / ration) if course and ration else None,
        "known": course is not None and owed is not None,
    }
    if not out["known"]:
        out["answer"] = "Cannot be worked out yet: Supply has no course length or no children's visits to count from."
        return out
    left = held - owed
    need_new = course * to_come
    out["left_for_new"] = left
    out["needed_for_new"] = need_new
    out["covers"] = left >= need_new
    out["to_buy"] = max(ZERO, need_new - left)
    out["to_buy_with_orders"] = max(ZERO, need_new - left - on_order)
    if to_come == 0:
        out["visits_per_case"] = out["max_visits"]
        out["answer"] = f"The target of {target:,} children is already reached by the {seen:,} children seen."
    else:
        per_case = left / to_come
        out["sachets_per_case"] = per_case
        visits = None
        if ration:
            visits = max(0, min(out["max_visits"] or 0, int(per_case / ration)))
        out["visits_per_case"] = visits
        if out["covers"]:
            out["answer"] = (
                f"Yes. After the {_plain(owed)} sachets still owed to children in treatment, "
                f"{_plain(left)} are left, enough for all {to_come:,} children still to come to have a full "
                f"course ({_plain(course)} sachets, up to {out['max_visits']} visits)."
            )
        else:
            out["answer"] = (
                f"No. After the {_plain(owed)} sachets still owed to children in treatment, {_plain(left)} are "
                f"left for {to_come:,} children still to come: about {_plain(per_case)} sachets each"
                + (f", or {visits} of {out['max_visits']} visits" if visits is not None and out["max_visits"] else "")
                + f". A full course for all of them needs {_plain(need_new)}, so {_plain(out['to_buy'])} more."
            )
    return out


FULL_CARTON_SACHETS = 150
MAX_WEEKS = 12


def children_by_partner(program_id, cases, targets: dict) -> list[dict]:
    """Each partner's children: seen, in treatment, and (where it has a target) still to enrol up to it."""
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    owner = partner_by_point(program_id)
    rows: dict = {}
    for case in cases:
        org = owner.get(case.supply_point_id)
        row = rows.setdefault(org, {"org_id": org, "seen": 0, "in_treatment": 0, "exited": 0})
        row["seen"] += 1
        row["in_treatment"] += case.status == "open"
        row["exited"] += case.status == "exited"
    for org in {int(k) for k in targets}:
        rows.setdefault(org, {"org_id": org, "seen": 0, "in_treatment": 0, "exited": 0})
    names = dict(LabsOrg.objects.filter(pk__in=[o for o in rows if o]).values_list("pk", "name"))
    out = []
    for org, row in rows.items():
        target = targets.get(str(org))
        out.append(
            {
                **row,
                "name": names.get(org, "No partner recorded"),
                "target": target,
                "to_enrol": max(0, target - row["seen"]) if target is not None else None,
            }
        )
    return sorted(out, key=lambda r: r["name"].lower())


def recent_enrolment(fc: dict) -> dict:
    """The recent rate as a number: new children a week per field worker, from their own recent enrolments."""
    rates = [_d(w["enrolled_per_week"]) for w in fc["workers"] if w.get("enrolled_per_week") is not None]
    return {
        "per_flw_per_week": (sum(rates, ZERO) / len(rates)) if rates else None,
        "workers": len(rates),
        "all_workers": len(fc["workers"]),
    }


def children_summary(fc: dict, cases, target: int) -> dict:
    """The children as the visits show them, once: exited, in treatment (and what each has had), still to enrol."""
    open_cases = [c for c in cases if c.status == "open"]
    seen = fc["programme"].get("children_seen") or len(cases)
    received = sum((c.received for c in open_cases), ZERO)
    meaning = fc["programme"].get("children") or {}
    text = ""
    if meaning:
        text = (
            f"{meaning['seen']:,} children seen in all: {meaning['enrolled']:,} enrolled since the visits began "
            f"and {meaning['carried_over']:,} already in treatment before them. {meaning['in_treatment']:,} are in "
            f"treatment now and {meaning['finished']:,} have finished."
        )
    return {
        "text": text,
        "enrolled": meaning.get("enrolled"),
        "carried_over": meaning.get("carried_over"),
        "finished": meaning.get("finished"),
        "seen": seen,
        "exited": sum(1 for c in cases if c.status == "exited"),
        "lost": sum(1 for c in cases if c.status == "lost"),
        "in_treatment": len(open_cases),
        "received_so_far": received,
        "received_each": (received / len(open_cases)) if open_cases else None,
        "target": target,
        "to_enrol": max(0, target - seen),
    }


def two_cases(fc: dict, cases, children: dict, held: Decimal, on_order: Decimal, item) -> list[dict]:
    """Do we have enough if every remaining child needs a full carton, and if all need the maximum 12 weeks?

    Remaining children are those in treatment (what each still needs is the course less what they have
    had) and those still to enrol (a whole course each). The maximum is the protocol's course when it is
    known, else 12 weeks at the first week's ration is not guessed: that case is then left out.
    """
    ration = _d((fc["basis"]["profile"] or [{}])[0].get("sachets"))
    carton = Decimal((item.base_per_pack if item is not None and item.base_per_pack else None) or FULL_CARTON_SACHETS)
    high = _d(fc["basis"]["course"].get("high"))
    scenarios = [("carton", f"Every remaining child needs a full carton ({carton:,.0f} sachets)", carton)]
    if high:
        scenarios.append(
            ("max_weeks", f"Every remaining child needs the maximum {MAX_WEEKS} weeks ({high:,.0f} sachets)", high)
        )
    out = []
    open_cases = [c for c in cases if c.status == "open"]
    for key, label, course in scenarios:
        owed = sum((max(course - c.received, ZERO) for c in open_cases), ZERO)
        need_new = course * children["to_enrol"]
        total = owed + need_new
        out.append(
            {
                "key": key,
                "label": label,
                "course": course,
                "owed": owed,
                "need_new": need_new,
                "total_need": total,
                "held": held,
                "covers": held >= total,
                "to_buy": max(ZERO, total - held),
                "to_buy_with_orders": max(ZERO, total - held - on_order),
                "on_order": on_order,
                "ration": ration,
            }
        )
    return out


def next_tender(program_id, fc: dict, today: date) -> dict:
    """When to start the next tender: when stock runs out at the current pace, less delivery and reply time.

    The delivery time is the longest lead time a live quote states on the latest tender, else that
    tender's own expectation (`procurement.services.timing`, the same count the tender page shows). Where
    there is none, no start date is invented.
    """
    from connect_labs.supply_chain.models import Tender
    from connect_labs.supply_chain.procurement.services import timing as counting

    dry = fc["programme"]["runs_dry_on"]
    weeks = fc["horizon_weeks"]
    out = {"runs_out_on": dry, "horizon_weeks": weeks, "lead_days": None, "reply_days": None, "start_by": None}
    tenders = Tender.objects.filter(program_id=program_id).order_by("-created_at")
    for tender in tenders:
        leads = list(
            tender.quotes.filter(voided=False, superseded_by__isnull=True).values_list("lead_time_days", flat=True)
        )
        lead = counting.lead_days(tender, leads)
        if lead is not None:
            out["lead_days"], out["reply_days"] = lead, counting.reply_days(tender)
            break
    if dry is None:
        out["text"] = (
            f"Stock covers the whole {weeks} weeks the forecast looks ahead at current enrolment, so no date to "
            "start the next tender can be given yet."
        )
        return out
    out_day = date.fromisoformat(dry)
    when = f"about {out_day.strftime('%B %Y')}"
    if out["lead_days"] is None:
        out["text"] = (
            f"At current enrolment, stock lasts until {when}. No delivery time is on record to count back with: "
            "say how long suppliers usually need on a tender (or wait for a quote) and the date to start is "
            "worked out."
        )
        return out
    start = counting.start_by(out_day, out["lead_days"], out["reply_days"])
    out["start_by"] = start
    if start < today:
        advice = f"the date to start was {start.strftime('%-d %b %Y')}: start now"
    else:
        advice = f"start the next tender by {start.strftime('%-d %b %Y')}"
    out["text"] = (
        f"At current enrolment, stock lasts until {when}; {advice} "
        f"({out['lead_days']} days for suppliers to deliver and {out['reply_days']} days for them to reply)."
    )
    return out


# ---- stores ---------------------------------------------------------------------------


def no_delivery_reasons(program_id) -> dict[int, dict]:
    """{supply point id: {"text", "href"}} for stores whose partner has been awarded a share and has not ordered it.

    "Savanna hasn't ordered yet", linked to the award: the answer to "why is nothing coming?" in words,
    where the table would otherwise say only "none expected".
    """
    from django.urls import reverse

    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.models import Contract, Tender
    from connect_labs.supply_chain.operations import record
    from connect_labs.supply_chain.procurement.services.splits import order_set
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    owner = partner_by_point(program_id)
    reasons: dict[int, dict] = {}
    tenders = Tender.objects.filter(program_id=program_id, status="awarded").prefetch_related("awards")
    for tender in tenders:
        if not tender.partner_splits:
            continue
        award = next(iter(tender.awards.all()), None)
        wire = record(tender)
        contracts = [record(c) for c in Contract.objects.filter(program_id=program_id, tender=tender)]
        waiting = set(order_set(wire, contracts)["waiting_org_ids"])
        names = dict(LabsOrg.objects.filter(pk__in=waiting).values_list("pk", "name"))
        for point_id, org_id in owner.items():
            if org_id in waiting and point_id not in reasons:
                reasons[point_id] = {
                    "text": f"{names.get(org_id, 'The partner')} hasn't ordered yet",
                    "href": reverse("supply_chain:award_detail", args=[award.pk]) if award else "",
                }
    return reasons


def store_rows(
    fc: dict, weeks=4, reasons: dict | None = None, owner=None, today=None, status_labels: dict | None = None
) -> list[dict]:
    """Each store: what the workers below it will need, what they already hold, what to send, and whether it lasts.

    Every row adds up: the workers below need `need_ahead` over the weeks ahead; they already hold
    `below_hold` of it; the store must supply `to_supply`; it holds `own`; so it must be sent `send`
    (nothing when it already holds enough, counting the deliveries expected within those weeks).
    """
    owner = owner or {}
    # `reasons` ({point id: {"text", "href"}}, from `no_delivery_reasons`) says why a store has nothing coming.
    reasons = reasons or {}
    next_delivery: dict[int, str] = {}
    arriving: dict[int, Decimal] = {}
    horizon_end = date.fromisoformat(fc["anchor"]) + timedelta(days=7 * weeks) if fc.get("anchor") else None
    for order in fc["programme"]["inbound"]:
        if order["expected_on"] and not order["overdue"]:
            current = next_delivery.get(order["supply_point_id"])
            if current is None or order["expected_on"] < current:
                next_delivery[order["supply_point_id"]] = order["expected_on"]
            if horizon_end is None or date.fromisoformat(order["expected_on"]) <= horizon_end:
                arriving[order["supply_point_id"]] = arriving.get(order["supply_point_id"], ZERO) + (
                    _d(order.get("quantity")) or ZERO
                )
    rows = []
    for s in fc["stores"]:
        dry = s["runs_dry_on"]
        arrives = next_delivery.get(s["supply_point_id"])
        if arrives is None:
            lasts = "no delivery planned" if dry else "no shortfall in the weeks ahead"
        else:
            lasts = "yes" if dry is None or dry >= arrives else "no"
        need_ahead = sum((_d(v) or ZERO for v in s["need"][:weeks]), ZERO)
        demand = s.get("demand_from_below") or []
        to_supply = (_d(demand[min(weeks, len(demand)) - 1]) if demand else None) or ZERO
        own = _d(s["own_on_hand"]) or ZERO
        coming = arriving.get(s["supply_point_id"], ZERO)
        send = max(ZERO, to_supply - own - coming)
        org = owner.get(s["supply_point_id"])
        rows.append(
            {
                **s,
                "org_id": org,
                "need_ahead": need_ahead,
                "below_hold": max(ZERO, need_ahead - to_supply),
                "to_supply": to_supply,
                "own": own,
                "arriving_in_weeks": coming,
                "send": send,
                "send_needed": send > 0,
                "next_delivery": arrives,
                "runs_dry_text": run_out_text(dry, today, fc.get("data_to"))["text"],
                # The orders on their way here, each with the one status label (standing.order_status_labels).
                "incoming": [
                    {
                        "reference": o.get("reference") or "",
                        "expected_on": o.get("expected_on"),
                        "status": (status_labels or {}).get(o.get("contract_id"), ""),
                    }
                    for o in fc["programme"]["inbound"]
                    if o["supply_point_id"] == s["supply_point_id"]
                ],
                "lasts_until_delivery": lasts,
                "no_delivery_reason": None if arrives else reasons.get(s["supply_point_id"]),
                "unit": fc.get("unit") or "",
            }
        )
    rows.sort(key=lambda r: (r["runs_dry_on"] or "9999", r["name"]))
    return rows


def group_stores(program_id, rows: list[dict]) -> list[dict]:
    """The store rows by the partner that runs each, partners in name order."""
    from connect_labs.labs.models import LabsOrg

    names = dict(LabsOrg.objects.filter(pk__in={r["org_id"] for r in rows if r["org_id"]}).values_list("pk", "name"))
    groups: dict = {}
    for row in rows:
        group = groups.setdefault(
            row["org_id"],
            {"org_id": row["org_id"], "name": names.get(row["org_id"], "No partner recorded"), "rows": []},
        )
        group["rows"].append(row)
    return sorted(groups.values(), key=lambda g: g["name"].lower())


# ---- the dosing check ------------------------------------------------------------------


def protocol_by_worker(program_id, item, rules, anchor, unit) -> dict:
    """{supply point id: {"given", "protocol", "visits"}} at the app's own protocol amount, where it is read.

    Only visits that answer at a rule's `protocol_paths` count, and only against what that visit gave out.
    The app's amount already subtracts what the household still has, so a worker is not marked short for a
    child who had sachets left. Empty when no rule names the path (or no visit answered it yet).
    """
    from connect_labs.supply_chain.models import WorkerVisit
    from connect_labs.supply_chain.stock.services.dispensing import household_paths, read_number
    from connect_labs.supply_chain.stock.services.flow import _first_kept
    from connect_labs.supply_chain.stock.services.visit_reader import REVERSING_STATUSES

    out: dict = {}
    for rule in rules:
        paths = household_paths(rule).get("protocol_paths")
        if not paths:
            continue
        kept = (
            WorkerVisit.objects.filter(
                program_id=program_id, opportunity_id=rule.opportunity_id, visit_date__lte=anchor
            )
            .exclude(status__in=REVERSING_STATUSES)
            .exclude(entity_id="")
        )
        sachets = forecasting._sachets_by_visit(program_id, item, kept.values("visit_id"), unit, anchor)
        for visit_id, point_id, answers in kept.values_list("visit_id", "supply_point_id", "answers"):
            amount = read_number(_first_kept(answers or {}, paths))
            if amount is None or visit_id not in sachets:
                continue
            row = out.setdefault(point_id, {"given": ZERO, "protocol": ZERO, "visits": 0})
            row["given"] += sachets[visit_id]
            row["protocol"] += amount
            row["visits"] += 1
    return out


def _verdict(share):
    if share is None:
        return "not enough children seen twice yet"
    if share < 100 - DOSE_TOLERANCE_PERCENT:
        return "below the protocol"
    if share > 100 + DOSE_TOLERANCE_PERCENT:
        return "above the protocol"
    return "in line with the protocol"


def dosing_rows(program_id, item, fc: dict, cases=None) -> list[dict]:
    """Each worker's dosing against the protocol, from their children's visits.

    The dashboards' dosing check. Where the app's own protocol amount is read (`protocol_paths` on the
    rule), what was given is compared with that, which allows for sachets a household still had, so only
    real gaps are flagged (`basis` "app"). Where it is not, the real daily dose is compared with the
    protocol's flat daily dose (`basis` "flat"), and the page says so. A worker with too few children seen
    twice is listed as not enough yet, never as fine.
    """
    protocol = _d((item.commodity.course_definition or {}).get("base_units_per_day")) if item.commodity_id else None
    if protocol is None:
        return []
    rules = list(DispensingRule.objects.filter(program_id=program_id, item=item, status="active").exclude(cases={}))
    if not rules:
        return []
    anchor = date.fromisoformat(fc["anchor"])
    if cases is None:
        cases = forecasting.read_cases(program_id, item, rules, anchor, fc["unit"])
    by_worker: dict[int, list] = {}
    for case in cases:
        by_worker.setdefault(case.supply_point_id, []).append(case)
    from_app = protocol_by_worker(program_id, item, rules, anchor, fc["unit"])
    out = []
    for w in fc["workers"]:
        measured = forecasting.delivered_ration(by_worker.get(w["supply_point_id"], []), protocol)
        app = from_app.get(w["supply_point_id"])
        if app and app["visits"] >= forecasting.K_MIN and app["protocol"] > 0:
            share = int((app["given"] / app["protocol"] * 100).quantize(Decimal(1)))
            basis = "app"
        else:
            share = measured["share_of_protocol"] if measured else None
            basis = "flat"
        verdict = _verdict(share)
        out.append(
            {
                "supply_point_id": w["supply_point_id"],
                "name": w["name"],
                # Children seen at least twice (a dose needs two visits), not those in treatment now.
                "children": measured["children"] if measured else 0,
                "in_treatment": w["open_cases"],
                "per_day": _d(measured["per_day"]) if measured else None,
                "protocol_per_day": protocol,
                "share": share,
                "basis": basis,
                "verdict": verdict,
                "flag": verdict in ("below the protocol", "above the protocol"),
            }
        )
    out.sort(key=lambda r: (not r["flag"], r["name"]))
    return out


# ---- everything the pages and the operation read ----------------------------------------


def _order_labels(program_id, fc: dict) -> dict:
    """The status label of every order on its way to a store, from the one place that words it."""
    from connect_labs.supply_chain.standing import order_status_labels

    return order_status_labels(program_id, [o.get("contract_id") for o in fc["programme"]["inbound"]])


def compute(program_id, item, *, on_date=None, horizon_weeks=8, course_size=None, opportunity_ids=None) -> dict:
    """The whole planning picture for one item, as the saved plan shapes it.

    `opportunity_ids` narrows it to those opportunities (a handout is planned for one); None is all.
    """
    plan = saved_plan(program_id, item)
    values = plan_values(plan)
    today = on_date or date.today()

    def run(weeks):
        return forecasting.forecast(
            program_id,
            item,
            on_date=on_date,
            horizon_weeks=weeks,
            course_size_fallback=course_size,
            enrolment_per_week=values["enrolment_per_flw_per_week"]["value"],
            opportunity_ids=opportunity_ids,
        )

    fc = run(horizon_weeks)
    anchor = date.fromisoformat(fc["anchor"])
    rules = list(DispensingRule.objects.filter(program_id=program_id, item=item, status="active").exclude(cases={}))
    if opportunity_ids is not None:
        rules = [r for r in rules if r.opportunity_id in set(opportunity_ids)]
    cases = forecasting.read_cases(program_id, item, rules, anchor, fc["unit"]) if rules else []
    window = handout_window(program_id, anchor, today)
    cadence = cadence_by_point(program_id)
    longest = max(cadence.values(), default=DEFAULT_HANDOUT_EVERY_DAYS)
    # The forecast must reach past the next handout after this one.
    needed_weeks = math.ceil((window["offset_days"] + longest) / 7) + 1
    if needed_weeks > fc["horizon_weeks"]:
        fc = run(needed_weeks)
    appetite = appetite_per_week(program_id, item, anchor)
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    stores = store_rows(
        fc,
        reasons=no_delivery_reasons(program_id),
        owner=partner_by_point(program_id),
        today=today,
        status_labels=_order_labels(program_id, fc),
    )
    rows = issue_rows(
        fc,
        values,
        offset_days=window["offset_days"],
        cadence=cadence,
        appetite=appetite,
        handout_on=window["handout_on"],
        today=today,
    )
    return {
        "forecast": fc,
        "as_of_note": as_of_note(fc),
        "household": flowing.household(program_id, item, anchor),
        "plan": values,
        "handout": {
            "on": window["handout_on"].isoformat(),
            "planned": window["planned"],
            "data_to": fc["data_to"],
            "next_on": (
                min((window["handout_on"] + timedelta(days=r["window_days"])).isoformat() for r in rows)
                if rows
                else None
            ),
        },
        "issue": rows,
        "issue_groups": group_rows(program_id, rows),
        "issue_total": sum(r["to_issue"] for r in rows),
        "short": running_short(rows),
        "break_even": break_even(fc, values, cases=cases, item=item, program_id=program_id, today=today),
        "recent_rate": recent_enrolment(fc),
        "stores": stores,
        "store_groups": group_stores(program_id, stores),
        "dosing": dosing_rows(program_id, item, fc, cases=cases),
    }


# ---- a partner sees their own --------------------------------------------------------


def partner_point_ids(access, program_id) -> set[int] | None:
    """The supply points a partner may see on the plan pages, or None for the programme itself.

    The programme (its running organisation's members, and platform support) sees everything.
    Anyone else holds the programme as a partner and sees only the places their own organisations
    run: a store managed by an organisation of theirs, the field workers and facilities below it
    (`issues.partner_by_point`: a point takes the nearest organisation above it). Fails closed: a
    caller whose organisations cannot be established sees no place at all.

    This scopes the plan pages and `stock_plan_view` only. Network, Stock, Workers and the other
    operations still read the whole programme to any partner who holds it.
    """
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.identity import caller_org_ids
    from connect_labs.supply_chain.runner import standing
    from connect_labs.supply_chain.stock.services import issues

    if standing(access).is_programme:
        return None
    try:
        connect_ids = caller_org_ids(access) or set()
    except Exception:  # noqa: BLE001 -- unknowable membership grants nothing
        connect_ids = set()
    mine = set(LabsOrg.objects.filter(connect_organization_id__in=connect_ids).values_list("pk", flat=True))
    if not mine:
        return set()
    return {pk for pk, org in issues.partner_by_point(program_id).items() if org in mine}


def group_rows_scoped(groups: list[dict], points: set[int]) -> list[dict]:
    """The grouped rows cut to the workers in `points`, dropping places and partners left empty."""
    out = []
    for partner in groups:
        places = []
        for place in partner["places"]:
            rows = [r for r in place["rows"] if r["supply_point_id"] in points]
            if rows:
                places.append({**place, "rows": rows, "to_issue": sum(r["to_issue"] for r in rows)})
        if places:
            out.append({**partner, "places": places, "to_issue": sum(p["to_issue"] for p in places)})
    return out


def scope_to(picture: dict, points: set[int]) -> dict:
    """The picture cut to a partner's own places. Programme-wide answers (the break-even) are withheld."""
    issue = [r for r in picture["issue"] if r["supply_point_id"] in points]
    return {
        **picture,
        "scoped": True,
        "issue": issue,
        "issue_groups": group_rows_scoped(picture["issue_groups"], points),
        "issue_total": sum(r["to_issue"] for r in issue),
        "short": [r for r in picture["short"] if r["supply_point_id"] in points],
        "stores": [s for s in picture["stores"] if s["supply_point_id"] in points],
        "store_groups": [
            {**g, "rows": [r for r in g["rows"] if r["supply_point_id"] in points]}
            for g in picture["store_groups"]
            if any(r["supply_point_id"] in points for r in g["rows"])
        ],
        "dosing": [d for d in picture["dosing"] if d["supply_point_id"] in points],
        "break_even": None,
        "household": None,
    }
