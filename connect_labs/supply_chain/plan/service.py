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
  - what Supply's records say they hold
  = to issue (never below nothing, in whole sachets)

A child leaves the list when their course ends: the forecast stops supplying a child at
the course size (the expected course once enough children have finished, else the
protocol's), which is the dashboards' "exits from recovery or visit 12".
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal

from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.plan.models import StockPlan
from connect_labs.supply_chain.stock.services import forecast as forecasting

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
        "target_cases": {
            "value": target,
            "default": DEFAULT_TARGET_CASES,
            "default_text": INPUTS["target_cases"]["default_text"],
            "is_default": target == DEFAULT_TARGET_CASES,
        },
    }


def _buffer_for(plan_vals, point_id) -> int:
    return int(plan_vals["buffer_days_by_point"].get(str(point_id), plan_vals["buffer_days"]["value"]))


# ---- what to issue ---------------------------------------------------------------


def _step(label, value, source, sign=""):
    return {"label": label, "value": value, "source": source, "sign": sign}


def issue_rows(fc: dict, plan_vals: dict) -> list[dict]:
    """One row per field worker: the steps to what to issue, and the day they run dry."""
    ration = _d((fc["basis"]["profile"] or [{}])[0].get("sachets"))
    rate_source = (
        "the plan's rate"
        if plan_vals["enrolment_per_flw_per_week"]["value"] is not None
        else "this worker's own recent rate from their visits"
    )
    rows = []
    for w in fc["workers"]:
        known = w["stock_known"]
        held = _d(w["on_hand"]) if known else None
        week = w["weeks"][0]
        due, new = _d(week["committed"]) or ZERO, _d(week["new"]) or ZERO
        days = _buffer_for(plan_vals, w["supply_point_id"])
        buffer = (due + new) * Decimal(days) / 7
        want = due + new + buffer
        issue = _whole_up(want - (held or ZERO)) if known else _whole_up(want)
        short = bool(known and held < due + new)
        if w["basis"] == "cases":
            children = w["open_cases"]
            steps = [
                _step(
                    "Children due a follow-up",
                    f"{children} children, {_plain(due)} sachets for the rest of their course this week",
                    "their visits: children enrolled and not yet finished (the case rule)",
                ),
                _step(
                    "New children expected",
                    f"{_plain(_d(w['enrolled_per_week']), 1)} a week, {_plain(new)} sachets",
                    f"{rate_source}, times the first week's ration of {_plain(ration)} sachets",
                    "+",
                ),
                _step("Appetite tests", "not counted", "Supply does not hold appetite tests yet", "+"),
                _step(
                    "Buffer",
                    f"{days} extra {'day' if days == 1 else 'days'}, {_plain(buffer)} sachets",
                    "the plan: "
                    + (
                        "set for this place"
                        if str(w["supply_point_id"]) in plan_vals["buffer_days_by_point"]
                        else "the same for everyone"
                    )
                    + ", as that share of a week's need",
                    "+",
                ),
                _step(
                    "Held now",
                    "not known" if not known else f"{_plain(held)} sachets",
                    "Supply's records: handovers and receipts, less what visits gave out",
                    "-",
                ),
            ]
        else:
            children = None
            steps = [
                _step(
                    "A week at their present pace",
                    f"{_plain(due)} sachets",
                    "no case rule on this opportunity, so the pace of the last weeks of visits",
                ),
                _step(
                    "Buffer",
                    f"{days} extra days, {_plain(buffer)} sachets",
                    "the plan, as that share of a week's need",
                    "+",
                ),
                _step(
                    "Held now",
                    "not known" if not known else f"{_plain(held)} sachets",
                    "Supply's records",
                    "-",
                ),
            ]
        rows.append(
            {
                "supply_point_id": w["supply_point_id"],
                "name": w["name"],
                "basis": w["basis"],
                "children": children,
                "due": due,
                "new": new,
                "buffer_days": days,
                "buffer": buffer,
                "held": held,
                "stock_known": known,
                "to_issue": issue,
                # The forecast's one run-out answer for this worker (forecast.worker_run_out).
                "runs_dry_on": w["run_out"]["date"],
                "run_out_method": w["run_out"]["method"],
                "short": short,
                "steps": steps,
                "result": (f"Issue {issue:,} sachets" if issue else "Nothing to issue: they hold enough for the week"),
            }
        )
    return rows


def running_short(rows: list[dict]) -> list[dict]:
    """Workers who hold less than next week's need, soonest to run dry first."""
    short = [r for r in rows if r["short"]]
    return sorted(short, key=lambda r: (r["runs_dry_on"] or "9999", r["name"]))


# ---- the break-even question -------------------------------------------------------


def break_even(fc: dict, plan_vals: dict) -> dict:
    """Does the stock left cover the children still in treatment and those still to come, up to the target?"""
    programme = fc["programme"]
    course = _d(fc["basis"]["course"]["size"])
    ration = _d((fc["basis"]["profile"] or [{}])[0].get("sachets"))
    held = _d(programme["on_hand"]) or ZERO
    owed = _d(programme.get("owed"))
    seen = programme.get("children_seen") or 0
    target = plan_vals["target_cases"]["value"]
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


