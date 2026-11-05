# flake8: noqa: F811
"""The supervisor's weekly plan: counted from the handout to the next one, grouped by partner and place,
with appetite tests and a dosing check against the app's own protocol amount.

THIS REPOSITORY IS PUBLIC. Invented names and figures; the world is test_forecast's.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Distribution, PartnerProfile, WorkerVisit
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.plan import service
from connect_labs.supply_chain.tests.test_forecast import PROGRAM, TODAY, world  # noqa: F401 -- fixture
from connect_labs.supply_chain.tests.test_stock_plan import _row, page  # noqa: F401 -- fixture
from connect_labs.supply_chain.tests.test_visit_reader import da, rutf, store  # noqa: F401 -- fixtures

pytestmark = pytest.mark.django_db


def _op(name, **payload):
    return call_operation(
        name, SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM), payload
    )


def _view(world, on=TODAY, **kw):
    return _op("stock_plan_view", item_id=world["item"].pk, as_of=on.isoformat(), course_size=150, **kw)


def test_counted_from_today_the_stock_is_what_is_left_after_the_days_since_the_last_visit(world):
    now = _row(_view(world), "worker-acacia")
    later = _view(world, on=TODAY + timedelta(days=7))
    row = _row(later, "worker-acacia")
    assert later["handout"]["on"] == (TODAY + timedelta(days=7)).isoformat()
    assert later["handout"]["data_to"] == TODAY.isoformat()
    assert Decimal(row["held_on_records"]) == Decimal(72)
    assert Decimal(row["held"]) < Decimal(now["held"])  # a week of children's use comes off before the handout
    assert any("used between the last visit data and the handout" in s["source"] for s in row["steps"])


def test_a_planned_handout_sets_the_date_and_the_partners_rhythm_the_next(world):
    week = _row(_view(world), "worker-acacia")["need_week"]
    org = LabsOrg.objects.create(slug="rhythm-org", name="Rhythm Org")
    world["store"].managed_by_org = org
    world["store"].save()
    PartnerProfile.objects.create(program_id=PROGRAM, org=org, handout_every_days=14)
    Distribution.objects.create(
        program_id=PROGRAM,
        opportunity_id=1,
        supply_point=world["store"],
        distributed_on=TODAY + timedelta(days=3),
        source="we_recorded",
    )
    view = _view(world)
    assert view["handout"]["on"] == (TODAY + timedelta(days=3)).isoformat() and view["handout"]["planned"]
    assert view["handout"]["next_on"] == (TODAY + timedelta(days=17)).isoformat()
    row = _row(view, "worker-acacia")
    assert row["window_days"] == 14
    assert Decimal(row["need_week"]) > Decimal(week)  # two weeks of need, not one


def test_rows_are_grouped_by_partner_and_place_with_a_total_each_place_packs(world):
    org = LabsOrg.objects.create(slug="group-org", name="Group Org")
    world["store"].managed_by_org = org
    world["store"].save()
    view = _view(world)
    [partner] = view["issue_groups"]
    assert partner["name"] == "Group Org"
    assert partner["to_issue"] == view["issue_total"] == sum(r["to_issue"] for r in view["issue"])
    assert sum(place["to_issue"] for place in partner["places"]) == partner["to_issue"]
    assert {r["name"] for place in partner["places"] for r in place["rows"]} == {"worker-acacia", "worker-baobab"}


def test_how_long_stock_lasts_after_the_handout_and_whether_it_reaches_the_next(world):
    row = _row(_view(world), "worker-acacia")
    # With no buffer a worker is planned to reach zero as the next handout arrives: it just reaches it.
    assert row["covers_next_handout"] is True and (row["lasts_days"] is None or row["lasts_days"] >= 7)
    assert row["lasts_until"] is None or row["lasts_until"] >= TODAY.isoformat()


def test_appetite_tests_are_counted_where_the_app_records_them(world):
    rule = world["rule"]
    rule.reports = {"household": {"appetite_paths": ["form.appetite"]}}
    rule.save()
    for visit_row in WorkerVisit.objects.filter(program_id=PROGRAM, supply_point=world["acacia"]):
        visit_row.answers = {**(visit_row.answers or {}), "form.appetite": "2"}
        visit_row.save()
    row = _row(_view(world), "worker-acacia")
    step = next(s for s in row["steps"] if s["label"] == "Appetite tests")
    assert step["value"] != "not counted" and Decimal(row["appetite"]) > 0
    assert Decimal(row["need_week"]) == Decimal(row["due"]) + Decimal(row["new"]) + Decimal(row["appetite"])
    baobab = next(s for s in _row(_view(world), "worker-baobab")["steps"] if s["label"] == "Appetite tests")
    assert baobab["value"] == "not counted" or "sachets" in baobab["value"]


@pytest.fixture
def dosed(world):
    world["item"].commodity.course_definition = {"base_units_per_day": "2", "base_units_per_course": 150}
    world["item"].commodity.save()
    return world


def test_without_the_apps_protocol_path_the_check_is_flat_and_says_so(dosed):
    rows = _view(dosed)["dosing"]
    assert rows and {r["basis"] for r in rows} == {"flat"}


def test_with_the_apps_protocol_amount_only_real_gaps_are_flagged(dosed):
    rule = dosed["rule"]
    rule.reports = {"household": {"protocol_paths": ["form.protocol_amount"]}}
    rule.save()
    # The app says each visit should have given what it gave: so nobody is below the protocol.
    for visit_row in WorkerVisit.objects.filter(program_id=PROGRAM, supply_point=dosed["acacia"]).exclude(
        entity_id=""
    ):
        visit_row.answers = {**(visit_row.answers or {}), "form.protocol_amount": "14"}
        visit_row.save()
    rows = {r["name"]: r for r in _view(dosed)["dosing"]}
    acacia = rows["worker-acacia"]
    assert acacia["basis"] in ("app", "flat")
    if acacia["basis"] == "app":
        assert acacia["share"] is not None and not acacia["flag"] or acacia["share"] < 90


def test_the_supervisor_page_loads_with_groups_buffer_and_handout_dates(page, world):
    PartnerProfile.objects.create(
        program_id=PROGRAM, org=LabsOrg.objects.create(slug="page-org", name="Page Org"), handout_every_days=7
    )
    html = page.get(reverse("supply_chain:plan")).content.decode()
    assert 'data-testid="plan-handout"' in html and "Handout on" in html and "Next handout after it" in html
    assert "Buffer:" in html and "0 days" in html and ">change<" in html
    assert "Pack" in html and "Lasts after the handout" in html
    assert "To issue in all</td>" not in html and "Runs out</th>" not in html
    assert "(the case rule)" not in html
    assert "s visits" in html


# ---- store manager view ---------------------------------------------------------


def test_each_store_row_adds_up_and_says_what_to_send(world):
    [store] = [s for s in _view(world)["stores"] if s["supply_point_id"] == world["store"].pk]
    need, below, supply = Decimal(store["need_ahead"]), Decimal(store["below_hold"]), Decimal(store["to_supply"])
    assert need - below == supply  # what they need, less what workers already hold, is what the store supplies
    own = Decimal(store["own"])
    assert Decimal(store["send"]) == max(Decimal(0), supply - own - Decimal(store["arriving_in_weeks"]))
    assert store["unit"]


def test_stores_are_grouped_by_the_partner_that_runs_them(world):
    org = LabsOrg.objects.create(slug="store-org", name="Store Org")
    world["store"].managed_by_org = org
    world["store"].save()
    groups = _view(world)["store_groups"]
    assert [g["name"] for g in groups] == ["Store Org"] and groups[0]["rows"]


def test_the_stores_page_loads_with_groups_units_and_the_send_column(page, world):
    html = page.get(reverse("supply_chain:plan_stores")).content.decode()
    assert "Store restocking" in html and "They need in 4 weeks" in html and "Store must supply" in html
    assert "Send</th>" in html and "sachet" in html


# ---- running organisation view -----------------------------------------------------


def test_the_running_view_starts_from_the_children_and_answers_two_cases(world):
    be = _view(world, horizon_weeks=26)["break_even"]
    children = be["children"]
    assert children["seen"] == 5 and children["in_treatment"] == 3 and children["exited"] == 1
    assert children["to_enrol"] == children["target"] - children["seen"] == 495
    carton = next(c for c in be["cases"] if c["key"] == "carton")
    assert Decimal(carton["course"]) == 150
    assert Decimal(carton["need_new"]) == 150 * 495
    assert Decimal(carton["total_need"]) == Decimal(carton["owed"]) + Decimal(carton["need_new"])
    assert carton["covers"] is False and Decimal(carton["to_buy"]) > 0


def test_the_next_tender_gets_a_start_date_counted_back_from_when_stock_runs_out(world):
    from connect_labs.supply_chain.models import Tender

    Tender.objects.create(program_id=PROGRAM, label="Earlier", lead_time_days_expected=60)
    nt = _view(world, horizon_weeks=26)["break_even"]["next_tender"]
    assert nt["lead_days"] == 60 and nt["reply_days"] == 14
    if nt["runs_out_on"]:
        assert nt["start_by"] and "start" in nt["text"] and "stock lasts until about" in nt["text"]
    else:
        assert "no date to start" in nt["text"]


def test_the_running_page_loads_with_children_cases_and_commas(page, world):
    PartnerProfile.objects.filter(program_id=PROGRAM).delete()
    html = page.get(reverse("supply_chain:plan_programme")).content.decode()
    assert 'data-testid="plan-children"' in html and "still to enrol" in html
    assert 'data-testid="plan-case-carton"' in html and "full carton (150 sachets)" in html
    assert "495" in html and "74,250" in html  # 150 x 495, with its comma
    assert 'data-testid="plan-next-tender"' in html
