# flake8: noqa: F811
"""The stock plan: three saved choices, what to issue each worker, the break-even and who changed what.

THIS REPOSITORY IS PUBLIC. Every id and figure here is invented. The world is test_forecast's, read on
Monday 28 September 2026: worker-acacia holds 72 sachets and has one open child (received 38 of a
150-sachet course) plus a carry-over child (14); worker-baobab holds 40 with one child enrolled that day.
"""

from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import OperationCall
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import forecast
from connect_labs.supply_chain.tests.test_forecast import COURSE, PROGRAM, TODAY, world  # noqa: F401 -- fixture
from connect_labs.supply_chain.tests.test_visit_reader import da, rutf, store  # noqa: F401 -- fixtures

pytestmark = pytest.mark.django_db


def _op(name, **payload):
    return call_operation(
        name, SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM), payload
    )


def _view(world, **kw):
    return _op("stock_plan_view", item_id=world["item"].pk, as_of=TODAY.isoformat(), course_size=150, **kw)


def _row(view, name):
    return next(r for r in view["issue"] if r["name"] == name)


def test_nothing_saved_reads_as_the_defaults_with_no_history(world):
    got = _op("stock_plan_get", item_id=world["item"].pk)
    assert got["saved"] is False and got["history"] == []
    assert got["plan"]["target_cases"] == {"value": 500, "default": 500, "default_text": "500", "is_default": True}
    assert got["plan"]["enrolment_per_flw_per_week"]["is_default"] is True
    assert got["plan"]["buffer_days"]["value"] == 0


def test_a_change_is_saved_with_who_made_it_and_the_default_beside_it(world):
    _op("stock_plan_set", data={"item_id": world["item"].pk, "target_cases": 2500, "buffer_days": 3})
    got = _op("stock_plan_get", item_id=world["item"].pk)
    assert got["saved"] and got["plan"]["target_cases"]["value"] == 2500
    assert got["plan"]["target_cases"]["is_default"] is False
    [entry] = got["history"]
    labels = {c["field"]: c for c in entry["changes"]}
    assert labels["target_cases"]["now"] == 2500 and labels["target_cases"]["default"] == "500"
    assert labels["buffer_days"]["now"] == 3
    assert OperationCall.objects.filter(operation="stock_plan_set", program_id=PROGRAM).count() == 1
    assert entry["who"]  # attributed

    _op("stock_plan_set", data={"item_id": world["item"].pk, "target_cases": 500})
    again = _op("stock_plan_get", item_id=world["item"].pk)
    assert len(again["history"]) == 2 and again["history"][0]["changes"][0]["was"] == 2500


def test_a_buffer_may_name_a_place_that_is_not_in_the_programme_only_if_it_exists(world):
    with pytest.raises(ValueError, match="no such supply point"):
        _op("stock_plan_set", data={"item_id": world["item"].pk, "buffer_days_by_point": {"999999": 2}})


