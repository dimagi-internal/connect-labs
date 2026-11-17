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
        assert nga["measures"] == {"nmr": pytest.approx(30.0), "nmr_country": pytest.approx(30.0)}

    def test_a_territory_is_a_place_with_its_own_people_and_its_own_mortality(self, nigeria):
        found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
        kano, lagos = (found["places"][str(nigeria[k].pk)] for k in ("kano", "lagos"))
        assert (kano["name"], kano["country"], kano["iso3"]) == ("Kano", "Nigeria", "NGA")
        assert kano["counts"]["births_lbw"] == 80_000 and lagos["counts"]["births_lbw"] == 120_000
        assert kano["measures"]["nmr"] == pytest.approx(40.0) and lagos["measures"]["nmr"] == pytest.approx(15.0)
        # the country's figure beside each, so a far-off territory can be flagged and `trust` has something to pull
        # toward
        assert kano["measures"]["nmr_country"] == lagos["measures"]["nmr_country"] == pytest.approx(30.0)

    def test_for_model_hands_the_sizing_code_everything_it_needs(self, nigeria):
        found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
        got = people.for_model(found, "kc")[str(nigeria["kano"].pk)]
        assert (got["name"], got["country"], got["iso3"], got["units"], got["partial"]) == (
            "Kano",
            "Nigeria",
            "NGA",
            80_000,
            False,
        )
        assert got["measures"] == {"nmr": pytest.approx(40.0), "nmr_country": pytest.approx(30.0)}
        assert "Kano" in got["measures_how"]["nmr"] and got["inputs"] == {}  # Kangaroo Care has no place inputs


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


class TestEveryModelAtTheTerritoryGrain:
    """Child Health Campaign and Mother Baby used one figure for every territory in a country. They take the
    territory's own now, by the same recipes Implementations value a delivery with."""

    @pytest.fixture
    def found(self, nigeria):
        return people.attach_risk(people.group_selection(selection(nigeria), "territory"))

    def test_a_territory_hands_on_the_inputs_the_baseline_holds_for_it_and_only_those(self, nigeria, found):
        kano = people.for_model(found, "chc")[str(nigeria["kano"].pk)]["inputs"]
        lagos = people.for_model(found, "chc")[str(nigeria["lagos"].pk)]["inputs"]
        assert kano["ors"] == pytest.approx(0.20) and lagos["ors"] == pytest.approx(0.60)  # surveyed coverage
        assert {"deaths", "mort659"} <= set(kano)  # scaled by the territory's under-five mortality
        assert "vas" not in kano  # no vitamin A figure here: the model's country figure stands

    def test_a_country_takes_the_models_own_figures_not_a_roll_up_of_its_territories(self, nigeria):
        found = people.attach_risk(people.group_selection(selection(nigeria), "country"))
        assert people.for_model(found, "chc")["NGA"]["inputs"] == {}

    def test_so_two_territories_of_one_country_differ_for_child_health_too(self, nigeria, monkeypatch):
        monkeypatch.setattr(
            people,
            "people_by_place",
            lambda q, grain="country": people.attach_risk(people.group_selection(selection(nigeria), grain)),
        )
        rows = {
            r["name"]: r
            for r in sizing.market_of(Scenario.from_params({"models": "chc", "grain": "territory"}))["models"][0][
                "rows"
            ]
        }
        assert rows["Kano"]["multiple"] != rows["Lagos"]["multiple"] and rows["Kano"]["local_inputs"]
        flat = sizing.market_of(Scenario.from_params({"models": "chc", "grain": "territory", "place_risk": "0"}))[
            "models"
        ][0]["rows"]
        assert len({round(r["multiple"], 9) for r in flat}) == 1  # switched off: the country's figures, alike


