"""A plan says how many of its people are counted from a regional rate rather than their country's own."""

import pytest

from connect_labs.effectiveness import market, people
from connect_labs.effectiveness.tests.test_place_risk import selection

pytestmark = pytest.mark.django_db


def test_a_place_whose_count_rests_on_a_regional_rate_is_marked_and_the_other_is_not(nigeria):
    found = people.attach_risk(people.group_selection(selection(nigeria, proxied=("kano",)), "territory"))
    by_name = {p["name"]: p for p in people.for_model(found, "kc").values()}
    assert by_name["Kano"]["proxied"] is True and by_name["Lagos"]["proxied"] is False


def test_it_is_only_the_count_the_model_uses_that_matters(nigeria):
    found = people.group_selection(selection(nigeria, proxied=("kano",)), "territory")
    assert {p["name"]: p["proxied"] for p in people.for_model(found, "chc").values()}[
        "Kano"
    ] is False  # CHC uses pop_u5


def test_the_plan_totals_the_people_and_countries_that_lean_on_one(nigeria):
    found = people.attach_risk(people.group_selection(selection(nigeria, proxied=("kano",)), "territory"))
    places = people.for_model(found, "kc")
    plan = market.size("kc", places, {}, 6.0, {"reach": 100}, target=150_000)["plan"]
    on = plan["regional_proxy"]
    assert on["places"] == 1 and on["countries"] == ["Nigeria"]
    assert 0 < on["people"] <= 80_000
    assert any(p["regional_proxy"] for p in plan["places"])


def test_a_plan_with_none_says_none(nigeria):
    found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
    plan = market.size("kc", people.for_model(found, "kc"), {}, 6.0, target=100_000)["plan"]
    assert plan["regional_proxy"] == {"people": 0, "places": 0, "countries": []}
