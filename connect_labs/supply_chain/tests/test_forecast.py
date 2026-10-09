"""The stock forecast: children in treatment and those still to enrol, against the stock where it is.

THIS REPOSITORY IS PUBLIC. Every username, id and figure here is invented.

The world, read on Monday 28 September 2026 (TODAY), the day of the last visit:

  Partner store: 500 sachets in on 10 Aug, 200 out to worker-acacia and 50 to
  worker-baobab on 18 Aug -- 250 left.

  worker-acacia (on hand 200 - 128 given out = 72):
    child-a  enrolled at Screening 8 Sep (10), followed up 15 and 22 Sep (14
             each) -- open, received 38. A follow-up on 27 Sep was REJECTED:
             its 14 never count.
    child-b  enrolled 1 Sep, recovered 22 Sep -- exited, received 52.
    child-c  enrolled 19 Aug, last seen 26 Aug -- lost (33 days, over 21).
    child-d  followed up 25 Sep with no Screening in the data -- an open
             carry-over case, received 14.
    child-e  screened 18 Sep and NOT enrolled -- not a case.
  worker-baobab (on hand 50 - 10 = 40):
    child-f  enrolled at Screening 28 Sep (10) -- open, received 10.

Too few cases for anything to be measured (K_MIN is 5), no protocol on the
commodity, and the caller's course is 150: every week of treatment is a tenth
of it, 15 sachets. Each worker enrolled one child in the last three weeks, so
a third of a child a week is projected; a cohort enrolling in week i needs 5
sachets in each week from i on.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from connect_labs.supply_chain.models import Movement, SupplyPoint
from connect_labs.supply_chain.stock.services import belief, forecast
from connect_labs.supply_chain.stock.services.dispensing import validate_cases
from connect_labs.supply_chain.stock.services.flow import flow
from connect_labs.supply_chain.tests.test_visit_reader import (  # noqa: F401 -- fixtures
    PROGRAM,
    RUTF_LINES,
    RUTF_PATH,
    _rule,
    da,
    read,
    rutf,
    store,
    visit,
)
from connect_labs.supply_chain.values import Quantity

pytestmark = pytest.mark.django_db

TODAY = date(2026, 9, 28)
ENROL = "form.screening_outcome.rutf_enrollment"
OUTCOME = "form.case_state.outcome_value"
CASES = {
    "enrol": {"forms": ["Screening"], "path": ENROL, "equals": "yes"},
    "outcome": {"path": OUTCOME, "open": ["enrolled"], "exit": ["recovered"], "complete": ["recovered"]},
    "lost_after_days": 21,
}
COURSE = Decimal(150)


def _screening(vid, on, child, enrolled, given=None, username="worker-acacia"):
    answers = {ENROL: "yes" if enrolled else "no"}
    if given is not None:
        answers[RUTF_PATH] = str(given)
    return visit(vid, on=on, name="Screening ", entity_id=child, username=username, user_id=f"uuid-{username}",
                 answers=answers)  # fmt: skip


def _follow_up(vid, on, child, given, outcome="enrolled", username="worker-acacia", **extra):
    return visit(vid, on=on, name="Visit Form", entity_id=child, username=username, user_id=f"uuid-{username}",
                 answers={OUTCOME: outcome, RUTF_PATH: str(given)}, **extra)  # fmt: skip


def _move(item, kind, quantity, on, frm=None, to=None):
    Movement.objects.create(
        program_id=PROGRAM,
        kind=kind,
        occurred_on=on,
        from_supply_point=frm,
        to_supply_point=to,
        item=item,
        commodity=item.commodity,
        quantity=Decimal(quantity),
        quantity_unit="sachet",
        source="we_recorded",
    )


@pytest.fixture
def world(da, rutf, store):  # noqa: F811
    rule = _rule(rutf, store, RUTF_LINES, cases=validate_cases(CASES))
    _move(rutf, "receipt", 500, date(2026, 8, 10), to=store)
    read(
        da,
        [
            _screening(1, "2026-09-08", "child-a", True, 10),
            _follow_up(2, "2026-09-15", "child-a", 14),
            _follow_up(3, "2026-09-22", "child-a", 14),
            _follow_up(4, "2026-09-27", "child-a", 14, status="rejected"),
            _screening(5, "2026-09-01", "child-b", True, 10),
            _follow_up(6, "2026-09-08", "child-b", 14),
            _follow_up(7, "2026-09-15", "child-b", 14),
            _follow_up(8, "2026-09-22", "child-b", 14, outcome="recovered"),
            _screening(9, "2026-08-19", "child-c", True, 10),
            _follow_up(10, "2026-08-26", "child-c", 14),
            _follow_up(11, "2026-09-25", "child-d", 14),
            _screening(12, "2026-09-18", "child-e", False),
            _screening(13, "2026-09-28", "child-f", True, 10, username="worker-baobab"),
        ],
    )
    acacia = SupplyPoint.objects.get(connect_username="worker-acacia")
    baobab = SupplyPoint.objects.get(connect_username="worker-baobab")
    _move(rutf, "distribution", 200, date(2026, 8, 18), frm=store, to=acacia)
    _move(rutf, "distribution", 50, date(2026, 8, 18), frm=store, to=baobab)
    return {"rule": rule, "item": rutf, "store": store, "acacia": acacia, "baobab": baobab}


def _run(world, **kwargs):
    kwargs.setdefault("on_date", TODAY)
    kwargs.setdefault("course_size_fallback", COURSE)
    return forecast.forecast(PROGRAM, world["item"], **kwargs)


def _worker(out, name):
    return next(w for w in out["workers"] if w["name"] == name)


def _store(out, world):
    return next(s for s in out["stores"] if s["supply_point_id"] == world["store"].pk)


def _needs(row):
    return [Decimal(week["need"]) for week in row["weeks"]]


def test_the_world_reads_as_described(world):
    on_hand = {b.point.name: b.on_hand.amount for b in belief.worker_beliefs(PROGRAM, world["item"], on_date=TODAY)}
    assert on_hand == {"worker-acacia": Decimal(72), "worker-baobab": Decimal(40)}


def test_open_cases_and_what_they_are_still_owed(world):
    out = _run(world)

    acacia, baobab = _worker(out, "worker-acacia"), _worker(out, "worker-baobab")
    assert (acacia["open_cases"], acacia["carry_over_cases"], acacia["owed"]) == (2, 1, "248")  # 112 + 136
    assert (baobab["open_cases"], baobab["owed"]) == (1, "140")
    assert (out["programme"]["open_cases"], out["programme"]["owed"]) == (3, "388")


def test_exited_and_lost_cases_owe_nothing_and_an_unenrolled_child_is_no_case(world):
    out = _run(world)

    cohorts = {c["week_of"]: (c["children"], c["carry_over"], c["owed"]) for c in out["cohorts"]}
    # child-a (week of 7 Sep), child-d (21 Sep, carry-over), child-f (28 Sep); never b, c or e.
    assert cohorts == {"2026-09-07": (1, 0, "112"), "2026-09-21": (1, 1, "136"), "2026-09-28": (1, 0, "140")}


def test_a_rejected_visits_sachets_are_not_received(world):
    out = _run(world)

    # child-a received 10 + 14 + 14; the rejected 27 Sep follow-up would have made it 52 (owed 98).
    assert next(c for c in out["cohorts"] if c["week_of"] == "2026-09-07")["owed"] == "112"


def test_committed_follows_the_profile_and_stops_at_the_course(world):
    out = _run(world)

    committed = [Decimal(week["committed"]) for week in _worker(out, "worker-acacia")["weeks"]]
    # child-a has 112 left, child-d 136, both at 15 a week: child-a's runs out in week 8.
    assert committed == [Decimal(30)] * 7 + [Decimal(22)]
    assert out["basis"]["course"] == {"size": "150", "basis": "default", "completed_cases": 1}
    assert {p["basis"] for p in out["basis"]["profile"]} == {"default"}


def test_new_children_are_projected_from_each_workers_own_enrolment(world):
    out = _run(world)

    acacia = _worker(out, "worker-acacia")
    assert (acacia["enrolled_per_week"], acacia["enrolment_weeks"]) == ("0.3", 3)
    assert [Decimal(week["new"]) for week in acacia["weeks"]] == [Decimal(5 * (i + 1)) for i in range(8)]
    assert out["programme"]["enrolled_per_week"] == "0.7"


def test_too_little_history_projects_no_new_children(world):
    world["rule"].active_from = date(2026, 9, 20)  # only the last seven days lie inside the rule's life
    world["rule"].save()

    acacia = _worker(_run(world), "worker-acacia")

    assert (acacia["enrolled_per_week"], acacia["enrolment_weeks"]) == (None, 1)
    assert {week["new"] for week in acacia["weeks"]} == {"0"}


def test_a_scenario_of_nothing_leaves_only_what_is_committed(world):
    acacia = _worker(_run(world, scenario=0), "worker-acacia")
    assert acacia["need_total"] == "232"


def test_a_worker_runs_dry_the_day_their_need_passes_their_stock(world):
    out = _run(world)

    # acacia: 35 then 40 against 72 -- 37 of the second week's 40 last 7 of its days.
    assert _worker(out, "worker-acacia")["runs_dry_on"] == "2026-10-12"
    # baobab: 20 then 25 against 40.
    assert _worker(out, "worker-baobab")["runs_dry_on"] == "2026-10-11"
    assert [w["name"] for w in out["workers"]] == ["worker-baobab", "worker-acacia"]  # soonest first
    assert _worker(out, "worker-acacia")["shortfall_by_end"] == "340"


def test_a_store_runs_dry_when_what_its_workers_cannot_cover_passes_its_own_stock(world):
    out = _run(world)

    partner = _store(out, world)
    assert [Decimal(v) for v in partner["demand_from_below"]] == [0, 8, 83, 168, 263, 368, 483, 600]
    assert (partner["runs_dry_on"], partner["shortfall_by_end"]) == ("2026-11-02", "350")
    assert Decimal(partner["own_on_hand"]["amount"]) == Decimal(250)
    # With one store at the top, the programme runs dry the same day.
    assert (out["programme"]["runs_dry_on"], out["programme"]["shortfall_by_end"]) == ("2026-11-02", "350")


def test_an_order_on_its_way_counts_at_the_programme_only(world, monkeypatch):
    from connect_labs.supply_chain.stock.services import network

    def inbound(program_id, points, item=None, as_of=None):
        return {
            world["store"].pk: [
                {"contract_id": 1, "reference": "PO-1", "outstanding": Quantity(Decimal(300), "sachet"),
                 "expected_on": date(2026, 10, 20), "overdue": False},
                {"contract_id": 2, "reference": "PO-2", "outstanding": Quantity(Decimal(100), "sachet"),
                 "expected_on": None, "overdue": False},
            ]
        }  # fmt: skip

    monkeypatch.setattr(network, "_expected_inbound", inbound)
    out = _run(world)

    assert _store(out, world)["runs_dry_on"] == "2026-11-02"  # a store's own stock is what it holds
    programme = out["programme"]
    assert [Decimal(v) for v in programme["inbound_by_week"]] == [0, 0, 0, 300, 0, 0, 0, 0]
    assert [(o["reference"], o["counted"]) for o in programme["inbound"]] == [("PO-1", True), ("PO-2", False)]
    # 550 available from week 4: the last week's 600 passes it.
    assert (programme["runs_dry_on"], programme["shortfall_by_end"]) == ("2026-11-21", "50")


def test_visits_that_stop_early_move_the_anchor_back_and_say_so(world):
    out = _run(world, on_date=TODAY + timedelta(days=5))

    assert (out["as_of"], out["anchor"], out["data_to"]) == ("2026-10-03", "2026-09-28", "2026-09-28")
    assert _worker(out, "worker-acacia")["runs_dry_on"] == "2026-10-12"


def test_without_a_case_rule_the_forecast_is_the_workers_pace(world):
    world["rule"].cases = {}
    world["rule"].save()

    out = _run(world)

    assert out["cases_configured"] is False
    acacia = _worker(out, "worker-acacia")
    pace = belief.point_belief(PROGRAM, world["acacia"], world["item"], on_date=TODAY).amc.amount
    assert acacia["basis"] == "pace"
    assert _needs(acacia)[0] == (pace / 30 * 7).quantize(Decimal("0.1"))
    assert (acacia["owed"], acacia["enrolled_per_week"]) == (None, None)


def test_numbers_agree_with_worker_stock_and_stock_flow(world):
    out = _run(world)
    wired = {b.point.pk: belief.wire(b) for b in belief.worker_beliefs(PROGRAM, world["item"], on_date=TODAY)}
    given = {
        link["source"]: link["series"][-1]
        for link in flow(PROGRAM, world["item"], on_date=TODAY)["links"]
        if link["target"] == "given"
    }
    for worker in out["workers"]:
        stock = wired[worker["supply_point_id"]]
        assert (worker["on_hand"], worker["dispensed"]) == (stock["on_hand"], stock["dispensed"])
        assert Decimal(worker["dispensed"]["amount"]) == Decimal(given[f"p{worker['supply_point_id']}"])
    # What the history shows given out is what the ledger posted in those weeks.
    assert sum(Decimal(week["given_out"]) for week in out["history"]) == Decimal(128 + 10)


def test_the_operation_is_a_read_on_the_mcp_catalogue_and_returns_the_forecast(world, da):  # noqa: F811
    from connect_labs.supply_chain.operations import agent_operations, call_operation

    assert "stock_forecast" in agent_operations()
    assert agent_operations()["stock_forecast"].is_write is False

    out = call_operation(
        "stock_forecast", da, {"item_id": world["item"].pk, "as_of": TODAY.isoformat(), "course_size": 150}
    )

    assert _worker(out, "worker-acacia")["runs_dry_on"] == "2026-10-12"
    assert out["basis"]["course"]["basis"] == "default"


def test_the_profile_is_measured_once_enough_children_were_in_treatment_all_week():
    anchor = date(2026, 9, 28)
    cases = []
    for n in range(5):
        start = anchor - timedelta(days=21)
        case = forecast.Case(
            key=(1, f"child-{n}"), opportunity_id=1, supply_point_id=1, enrolled_on=start, enrol_point_id=1,
            carry_over=False, last_seen=start + timedelta(days=14), outcome="enrolled",
        )  # fmt: skip
        case.visits = [
            (start, Decimal(10)),
            (start + timedelta(days=7), Decimal(14)),
            (start + timedelta(days=14), Decimal(14)),
        ]
        cases.append(case)

    profile = forecast.delivery_profile(cases, anchor, COURSE, None)

    assert {k: v for k, (v, _) in profile.measured.items()} == {0: Decimal(10), 1: Decimal(14), 2: Decimal(14)}
    assert (profile.week(5), profile.basis(5)) == (Decimal(14), "steady")


def test_the_course_is_measured_from_completed_cases_once_there_are_enough(world):
    cases = [
        forecast.Case(
            key=(1, f"child-{n}"), opportunity_id=world["rule"].opportunity_id, supply_point_id=1,
            enrolled_on=TODAY, enrol_point_id=1, carry_over=False, last_seen=TODAY, outcome="recovered",
            received=Decimal(received), status="exited",
        )  # fmt: skip
        for n, received in enumerate((100, 120, 140, 150, 160))
    ]

    assert forecast.course_size(cases, [world["rule"]], world["item"], COURSE) == {
        "size": Decimal(140),
        "basis": "measured",
        "completed_cases": 5,
    }


def test_a_protocol_on_the_commodity_sets_the_course_and_the_weekly_profile(world):
    commodity = world["item"].commodity
    commodity.course_definition = {"base_units_per_course": 120, "base_units_per_day": 2}
    commodity.save()

    out = _run(world)

    assert (out["basis"]["course"]["size"], out["basis"]["course"]["basis"]) == ("120", "protocol")
    assert (out["basis"]["profile"][3]["sachets"], out["basis"]["profile"][3]["basis"]) == ("14", "protocol")