class TestTrustAndTheCheck:
    @pytest.fixture
    def sized(self, nigeria, monkeypatch):
        monkeypatch.setattr(
            people,
            "people_by_place",
            lambda q, grain="country": people.attach_risk(people.group_selection(selection(nigeria), grain)),
        )
        return lambda params: sizing.market_of(Scenario.from_params(params))

    def test_no_trust_in_a_territory_is_its_countrys_figure(self, nigeria, sized):
        rows = {r["name"]: r for r in sized({"models": "kc", "grain": "territory", "trust": "0"})["models"][0]["rows"]}
        assert rows["Kano"]["risk"] == rows["Lagos"]["risk"] == pytest.approx(0.13 * 30 / 28.4)

    def test_a_plan_carries_its_check_and_a_less_trusted_scenario_does_not(self, nigeria, sized):
        plan = sized({"models": "kc", "grain": "territory", "target": "60000"})["models"][0]["plan"]
        assert plan["check"]["trust"] == 50 and 0 < plan["check"]["kept_share"] <= 1
        assert plan["check"]["places"] == len(plan["places"])
        less = sized({"models": "kc", "grain": "territory", "target": "60000", "trust": "50"})["models"][0]["plan"]
        assert less["check"] is None if "check" in less else True

    def test_the_plan_adds_up_by_country_and_says_the_limits_it_ran_under(self, nigeria, sized):
        plan = sized(
            {"models": "kc", "grain": "territory", "target": "60000", "min_place": "1000", "max_countries": "1"}
        )["models"][0]["plan"]
        assert plan["countries"][0]["country"] == "Nigeria" and plan["countries"][0]["places"] == len(plan["places"])
        assert plan["limits"] == {"min_place": 1000.0, "max_countries": 1, "left_out": 0}

    def test_the_countrys_figure_reaches_the_row_and_exactly_twice_or_half_is_not_far(self, nigeria, sized):
        rows = {r["name"]: r for r in sized({"models": "kc", "grain": "territory"})["models"][0]["rows"]}
        # Lagos is exactly half its country's 30, Kano a third above it: far means MORE than twice either way
        assert rows["Lagos"]["far_from_country"] is False and rows["Kano"]["far_from_country"] is False
        assert rows["Kano"]["measure_country"] == rows["Lagos"]["measure_country"] == pytest.approx(30.0)
        assert rows["Kano"]["measure"] == pytest.approx(40.0) and "Kano" in rows["Kano"]["measure_how"]


class TestThePlanAsASpreadsheet:
    @pytest.fixture
    def signed_in(self, client, django_user_model, nigeria, monkeypatch):
        monkeypatch.setattr(
            people,
            "people_by_place",
            lambda q, grain="country": people.attach_risk(people.group_selection(selection(nigeria), grain)),
        )
        client.force_login(django_user_model.objects.create_user(username="csv", password="x"))
        return client

    def test_it_is_one_row_per_place_then_the_totals_and_the_scenario(self, signed_in):
        from django.urls import reverse

        r = signed_in.get(reverse("cea:market_plan_csv"), {"models": "kc", "grain": "territory", "target": "60000"})
        assert (
            r.status_code == 200
            and r["Content-Type"].startswith("text/csv")
            and "plan-kc-60000.csv" in r["Content-Disposition"]
        )
        lines = r.content.decode().splitlines()
        assert lines[0].startswith("rank,place,country,low-birthweight newborns reached")
        assert lines[1].split(",")[1:3] == ["Kano", "Nigeria"]
        assert any(line.startswith("total,") for line in lines) and any(
            line.startswith("scenario link,") for line in lines
        )

    def test_without_a_target_there_is_no_plan_to_download(self, signed_in):
        from django.urls import reverse

        r = signed_in.get(reverse("cea:market_plan_csv"), {"models": "kc"})
        assert r.status_code == 400 and "target" in r.content.decode()

    def test_a_scenario_that_cannot_run_says_why(self, signed_in):
        from django.urls import reverse

        r = signed_in.get(reverse("cea:market_plan_csv"), {"models": "chc", "target": "5", "i.chc.nonsense": "1"})
        assert r.status_code == 400 and "nonsense" in r.content.decode()