def test_what_to_issue_is_the_week_less_what_the_records_say_is_held(world):
    view = _view(world)
    acacia = _row(view, "worker-acacia")
    fc = forecast.forecast(PROGRAM, world["item"], on_date=TODAY, course_size_fallback=COURSE)
    week = next(w for w in fc["workers"] if w["name"] == "worker-acacia")["weeks"][0]
    need = Decimal(week["committed"]) + Decimal(week["new"])
    assert Decimal(acacia["held"]) == Decimal(72)
    assert int(acacia["to_issue"]) == max(0, int(-(-(need - 72) // 1)))
    labels = [s["label"] for s in acacia["steps"]]
    assert labels == ["Children due a follow-up", "New children expected", "Appetite tests", "Buffer", "Held at the handout"]
    assert all(s["source"] for s in acacia["steps"])  # each number says where it came from


def test_a_buffer_adds_that_many_days_of_need_for_everyone_or_one_place(world):
    base = _row(_view(world), "worker-baobab")
    _op("stock_plan_set", data={"item_id": world["item"].pk, "buffer_days": 7})
    week_buffer = _row(_view(world), "worker-baobab")
    assert Decimal(week_buffer["buffer"]) == Decimal(base["due"]) + Decimal(base["new"])
    assert week_buffer["to_issue"] >= base["to_issue"]
    _op(
        "stock_plan_set",
        data={"item_id": world["item"].pk, "buffer_days_by_point": {str(world["baobab"].pk): 0}},
    )
    assert _row(_view(world), "worker-baobab")["buffer_days"] == 0
    assert _row(_view(world), "worker-acacia")["buffer_days"] == 7


def test_the_enrolment_rate_the_plan_sets_replaces_each_workers_own(world):
    _op("stock_plan_set", data={"item_id": world["item"].pk, "enrolment_per_flw_per_week": 4})
    row = _row(_view(world), "worker-acacia")
    assert Decimal(row["new"]) > Decimal(_row(_view(world, horizon_weeks=8), "worker-acacia")["due"]) * 0 and Decimal(
        row["new"]
    ) == Decimal(4) * Decimal(
        15
    )  # the world's ration is a tenth of the 150-sachet course
    _op("stock_plan_set", data={"item_id": world["item"].pk, "enrolment_per_flw_per_week": None})
    assert _op("stock_plan_get", item_id=world["item"].pk)["plan"]["enrolment_per_flw_per_week"]["is_default"]


def test_who_may_run_short_is_whoever_holds_less_than_next_weeks_need(world):
    view = _view(world)
    short = {r["name"] for r in view["short"]}
    for row in view["issue"]:
        assert (row["name"] in short) == (Decimal(row["held"]) < Decimal(row["due"]) + Decimal(row["new"]))


def test_break_even_counts_the_target_against_the_stock_left_after_whats_owed(world):
    view = _view(world)
    be = view["break_even"]
    assert (
        be["target"] == 500 and be["children_so_far"] == 5
    )  # children a, b, c, d and f; e was screened, not enrolled
    assert Decimal(be["held"]) == Decimal(250) + Decimal(72) + Decimal(40)
    left = Decimal(be["held"]) - Decimal(be["owed"])
    assert Decimal(be["left_for_new"]) == left
    assert be["covers"] is False  # 495 more full courses of 150 is far more than is left
    assert Decimal(be["to_buy"]) == Decimal(be["needed_for_new"]) - left
    assert 0 <= be["visits_per_case"] < be["max_visits"] or be["max_visits"] is None
    assert be["answer"].startswith("No.")


def test_a_target_already_reached_says_so(world):
    _op("stock_plan_set", data={"item_id": world["item"].pk, "target_cases": 1})
    be = _view(world)["break_even"]
    assert be["children_to_come"] == 0 and "already reached" in be["answer"]


def test_a_store_says_whether_it_lasts_until_its_next_delivery(world):
    stores = _view(world)["stores"]
    store = next(s for s in stores if s["supply_point_id"] == world["store"].pk)
    assert store["lasts_until_delivery"] in ("no delivery planned", "no shortfall in the weeks ahead")
    assert store["next_delivery"] is None


# ---- the pages ---------------------------------------------------------------


@pytest.fixture
def page(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.plan import views as plan_views  # noqa: F401
    from connect_labs.supply_chain.stock import visit_views  # noqa: F401

    account = django_user_model.objects.create_user(username="katherine", password="x", email="k@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "plan.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped, raising=False)
    monkeypatch.setattr("connect_labs.supply_chain.views._access", _scoped)
    return client


def test_the_three_pages_render_for_their_people(page, world):
    from django.urls import reverse

    supervisor = page.get(reverse("supply_chain:plan"))
    assert supervisor.status_code == 200
    html = supervisor.content.decode()
    assert "What to issue at the next handout" in html and "worker-acacia" in html
    assert "Who changed what" in html and "default: 500" in html
    stores = page.get(reverse("supply_chain:plan_stores"))
    assert "Store restocking" in stores.content.decode() and "Partner store" in stores.content.decode()
    programme = page.get(reverse("supply_chain:plan_programme"))
    text = programme.content.decode()
    assert "Will we have enough?" in text and "Can we account for every sachet?" in text
    assert "When and how much to buy next" in text


def test_the_change_screen_saves_the_plan_and_an_empty_rate_goes_back_to_recent(page, world):
    from django.urls import reverse

    url = f"{reverse('supply_chain:plan_inputs')}?item_id={world['item'].pk}"
    assert page.get(url).status_code == 200
    done = page.post(url, {"enrolment_per_flw_per_week": "3", "buffer_days": "2", "target_cases": "2500"})
    assert done.status_code == 302
    plan = _op("stock_plan_get", item_id=world["item"].pk)["plan"]
    assert plan["target_cases"]["value"] == 2500 and plan["buffer_days"]["value"] == 2
    assert Decimal(str(plan["enrolment_per_flw_per_week"]["value"])) == 3
    page.post(url, {"enrolment_per_flw_per_week": "", "buffer_days": "2", "target_cases": "2500"})
    assert _op("stock_plan_get", item_id=world["item"].pk)["plan"]["enrolment_per_flw_per_week"]["is_default"]


def test_the_place_screen_sets_and_clears_a_buffer_for_one_place(page, world):
    from django.urls import reverse

    url = f"{reverse('supply_chain:plan_buffer_place')}?item_id={world['item'].pk}"
    page.post(url, {"supply_point": world["acacia"].pk, "days": "5"})
    assert _op("stock_plan_get", item_id=world["item"].pk)["plan"]["buffer_days_by_point"] == {
        str(world["acacia"].pk): 5
    }
    page.post(url, {"supply_point": world["acacia"].pk, "days": ""})
    assert _op("stock_plan_get", item_id=world["item"].pk)["plan"]["buffer_days_by_point"] == {}


# ---- a partner sees their own -----------------------------------------------------


def test_a_partner_sees_only_the_places_their_organisation_runs_and_no_programme_total(world, monkeypatch):
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.identity import caller_org_ids  # noqa: F401
    from connect_labs.supply_chain.runner import Standing

    partner = LabsOrg.objects.create(slug="a-partner", name="A Partner", connect_organization_id=777)
    world["store"].managed_by_org = partner
    world["store"].save()  # acacia and baobab hang from the store, so they are the partner's
    outsider = LabsOrg.objects.create(slug="another", name="Another", connect_organization_id=888)

    monkeypatch.setattr("connect_labs.supply_chain.runner.standing", lambda access: Standing("partner", None))
    monkeypatch.setattr("connect_labs.supply_chain.identity.caller_org_ids", lambda access: {777})
    mine = _view(world)
    assert mine["scoped"] is True and mine["break_even"] is None
    assert {r["name"] for r in mine["issue"]} == {"worker-acacia", "worker-baobab"}
    assert mine["issue_total"] == sum(r["to_issue"] for r in mine["issue"])

    monkeypatch.setattr(
        "connect_labs.supply_chain.identity.caller_org_ids", lambda access: {outsider.connect_organization_id}
    )
    theirs = _view(world)
    assert theirs["issue"] == [] and theirs["stores"] == [] and theirs["short"] == [] and theirs["break_even"] is None

    monkeypatch.setattr("connect_labs.supply_chain.identity.caller_org_ids", lambda access: None)
    assert _view(world)["issue"] == []  # nobody we can place sees nothing


def test_the_programme_itself_is_not_scoped(world):
    assert _view(world)["scoped"] is False


def test_a_handout_filled_from_the_plan_follows_the_saved_plan(world):
    """The Plan page's what-to-issue is what the handout is filled with, buffer and all."""
    import time_machine

    from connect_labs.supply_chain.models import Distribution

    with time_machine.travel(TODAY, tick=False):
        _filled_from_the_plan(world, Distribution)


def _filled_from_the_plan(world, Distribution):
    _op("stock_plan_set", data={"item_id": world["item"].pk, "buffer_days": 60})
    expected = {
        r["supply_point_id"]: int(r["to_issue"])
        for r in _op("stock_plan_view", item_id=world["item"].pk, horizon_weeks=1)["issue"]
        if r["to_issue"] > 0
    }
    assert expected, "the world should leave at least one worker needing stock"
    run = _op(
        "distribution_plan",
        data={
            "supply_point_id": world["store"].pk,
            "opportunity_id": world["rule"].opportunity_id,
            "commodity_slug": world["item"].commodity.slug,
            "distributed_on": TODAY.isoformat(),
            "source": "we_recorded",
            "from_forecast": True,
        },
    )
    lines = Distribution.objects.get(pk=run["id"]).lines.all()
    assert {line.to_supply_point_id: int(line.quantity) for line in lines} == expected


def test_who_may_run_short_adds_the_two_parts_of_the_need_rather_than_joining_them(page, world, monkeypatch):
    """'flw_018 needs about 7123' was 71 and 23 joined as text by the template's `|add`; the sum is 94."""
    from django.template.defaultfilters import floatformat
    from django.urls import reverse

    from connect_labs.supply_chain.plan import service

    seen = []

    def every_worker_as_short(rows):
        # List everyone, so the line renders whatever this world's stock happens to be, with a whole
        # number due and a fractional one new: the shape (71 and 23.4) that `|add` read as "7123.4".
        for row in rows:
            row["due"], row["new"] = Decimal(71), Decimal("23.4")
            row["need_week"] = row["due"] + row["new"]
        seen.extend(rows)
        return list(rows)

    monkeypatch.setattr(service, "running_short", every_worker_as_short)
    html = page.get(reverse("supply_chain:plan")).content.decode()
    assert seen and "needs about" in html
    for row in seen:
        total = floatformat(row["due"] + row["new"], 0)
        assert total == "94"
        assert f"needs about {total} this week" in html
        joined = f"{floatformat(row['due'], 0)}{floatformat(row['new'], 0)}"
        assert joined == "7123" and f"needs about {joined}" not in html
