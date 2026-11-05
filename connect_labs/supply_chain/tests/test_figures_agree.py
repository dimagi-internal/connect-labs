# flake8: noqa: F811
"""Round 2, "figures that disagree" (Sophie Feintuch, programme 10172, 5 Nov 2026).

THIS REPOSITORY IS PUBLIC. Every id and figure here is invented. One world, read by every page, so a fact
has one value: the children total, the official stock figure, the household lines, the run-out text.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import (
    DispensingRule,
    Distribution,
    DistributionLine,
    StockCount,
    WorkerVisit,
)
from connect_labs.supply_chain.plan import service
from connect_labs.supply_chain.stock.services import flow, forecast, official_stock
from connect_labs.supply_chain.tests.test_forecast import COURSE, PROGRAM, TODAY, world  # noqa: F401 -- fixture
from connect_labs.supply_chain.tests.test_stock_plan import _op, _row, _view, page  # noqa: F401 -- fixtures
from connect_labs.supply_chain.tests.test_visit_reader import da, rutf, store  # noqa: F401 -- fixtures

pytestmark = pytest.mark.django_db


# ---- the official stock figure -------------------------------------------------


def test_until_a_partners_handovers_are_recorded_the_phone_balance_is_official():
    got = official_stock.official_flw_stock(
        records={"amount": "72", "unit": "sachet"},
        phone={"amount": "50", "unit": "sachet"},
        partner_has_handovers=False,
    )
    assert got["official"] == Decimal(50) and got["source"] == "phone"
    assert got["differs"] and got["difference"] == Decimal(-22)
    assert "phone balance is official" in got["note"] and "phone lower by 22" in got["note"]


def test_once_handovers_are_recorded_supplys_records_are_official():
    got = official_stock.official_flw_stock(records=72, phone=50, partner_has_handovers=True)
    assert got["official"] == Decimal(72) and got["source"] == "records" and got["differs"]


def test_the_other_figure_stands_in_when_only_one_is_known_and_agreement_is_not_flagged():
    only_records = official_stock.official_flw_stock(records=72, phone=None, partner_has_handovers=False)
    assert only_records["official"] == Decimal(72) and only_records["source"] == "records"
    assert "Only Supply’s record is known" in only_records["note"] and not only_records["differs"]
    same = official_stock.official_flw_stock(records=72, phone=Decimal("72.3"), partner_has_handovers=False)
    assert not same["differs"]
    nothing = official_stock.official_flw_stock(records=None, phone=None, partner_has_handovers=False)
    assert nothing["official"] is None and "No figure is known" in nothing["note"]


def _phone_count(world, on, quantity):
    StockCount.objects.create(
        program_id=PROGRAM,
        opportunity_id=world["rule"].opportunity_id,
        supply_point=world["acacia"],
        item=world["item"],
        commodity=world["item"].commodity,
        kind="self_reported",
        counted_on=on,
        quantity=quantity,
        quantity_unit="sachet",
    )


def _confirmed_handout(world):
    run = Distribution.objects.create(
        program_id=PROGRAM,
        opportunity_id=world["rule"].opportunity_id,
        supply_point=world["store"],
        distributed_on=date(2026, 9, 1),
    )
    DistributionLine.objects.create(
        distribution=run,
        to_supply_point=world["acacia"],
        item=world["item"],
        quantity=10,
        quantity_unit="sachet",
        quantity_given=10,
    )
    return run


def test_the_forecast_plans_from_the_phone_until_handovers_are_recorded_then_from_the_records(world):
    _phone_count(world, date(2026, 9, 26), 50)
    fc = forecast.forecast(PROGRAM, world["item"], on_date=TODAY, course_size_fallback=COURSE)
    acacia = next(w for w in fc["workers"] if w["name"] == "worker-acacia")
    assert acacia["official_stock"]["source"] == "phone" and Decimal(acacia["official_stock"]["amount"]) == 50
    assert acacia["official_stock"]["differs"] and Decimal(acacia["official_stock"]["records"]) == 72
    assert official_stock.partners_with_handovers(PROGRAM) == set()

    _confirmed_handout(world)
    assert official_stock.partners_with_handovers(PROGRAM) == {None}
    fc = forecast.forecast(PROGRAM, world["item"], on_date=TODAY, course_size_fallback=COURSE)
    acacia = next(w for w in fc["workers"] if w["name"] == "worker-acacia")
    assert acacia["official_stock"]["source"] == "records" and Decimal(acacia["official_stock"]["amount"]) == 72
    assert Decimal(acacia["official_stock"]["phone"]) == 50  # both are still shown


def test_a_planned_run_that_nobody_confirmed_is_not_a_recorded_handover(world):
    run = _confirmed_handout(world)
    run.lines.update(quantity_given=None)
    assert official_stock.partners_with_handovers(PROGRAM) == set()


def test_the_plan_holds_the_official_figure_and_says_when_the_phone_differs(world):
    _phone_count(world, date(2026, 9, 26), 50)
    row = _row(_view(world), "worker-acacia")
    assert Decimal(row["held"]) == 50 and row["stock_source"] == "phone" and row["stock_differs"]
    held_step = next(s for s in row["steps"] if s["label"] == "Held now")
    assert "phone balance is official" in held_step["source"]


# ---- the children total ----------------------------------------------------------


def test_one_children_total_says_what_each_number_means(world):
    fc = forecast.forecast(PROGRAM, world["item"], on_date=TODAY, course_size_fallback=COURSE)
    kids = service.children_summary(fc)
    assert kids["seen"] == kids["enrolled"] + kids["carried_over"] == 5
    assert kids["in_treatment"] == fc["programme"]["open_cases"]
    assert "5 children seen in all" in kids["text"] and "in treatment now" in kids["text"]
    be = _view(world)["break_even"]
    assert be["children_so_far"] == kids["seen"] and be["children"]["seen"] == kids["seen"]


# ---- the run-out text ------------------------------------------------------------


def test_a_run_out_date_behind_us_is_flagged_and_the_data_day_is_said():
    got = service.run_out_text("2026-10-30", today=date(2026, 11, 5), data_to="2026-10-23")
    assert got["past"] and got["text"] == "30 Oct 2026: may already have run out (figures run to 23 Oct 2026)"
    ahead = service.run_out_text("2026-11-30", today=date(2026, 11, 5))
    assert ahead == {"text": "30 Nov 2026", "past": False}
    assert service.run_out_text(None)["text"] == "not in the weeks ahead"


def test_the_page_says_what_day_the_figures_run_to(world):
    note = _view(world)["as_of_note"]
    assert note["text"].startswith("Based on visits up to ") and note["data_to"]


# ---- the household lines -----------------------------------------------------------


HOUSEHOLD = {
    "returned_paths": ["form.returned"],
    "leftover_paths": ["form.left"],
    "missing_paths": ["form.missing"],
    "misused_paths": ["form.misused"],
    "dispensed_paths": ["form.dispensed"],
    "appetite_paths": ["form.appetite"],
    "reason_paths": ["form.why"],
}


def _answer(point, **answers):
    kept = (
        WorkerVisit.objects.filter(supply_point=point).exclude(status="rejected").order_by("visit_date", "id").first()
    )
    kept.answers = {**kept.answers, **{f"form.{k}": v for k, v in answers.items()}}
    kept.save()


def _household(world):
    DispensingRule.objects.update(reports={"household": HOUSEHOLD})
    _answer(world["baobab"], dispensed="100", returned="40", left="5", missing="10", why="none", appetite="6")
    _answer(world["acacia"], dispensed="50", returned="20", misused="4", why="sold shared_with_family")


def test_the_household_lines_add_up_with_the_latest_rations_as_their_own_line(world):
    _household(world)
    home = flow.household(PROGRAM, world["item"], TODAY)
    assert home["dispensed"] == 150
    assert home["returned"] == 60 and home["leftover"] == 5 and home["missing"] == 10 and home["misused"] == 4
    assert home["checked"] == 79
    assert home["not_yet_checked"] == 150 - 79
    assert (
        home["returned"] + home["leftover"] + home["missing"] + home["misused"] + home["not_yet_checked"]
        == home["dispensed"]
    )
    assert [r["key"] for r in home["rows"]] == [
        "dispensed",
        "returned",
        "leftover",
        "missing",
        "misused",
        "not_yet_checked",
        "appetite",
    ]
    assert home["appetite"] == 6  # separate, not part of the sum


def test_a_caregiver_answering_none_is_a_recorded_zero_and_reasons_are_split(world):
    DispensingRule.objects.update(reports={"household": HOUSEHOLD})
    _answer(world["baobab"], why="none")
    home = flow.household(PROGRAM, world["item"], TODAY)
    assert home["misused"] == 0 and home["no_misuse_visits"] == 1 and home["reasons"] == []
    misused = next(r for r in home["rows"] if r["key"] == "misused")
    assert misused["amount"] == 0 and misused["visits"] == 1

    _answer(world["acacia"], why="sold shared_with_family")
    _answer(world["baobab"], why="sold none")  # "none" beside a real reason is not zero misuse
    home = flow.household(PROGRAM, world["item"], TODAY)
    assert home["reasons"] == [
        {"reason": "sold", "visits": 2},
        {"reason": "shared with family", "visits": 1},
    ]


def test_a_blank_misuse_with_no_answer_stays_not_recorded(world):
    DispensingRule.objects.update(reports={"household": HOUSEHOLD})
    _answer(world["baobab"], returned="3")
    assert flow.household(PROGRAM, world["item"], TODAY)["misused"] is None


def test_each_line_says_how_many_visits_it_rests_on(world):
    _household(world)
    rows = {r["key"]: r for r in flow.household(PROGRAM, world["item"], TODAY)["rows"]}
    assert rows["leftover"]["visits"] == 1 and rows["returned"]["visits"] == 2


def test_appetite_tests_are_a_weekly_average_per_worker_from_the_same_paths(world):
    _household(world)
    by_worker = flow.appetite_by_worker(PROGRAM, world["item"], TODAY, weeks=3)
    assert by_worker == {world["baobab"].pk: Decimal(6) / 3}


def test_the_plan_counts_appetite_tests_and_drops_the_not_counted_text(world):
    _household(world)
    row = _row(_view(world), "worker-baobab")
    assert Decimal(row["appetite"]) == 2
    step = next(s for s in row["steps"] if s["label"] == "Appetite tests")
    assert step["value"] == "2.0 sachets a week" and "not counted" not in step["value"]
    assert Decimal(row["need_week"]) == Decimal(row["due"]) + Decimal(row["new"]) + 2


def test_a_form_that_asks_no_appetite_question_says_so_not_zero(world):
    fc = forecast.forecast(PROGRAM, world["item"], on_date=TODAY, course_size_fallback=COURSE)
    rows = service.issue_rows(fc, service.plan_values(None), appetite=None)
    step = next(s for s in rows[0]["steps"] if s["label"] == "Appetite tests")
    assert step["value"] == "not asked on this form"


# ---- the pages load -----------------------------------------------------------------


def test_the_plan_pages_load_with_the_new_figures(page, world):
    _household(world)
    _phone_count(world, date(2026, 9, 26), 50)
    supervisor = page.get(reverse("supply_chain:plan"))
    assert supervisor.status_code == 200
    html = supervisor.content.decode()
    assert 'data-testid="plan-as-of"' in html and "Based on visits up to" in html
    assert 'data-testid="plan-household"' in html and "Given at the latest visits, not yet checked" in html
    assert 'data-testid="stock-differs"' in html and "phone balance" in html
    assert "Supply does not hold them yet" not in html and "is not here yet" not in html
    stores = page.get(reverse("supply_chain:plan_stores"))
    assert stores.status_code == 200
    programme = page.get(reverse("supply_chain:plan_programme"))
    assert programme.status_code == 200
    assert 'data-testid="plan-children-total"' in programme.content.decode()


def test_a_store_lists_the_orders_on_their_way_with_the_one_status_label():
    fc = {
        "data_to": "2026-10-23",
        "programme": {
            "inbound": [
                {
                    "contract_id": 7,
                    "reference": "PO-RB-1",
                    "supply_point_id": 3,
                    "expected_on": "2026-11-20",
                    "overdue": False,
                },
                {"contract_id": 8, "reference": "PO-X", "supply_point_id": 9, "expected_on": None, "overdue": False},
            ]
        },
        "stores": [
            {"supply_point_id": 3, "name": "Riverbend", "runs_dry_on": "2026-10-23", "need": ["10"], "workers": 1},
        ],
    }
    [row] = service.store_rows(fc, today=date(2026, 11, 5), status_labels={7: "In transit · expected 20 Nov"})
    assert row["incoming"] == [
        {"reference": "PO-RB-1", "expected_on": "2026-11-20", "status": "In transit · expected 20 Nov"}
    ]
    assert row["next_delivery"] == "2026-11-20"
    assert row["runs_dry_text"].startswith("23 Oct 2026: may already have run out")