# ---- stores ---------------------------------------------------------------------------


def store_rows(fc: dict, weeks=4) -> list[dict]:
    """Each store: what the workers below it will need, whether it lasts until its next delivery, and for how long."""
    next_delivery: dict[int, str] = {}
    for order in fc["programme"]["inbound"]:
        if order["expected_on"] and not order["overdue"]:
            current = next_delivery.get(order["supply_point_id"])
            if current is None or order["expected_on"] < current:
                next_delivery[order["supply_point_id"]] = order["expected_on"]
    rows = []
    for s in fc["stores"]:
        dry = s["runs_dry_on"]
        arrives = next_delivery.get(s["supply_point_id"])
        if arrives is None:
            lasts = "no delivery planned" if dry else "no shortfall in the weeks ahead"
        else:
            lasts = "yes" if dry is None or dry >= arrives else "no"
        rows.append(
            {
                **s,
                "need_ahead": sum((_d(v) or ZERO for v in s["need"][:weeks]), ZERO),
                "next_delivery": arrives,
                "lasts_until_delivery": lasts,
            }
        )
    rows.sort(key=lambda r: (r["runs_dry_on"] or "9999", r["name"]))
    return rows


# ---- the dosing check ------------------------------------------------------------------


def dosing_rows(program_id, item, fc: dict) -> list[dict]:
    """Each worker's real daily dose against the protocol's, from their children's visits.

    The dashboards' dosing check. A worker with too few children seen twice is listed as
    not enough yet, never as fine.
    """
    protocol = _d((item.commodity.course_definition or {}).get("base_units_per_day")) if item.commodity_id else None
    if protocol is None:
        return []
    rules = list(DispensingRule.objects.filter(program_id=program_id, item=item, status="active").exclude(cases={}))
    if not rules:
        return []
    anchor = date.fromisoformat(fc["anchor"])
    cases = forecasting.read_cases(program_id, item, rules, anchor, fc["unit"])
    by_worker: dict[int, list] = {}
    for case in cases:
        by_worker.setdefault(case.supply_point_id, []).append(case)
    out = []
    for w in fc["workers"]:
        measured = forecasting.delivered_ration(by_worker.get(w["supply_point_id"], []), protocol)
        share = measured["share_of_protocol"] if measured else None
        if measured is None:
            verdict = "not enough children seen twice yet"
        elif share < 100 - DOSE_TOLERANCE_PERCENT:
            verdict = "below the protocol"
        elif share > 100 + DOSE_TOLERANCE_PERCENT:
            verdict = "above the protocol"
        else:
            verdict = "in line with the protocol"
        out.append(
            {
                "supply_point_id": w["supply_point_id"],
                "name": w["name"],
                "children": measured["children"] if measured else 0,
                "per_day": _d(measured["per_day"]) if measured else None,
                "protocol_per_day": protocol,
                "share": share,
                "verdict": verdict,
                "flag": verdict in ("below the protocol", "above the protocol"),
            }
        )
    out.sort(key=lambda r: (not r["flag"], r["name"]))
    return out


# ---- everything the pages and the operation read ----------------------------------------


def compute(program_id, item, *, on_date=None, horizon_weeks=8, course_size=None, opportunity_ids=None) -> dict:
    """The whole planning picture for one item, as the saved plan shapes it.

    `opportunity_ids` narrows it to those opportunities (a handout is planned for one); None is all.
    """
    plan = saved_plan(program_id, item)
    values = plan_values(plan)
    fc = forecasting.forecast(
        program_id,
        item,
        on_date=on_date,
        horizon_weeks=horizon_weeks,
        course_size_fallback=course_size,
        enrolment_per_week=values["enrolment_per_flw_per_week"]["value"],
        opportunity_ids=opportunity_ids,
    )
    rows = issue_rows(fc, values)
    return {
        "forecast": fc,
        "plan": values,
        "issue": rows,
        "issue_total": sum(r["to_issue"] for r in rows),
        "short": running_short(rows),
        "break_even": break_even(fc, values),
        "stores": store_rows(fc),
        "dosing": dosing_rows(program_id, item, fc),
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


def scope_to(picture: dict, points: set[int]) -> dict:
    """The picture cut to a partner's own places. Programme-wide answers (the break-even) are withheld."""
    issue = [r for r in picture["issue"] if r["supply_point_id"] in points]
    return {
        **picture,
        "scoped": True,
        "issue": issue,
        "issue_total": sum(r["to_issue"] for r in issue),
        "short": [r for r in picture["short"] if r["supply_point_id"] in points],
        "stores": [s for s in picture["stores"] if s["supply_point_id"] in points],
        "dosing": [d for d in picture["dosing"] if d["supply_point_id"] in points],
        "break_even": None,
    }
