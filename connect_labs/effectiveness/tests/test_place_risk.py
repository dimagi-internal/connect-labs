"""A place's newborn mortality, from the baseline, and a territory's market from there to a plan."""

from types import SimpleNamespace

import pytest

from connect_labs.effectiveness import baseline, people, sizing
from connect_labs.effectiveness.scenario import Scenario

pytestmark = pytest.mark.django_db


class TestNewbornMortality:
    def test_a_territory_with_its_own_figure_takes_it_and_says_where_from(self, nigeria):
        got = baseline.Baselines([nigeria["kano"]]).newborn_mortality("NGA", nigeria["kano"])
        assert got[0] == pytest.approx(40.0) and "Kano" in got[1]

    def test_without_one_it_takes_the_countrys_scaled_by_the_territorys_under_five_mortality(self, nigeria):
        # Lagos: under-five mortality 50 against the country's 100, and no newborn figure of its own
        got = baseline.Baselines([nigeria["lagos"]]).newborn_mortality("NGA", nigeria["lagos"])
        assert got[0] == pytest.approx(30.0 * 50 / 100) and "under-five mortality" in got[1]

    def test_a_country_is_its_own_estimate(self, nigeria):
        got = baseline.Baselines([nigeria["ng"]]).newborn_mortality("NGA", None)
        assert got[0] == pytest.approx(30.0) and "IGME" in got[1]

    def test_a_country_with_nothing_has_no_figure_rather_than_a_guess(self, nigeria):
        assert baseline.Baselines([nigeria["ng"]]).newborn_mortality("NER", None) is None


def selection(nigeria, lbw=None):
    """What the baseline's selection of the two Nigerian states looks like to ``people``."""
    lbw = lbw or {"kano": 80_000, "lagos": 120_000}
    areas = [
        SimpleNamespace(
            boundary=nigeria[k],
            iso_code="NGA",
            country_name="Nigeria",
            name=nigeria[k].name,
            counts={"births_lbw": lbw[k], "pop_u5": lbw[k] * 10},
        )
        for k in ("kano", "lagos")
    ]
    return SimpleNamespace(areas=areas, unit_count=2, countries_unsupported=[], method="subnational_relevelled")


class TestGrain:
    def test_a_country_adds_its_territories_and_carries_the_countrys_own_mortality(self, nigeria):
        found = people.attach_risk(people.group_selection(selection(nigeria), "country"))
        nga = found["places"]["NGA"]
        assert nga["counts"]["births_lbw"] == 200_000 and nga["name"] == "Nigeria"
        assert nga["measures"] == {"nmr": pytest.approx(30.0)}

    def test_a_territory_is_a_place_with_its_own_people_and_its_own_mortality(self, nigeria):
        found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
        kano, lagos = (found["places"][str(nigeria[k].pk)] for k in ("kano", "lagos"))
        assert (kano["name"], kano["country"], kano["iso3"]) == ("Kano", "Nigeria", "NGA")
        assert kano["counts"]["births_lbw"] == 80_000 and lagos["counts"]["births_lbw"] == 120_000
        assert kano["measures"]["nmr"] == pytest.approx(40.0) and lagos["measures"]["nmr"] == pytest.approx(15.0)

    def test_for_model_hands_the_sizing_code_everything_it_needs(self, nigeria):
        found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
        got = people.for_model(found, "kc")[str(nigeria["kano"].pk)]
        assert got == {
            "name": "Kano",
            "country": "Nigeria",
            "iso3": "NGA",
            "units": 80_000,
            "partial": False,
            "measures": {"nmr": pytest.approx(40.0)},
        }


