"""A plan says how many of its people are in countries where our births total is far from the UN's."""

import pytest

from connect_labs.effectiveness import baseline, market, people
from connect_labs.effectiveness.tests.helpers import value
from connect_labs.effectiveness.tests.test_place_risk import selection
from connect_labs.labs.indicators.models import Source

pytestmark = pytest.mark.django_db


def with_births(nigeria, ours, wpp):
    value(nigeria["ng"], "births", ours, year=2023, source=Source.DERIVED)
    value(nigeria["ng"], "births_wpp", wpp, year=2023, source=Source.WPP)


def test_the_baseline_gives_the_ratio_and_whether_it_is_off(nigeria):
    with_births(nigeria, 5_000_000.0, 7_500_000.0)
    got = baseline.Baselines([nigeria["kano"]]).births_check("NGA")
    assert got["ratio"] == pytest.approx(2 / 3) and got["off"] is True
    assert baseline.Baselines([nigeria["kano"]]).births_check("KEN") is None


def test_a_country_with_no_un_figure_has_no_check(nigeria):
    assert baseline.Baselines([nigeria["kano"]]).births_check("NGA") is None


def plan_for(nigeria):
    found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
    return market.size("kc", people.for_model(found, "kc"), {}, 6.0, {"reach": 100}, target=150_000)["plan"]


def test_a_plan_in_a_country_far_from_the_uns_total_says_so(nigeria):
    with_births(nigeria, 5_000_000.0, 7_500_000.0)
    off = plan_for(nigeria)["births_off"]
    assert off["countries"] == ["Nigeria"] and off["people"] == pytest.approx(150_000)


def test_a_plan_in_a_country_that_agrees_says_nothing(nigeria):
    with_births(nigeria, 7_400_000.0, 7_500_000.0)
    assert plan_for(nigeria)["births_off"] == {"people": 0, "places": 0, "countries": []}