class TestFromTerritoriesToAPlan:
    @pytest.fixture
    def sized(self, nigeria, monkeypatch):
        def found(question, grain="country"):
            return people.attach_risk(people.group_selection(selection(nigeria), grain))

        monkeypatch.setattr(people, "people_by_place", found)
        return lambda params: sizing.market_of(Scenario.from_params(params))

    def test_the_territory_where_small_babies_are_likelier_to_die_is_the_better_buy(self, nigeria, sized):
        rows = {r["name"]: r for r in sized({"models": "kc", "grain": "territory"})["models"][0]["rows"]}
        assert rows["Kano"]["multiple"] > rows["Lagos"]["multiple"]
        assert rows["Kano"]["risk"] == pytest.approx(0.13 * 40 / 28.4) and rows["Lagos"]["risk"] == pytest.approx(
            0.13 * 15 / 28.4
        )
        assert rows["Kano"]["country"] == "Nigeria"

    def test_the_plan_takes_the_best_territory_up_to_what_can_be_reached_there_then_the_next(self, nigeria, sized):
        out = sized({"models": "kc", "grain": "territory", "target": "70000"})
        plan = out["models"][0]["plan"]
        assert [(p["name"], round(p["reached"])) for p in plan["places"]] == [("Kano", 40_000), ("Lagos", 30_000)]
        assert plan["feasible"] and out["target"] == 70_000 and plan["gain"]["deaths_averted_pct"] > 0

    def test_the_most_that_can_be_reached_is_kangaroo_cares_own_starting_half(self, nigeria, sized):
        capacity = sized({"models": "kc", "grain": "territory", "target": "1"})["models"][0]["plan"]["capacity"]
        assert capacity == pytest.approx(0.5 * 200_000)
        wider = sized({"models": "kc", "grain": "territory", "target": "1", "kc_reach": "80"})["models"][0]["plan"]
        assert wider["capacity"] == pytest.approx(0.8 * 200_000)

    def test_at_the_country_grain_the_same_selection_is_one_place(self, nigeria, sized):
        rows = sized({"models": "kc"})["models"][0]["rows"]
        assert [r["name"] for r in rows] == ["Nigeria"] and rows[0]["risk"] == pytest.approx(0.13 * 30 / 28.4)

    def test_the_answer_says_what_grain_it_is_and_what_it_is_aiming_at(self, nigeria, sized):
        out = sized({"models": "kc", "grain": "territory", "target": "50000"})
        assert out["grain"] == "territory" and out["selection"]["places"] == 2 and out["selection"]["countries"] == 1
        assert out["link"]["target"] == "50000" and out["link"]["grain"] == "territory"

    def test_turning_the_scaling_off_makes_the_territories_alike_and_the_answer_says_so(self, nigeria, sized):
        m = sized({"models": "kc", "grain": "territory", "place_risk": "0"})["models"][0]
        assert m["flat"] is True and m["place_risk"]["on"] is False
        assert len({round(r["multiple"], 9) for r in m["rows"]}) == 1

    def test_the_overall_is_the_plan_when_there_is_one_and_everything_reachable_when_there_is_not(
        self, nigeria, sized
    ):
        planned = sized({"models": "kc", "grain": "territory", "target": "70000"})
        t = planned["models"][0]["plan"]["totals"]
        o = planned["overall"]
        assert o["planned"] and o["feasible"] and o["cost"] == t["cost"] and o["people"] == pytest.approx(70_000)
        assert o["deaths_averted"] == t["deaths_averted"] and o["cost_per_death"] == t["cost_per_death"]
        free = sized({"models": "kc", "grain": "territory"})
        m = free["models"][0]["totals"]
        assert free["overall"]["planned"] is False and free["overall"]["cost"] == m["market_usd"]
        assert free["overall"]["multiple"] == m["multiple"] and free["overall"]["people"] == m["units"]

    def test_the_overall_of_two_programmes_adds_cost_and_deaths_and_weights_the_multiple_by_spend(
        self, nigeria, sized
    ):
        both = sized({"models": "chc,kc", "grain": "territory"})
        a, b = (m["totals"] for m in both["models"])
        o = both["overall"]
        assert o["cost"] == pytest.approx(a["market_usd"] + b["market_usd"])
        assert o["deaths_averted"] == pytest.approx(a["deaths_averted"] + b["deaths_averted"])
        lo, hi = sorted((a["multiple"], b["multiple"]))
        assert lo <= o["multiple"] <= hi
