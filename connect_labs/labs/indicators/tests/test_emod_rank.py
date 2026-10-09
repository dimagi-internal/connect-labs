"""Ranking (state, design) pairs from the per-state EMOD grid, on a fixture grid.

The fixture (``fixtures/pmc_state_grid.json``) holds three states:

* Ondo -- fitted, not seasonal: four PMC designs, one of them (quarterly) with an effect inside its noise;
* Kano -- fitted, seasonal: one PMC design and SMC;
* Lagos -- the fit could not reach its prevalence.

At the default costs a dose costs 0.80 x 1.2 / 0.95 = $1.0105.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from connect_labs.labs.indicators.emod import rank

FIXTURE = Path(__file__).parent / "fixtures" / "pmc_state_grid.json"
PER_DOSE = 0.8 * 1.2 / 0.95


@pytest.fixture
def grid():
    return json.loads(FIXTURE.read_text())


def _order(out):
    return [(r["state"], r["design_code"]) for r in out["ranked"]]


def test_ranking_order_and_arithmetic(grid):
    out = rank.rank_pairs(["Ondo", "Kano", "Lagos"], grid=grid)

    assert _order(out) == [
        ("Kano", "pmc_m4_onset"),  # $12
        ("Kano", "smc_m4_onset"),  # $13
        ("Ondo", "pmc_m6_onset"),  # $20, 90,000 cases: the tie goes to more cases averted
        ("Ondo", "pmc_m4_onset"),  # $20, 60,000 cases
        ("Ondo", "pmc_m12"),  # $24
    ]
    assert [r["rank"] for r in out["ranked"]] == [1, 2, 3, 4, 5]

    smc = out["ranked"][1]
    cases = 60 / 100 * 400 / 1000 * 2_000_000  # 480,000
    spend = 3.4 * (56 / 60) * 2_000_000 * PER_DOSE  # 6.41m
    assert smc["cases_averted_per_year"] == 480_000
    assert smc["spend_per_year"] == rank.sig(spend) == 6_400_000
    assert smc["cost_per_case_averted"] == rank.sig(spend / cases) == 13
    assert smc["averted_u5_pct"] == 60.0 and smc["ci"] == 3.0
    assert smc["state_design"].startswith("Kano · SMC (SPAQ)")
    assert "fitted" in smc["fit_note"]

    ondo_m12 = out["ranked"][4]
    assert ondo_m12["cost_per_case_averted"] == rank.sig(10.2 * 0.35 * 1_000_000 * PER_DOSE / 150_000) == 24

    assert out["label"] == "illustrative · fitted to each state's prevalence and rainfall"
    assert out["costs"]["cost_per_dose"] == round(PER_DOSE, 3)
    assert "$0.80 a visit" in out["costs_line"] and "20% platform fee" in out["costs_line"]


def test_best_per_state_and_its_totals(grid):
    out = rank.rank_pairs(["Ondo", "Kano"], grid=grid)

    best = {r["state"]: r["design_code"] for r in out["best_per_state"]}
    assert best == {"Kano": "pmc_m4_onset", "Ondo": "pmc_m6_onset"}
    totals = out["best_per_state_totals"]
    assert totals["states"] == 2
    assert totals["cases_averted_per_year"] == 290_000  # 200,000 + 90,000, three figures


def test_a_cheaper_visit_lowers_the_cost_per_case_and_keeps_the_order(grid):
    dear = rank.rank_pairs(["Ondo", "Kano"], grid=grid)
    cheap = rank.rank_pairs(["Ondo", "Kano"], cost_per_visit=0.4, grid=grid)

    assert _order(cheap) == _order(dear)
    for c, d in zip(cheap["ranked"], dear["ranked"]):
        assert c["cost_per_case_averted"] < d["cost_per_case_averted"]
        assert c["cases_averted_per_year"] == d["cases_averted_per_year"]


def test_fewer_pairs_than_asked_returns_them_all_and_says_so(grid):
    out = rank.rank_pairs(["Kano"], top_n=10, grid=grid)

    assert len(out["ranked"]) == 2
    assert out["pairs_ranked"] == 2
    assert "Only 2" in out["note"]


def test_top_n_is_capped(grid):
    assert rank.rank_pairs(None, top_n=500, grid=grid)["top_n"] == rank.MAX_TOP_N
    assert len(rank.rank_pairs(None, top_n=2, grid=grid)["ranked"]) == 2
    assert rank.rank_pairs(None, top_n=2, grid=grid)["note"] is None
    with pytest.raises(ValueError):
        rank.rank_pairs(None, top_n=0, grid=grid)


def test_an_unfit_state_is_excluded_with_its_reason(grid):
    out = rank.rank_pairs(["Lagos", "Ondo"], grid=grid)

    assert "Lagos" not in {r["state"] for r in out["ranked"]}
    (lagos,) = (e for e in out["excluded"] if e["state"] == "Lagos")
    assert "could not be fitted" in lagos["reason"] and "3%" in lagos["reason"]


def test_no_ranked_pairs_is_an_answer_not_an_error(grid):
    out = rank.rank_pairs(["Lagos", "Atlantis"], grid=grid)

    assert out["ranked"] == [] and out["best_per_state"] == []
    assert {e["state"] for e in out["excluded"]} == {"Lagos", "Atlantis"}
    assert "not in the per-state model grid" in [e for e in out["excluded"] if e["state"] == "Atlantis"][0]["reason"]
    assert out["note"].startswith("No state and design")


def test_an_effect_inside_its_noise_is_excluded_not_ranked(grid):
    out = rank.rank_pairs(["Ondo"], grid=grid)

    assert ("Ondo", "pmc_q4") not in _order(out)
    assert out["excluded_designs"] == [
        {
            "state": "Ondo",
            "design_code": "pmc_q4",
            "design_label": "Quarterly from May, 3–24 months",
            "reason": "no measurable effect",
        }
    ]


def test_a_state_with_no_effective_design_is_excluded(grid):
    for d in grid["states"]["Kano"]["designs"].values():
        d["averted_u5_pct"] = 0
    out = rank.rank_pairs(["Kano"], grid=grid)

    assert out["ranked"] == []
    assert out["excluded"][0]["state"] == "Kano" and "no measurable effect" in out["excluded"][0]["reason"]


@pytest.mark.parametrize("dose_rate", [0, -0.1])
def test_dose_rate_zero_is_refused(grid, dose_rate):
    with pytest.raises(ValueError):
        rank.rank_pairs(["Ondo"], dose_rate=dose_rate, grid=grid)


def test_a_free_visit_ranks_by_cases_without_dividing_by_zero(grid):
    out = rank.rank_pairs(["Ondo", "Kano"], cost_per_visit=0, grid=grid)

    assert all(r["cost_per_case_averted"] == 0 for r in out["ranked"])
    cases = [r["cases_averted_per_year"] for r in out["ranked"]]
    assert cases == sorted(cases, reverse=True)


def test_smc_only_where_the_grid_has_it(grid):
    out = rank.rank_pairs(["Ondo", "Kano"], kinds=("smc",), grid=grid)

    assert _order(out) == [("Kano", "smc_m4_onset")]
    everything = rank.rank_pairs(None, grid=grid)
    assert {r["state"] for r in everything["ranked"] if r["kind"] == "smc"} == {"Kano"}
    assert all("(SPAQ)" in r["design_label"] for r in everything["ranked"] if r["kind"] == "smc")


def test_an_smc_label_without_the_drug_gets_it(grid):
    grid["states"]["Kano"]["designs"]["smc_m4_onset"]["label"] = "SMC: 4 monthly rounds"
    out = rank.rank_pairs(["Kano"], kinds=("smc",), grid=grid)

    assert out["ranked"][0]["design_label"] == "SMC: 4 monthly rounds (SPAQ)"


def test_selection_names_are_case_insensitive(grid):
    out = rank.rank_pairs(["ondo", " KANO ", "Kano (NGA)"], grid=grid)

    assert {r["state"] for r in out["ranked"]} == {"Ondo", "Kano"}
    assert out["excluded"] == []


def test_a_missing_grid_file_loads_as_none(tmp_path):
    assert rank.load_grid(tmp_path / "absent.json") is None
    assert rank.load_grid(FIXTURE)["version"] == 1


def test_significant_figures():
    assert rank.sig(20.04) == 20
    assert rank.sig(0.874) == 0.87
    assert rank.sig(6_413_474) == 6_400_000
    assert rank.sig(288_123, 3) == 288_000
    assert rank.sig(0) == 0


def test_ranking_uses_the_exact_cost_not_the_rounded_one(grid):
    """$44.4 with more cases and $43.6 both display as $44; the cheaper one still ranks first."""
    designs = grid["states"]["Ondo"]["designs"]
    cost_for = {"pmc_m4_onset": 43.6, "pmc_m6_onset": 44.4}  # $ per case, set through the doses
    for code, target in cost_for.items():
        d = designs[code]
        cases = d["averted_u5_pct"] / 100 * 300 / 1000 * 1_000_000
        d["doses_per_child_per_year"] = target * cases / (d["target_pop_fraction"] * 1_000_000 * PER_DOSE)
    del designs["pmc_m12"]
    out = rank.rank_pairs(["Ondo"], grid=grid)

    # m6 averts more cases (90,000 vs 60,000) but costs $44.4; m4 costs $43.6.
    assert [r["design_code"] for r in out["ranked"]] == ["pmc_m4_onset", "pmc_m6_onset"]
    assert [r["cost_per_case_averted"] for r in out["ranked"]] == [44, 44]
    assert out["best_per_state"][0]["design_code"] == "pmc_m4_onset"


@pytest.mark.parametrize("alias", ["FCT", "Federal Capital Territory", "abuja", "Abuja (NGA)"])
def test_the_capital_territory_aliases_resolve(grid, alias):
    grid["states"]["Abuja Federal Capital Territory"] = grid["states"]["Ondo"]
    out = rank.rank_pairs([alias], grid=grid)

    assert {r["state"] for r in out["ranked"]} == {"Abuja Federal Capital Territory"}
    assert out["excluded"] == []


def test_a_unique_word_match_is_accepted_and_an_ambiguous_one_is_not(grid):
    grid["states"]["Akwa Ibom"] = grid["states"]["Ondo"]
    grid["states"]["Cross River"] = grid["states"]["Ondo"]
    grid["states"]["River North"] = grid["states"]["Ondo"]

    assert {r["state"] for r in rank.rank_pairs(["ibom"], grid=grid)["ranked"]} == {"Akwa Ibom"}
    vague = rank.rank_pairs(["river"], grid=grid)
    assert vague["ranked"] == [] and vague["excluded"][0]["reason"] == "not in the per-state model grid"


def test_the_caveats_say_one_visit_price_for_pmc_and_smc(grid):
    assert any("same price per visit" in c for c in rank.rank_pairs(None, grid=grid)["caveats"])


# --- deaths and the bar -------------------------------------------------------------------------------


def test_deaths_rank_by_cost_per_death_and_value_against_the_bar(grid):
    from connect_labs.labs.indicators.emod import mortality

    deaths = {"Ondo": 4_000.0, "Kano": 200.0}  # Kano cheap per case, but few deaths to avert
    out = rank.rank_pairs(["Ondo", "Kano"], grid=grid, deaths=deaths)

    assert out["ranked_by"] == "cost per death averted" and out["deaths_basis"] == "prevalence_scaled"
    assert [r["state"] for r in out["ranked"][:2]] == ["Ondo", "Ondo"]
    top = out["ranked"][0]
    d = grid["states"]["Ondo"]["designs"][top["design_code"]]
    spend = d["doses_per_child_per_year"] * d["target_pop_fraction"] * 1_000_000 * PER_DOSE
    averted = d["averted_u5_pct"] / 100 * 4_000
    assert top["deaths_averted_per_year"] == rank.sig(averted)
    assert top["cost_per_death_averted"] == rank.sig(spend / averted)
    assert top["multiple_of_benchmark"] == round(113 * averted / spend / 0.003, 1)
    assert out["bar"] == 6.0
    assert out["pairs_ranked"] == len(out["ranked"])  # all pairs shown, so the counts can be checked here
    assert out["pairs_clearing_bar"] == sum(r["clears_bar"] for r in out["ranked"])
    assert out["states_clearing_bar"] == len({r["state"] for r in out["ranked"] if r["clears_bar"]})
    assert all(r["clears_bar"] == (r["multiple_of_benchmark"] >= mortality.BAR.value) for r in out["ranked"])
    assert out["caveats"][-1] == rank.DEATHS_CAVEATS["prevalence_scaled"]


def test_a_state_with_no_death_figure_is_excluded_with_the_reason(grid):
    out = rank.rank_pairs(["Ondo", "Kano"], grid=grid, deaths={"Ondo": 4_000.0})
    assert {"state": "Kano", "reason": rank.NO_DEATHS} in out["excluded"]
    assert {r["state"] for r in out["ranked"]} == {"Ondo"}


def test_an_unknown_deaths_basis_is_an_error(grid):
    with pytest.raises(ValueError, match="deaths_basis"):
        rank.rank_pairs(["Ondo"], grid=grid, deaths={"Ondo": 1.0}, deaths_basis="ihme")


def test_without_deaths_it_still_ranks_by_cost_per_case(grid):
    out = rank.rank_pairs(["Ondo", "Kano"], grid=grid)
    assert out["ranked_by"] == "cost per case averted" and "multiple_of_benchmark" not in out["ranked"][0]


def test_the_state_index_marks_year_round_transmission_by_season_and_prevalence(grid):
    got = {s["name"]: s for s in rank.state_index(grid)}

    # Ondo: 40% of the rain in the wettest quarter, 30% prevalence. Kano is seasonal; Lagos has 3% prevalence.
    assert {n: s["perennial"] for n, s in got.items()} == {"Kano": False, "Lagos": False, "Ondo": True}
    assert got["Ondo"]["pfpr"] == 0.3 and got["Ondo"]["rain_wettest_quarter"] == 40.0
    assert rank.state_index(None) == []


def test_by_design_pools_each_schedule_across_the_states_with_its_noise(grid):
    out = rank.rank_pairs(["Ondo", "Kano"], grid=grid, deaths={"Ondo": 4_000.0, "Kano": 20_000.0})
    by = {r["design_code"]: r for r in out["by_design"]}

    # 4 monthly rounds ran in both states: 20% of Ondo's 4,000 deaths + 25% of Kano's 20,000 = 5,800.
    assert by["pmc_m4_onset"]["states"] == 2 and by["pmc_m4_onset"]["deaths_averted_per_year"] == 5800
    assert by["pmc_m4_onset"]["label"] == "4 monthly rounds from the rains, 3–24 months"
    # Quarterly's 3% +/- 4 in Ondo is not ranked as a pair, but it is pooled (not dropped) and counted as unclear.
    assert "pmc_q4" not in {r["design_code"] for r in out["ranked"]}
    assert by["pmc_q4"]["states_no_effect"] == 1 and by["pmc_q4"]["deaths_averted_per_year"] == 120
    # Cheapest per death first, each with its multiple of the benchmark.
    costs = [r["cost_per_death_averted"] for r in out["by_design"]]
    assert costs == sorted(costs) and all("multiple_of_benchmark" in r for r in out["by_design"])


def test_the_state_index_carries_the_rain_onset_for_the_live_run(grid):
    assert {s["name"]: s["onset_month"] for s in rank.state_index(grid)} == {"Kano": 6, "Lagos": 4, "Ondo": 5}


def _pool(label, per_state):
    """A pooled design as rank_pairs builds it; ``per_state`` is {state: (deaths, multiple)}."""
    rows = [
        {"state": s, "design_label": f"{label} in {s}", "deaths": d, "spend": 113 * d / (0.003 * m)}
        for s, (d, m) in per_state.items()
    ]
    return {"label": label, "kind": "pmc", "deaths": sum(r["deaths"] for r in rows), "per_state": rows}


class TestRecommend:
    STATES = ["A", "B", "C"]

    def pooled(self):
        return {
            # Cheapest per death overall, but it does not run in C: never the recommendation.
            "partial": _pool("Partial", {"A": (100, 40), "B": (100, 40)}),
            "pmc_m8_onset": _pool("8 monthly", {"A": (100, 12), "B": (100, 10), "C": (10, 3)}),
            "pmc_m12": _pool("Year-round", {"A": (130, 10), "B": (130, 9), "C": (12, 3)}),
            "pmc_q4": _pool("Quarterly", {"A": (40, 5), "B": (40, 4), "C": (4, 1)}),
        }

    def test_it_is_the_most_deaths_per_dollar_among_designs_that_run_everywhere(self):
        got = rank.recommend(self.pooled(), self.STATES)

        assert got["design_code"] == "pmc_m8_onset" and got["label"] == "8 monthly"

    def test_states_below_the_bar_are_dropped_and_the_programme_is_the_rest(self):
        got = rank.recommend(self.pooled(), self.STATES)

        assert got["keep"] == ["A", "B"] and got["drop"] == ["C"]
        assert got["deaths_averted_per_year"] == 200
        # A at 12x and B at 10x pooled: 200 deaths for 113*(100/36 + 100/30)/0.003 dollars -> 10.9x.
        assert got["multiple_of_benchmark"] == 10.9
        assert [s["state"] for s in got["states"]] == ["A", "B", "C"] and got["states"][2]["clears_bar"] is False

    def test_the_step_up_is_valued_on_the_increment(self):
        got = rank.recommend(self.pooled(), self.STATES)["step_up"]

        # Year-round over 8 monthly in A and B: 60 more deaths; cost from the two designs' spends.
        assert got["design_code"] == "pmc_m12" and got["extra_deaths_averted_per_year"] == 60
        spend = lambda d, m: 113 * d / (0.003 * m)  # noqa: E731
        extra = spend(130, 10) + spend(130, 9) - spend(100, 12) - spend(100, 10)
        assert got["multiple_of_benchmark"] == round(113 * 60 / extra / 0.003, 1)
        assert got["clears_bar"] is (got["multiple_of_benchmark"] >= 6)

    def test_it_compares_with_quarterly_over_the_same_states(self):
        got = rank.recommend(self.pooled(), self.STATES)["versus_quarterly"]

        assert got["multiple_of_benchmark"] < 6 and got["deaths_per_dollar_ratio"] > 2

    def test_a_state_where_the_effect_is_within_the_noise_is_unclear_not_kept_or_dropped(self):
        pooled = self.pooled()
        b_row = next(r for r in pooled["pmc_m8_onset"]["per_state"] if r["state"] == "B")
        b_row["clear"] = False  # B's 8-monthly effect is inside its own seed noise
        best = {"B": {"design_label": "Year-round in B", "multiple_of_benchmark": 9.0, "clears_bar": True}}

        got = rank.recommend(pooled, self.STATES, best)

        assert got["keep"] == ["A"] and got["drop"] == ["C"]
        assert got["unclear"] == [
            {
                "state": "B",
                "best_design_label": "Year-round in B",
                "best_multiple_of_benchmark": 9.0,
                "best_clears_bar": True,
            }
        ]
        assert got["deaths_averted_per_year"] == 100  # the programme counts A only
        assert next(s for s in got["states"] if s["state"] == "B")["clear_effect"] is False

    def test_nothing_runs_everywhere_means_no_recommendation(self):
        assert rank.recommend({"partial": _pool("Partial", {"A": (1, 9)})}, ["A", "B"]) is None


def test_rank_pairs_carries_the_recommendation_only_with_deaths(grid):
    with_deaths = rank.rank_pairs(["Ondo", "Kano"], grid=grid, deaths={"Ondo": 4_000.0, "Kano": 20_000.0})
    # Only 4 monthly rounds runs in both fixture states.
    assert with_deaths["recommendation"]["design_code"] == "pmc_m4_onset"
    assert rank.rank_pairs(["Ondo", "Kano"], grid=grid)["recommendation"] is None


class TestAges:
    STATES = ["A", "B"]

    def pooled(self):
        return {
            "pmc_m8_onset": _pool("8 monthly", {"A": (100, 12), "B": (100, 10)}),
            "pmc_m8_onset_y2": _pool("8 monthly, second year", {"A": (60, 15), "B": (60, 13)}),
        }

    def test_the_recommendation_is_for_our_proposals_3_to_24_months_by_default(self):
        got = rank.recommend(self.pooled(), self.STATES)

        assert got["design_code"] == "pmc_m8_onset" and got["ages"] == "3_24"

    def test_the_second_year_alone_is_chosen_only_when_asked(self):
        assert rank.recommend(self.pooled(), self.STATES, ages="12_24")["design_code"] == "pmc_m8_onset_y2"
        assert rank.recommend(self.pooled(), self.STATES, ages="any")["design_code"] == "pmc_m8_onset_y2"

    def test_smc_is_outside_both_pmc_age_bands(self):
        assert rank.design_ages("smc_m4_onset", "smc") is None
        assert rank.design_ages("pmc_q4_y2", "pmc") == "12_24" and rank.design_ages("pmc_q4", "pmc") == "3_24"

    def test_an_unknown_age_band_is_refused(self, grid):
        with pytest.raises(ValueError, match="ages"):
            rank.rank_pairs(["Ondo"], grid=grid, ages="0_60")


class TestBudgetPlans:
    STATES = ["A", "B", "C", "D"]

    def pooled(self):
        spend = lambda d, m: 113 * d / (0.003 * m)  # noqa: E731
        self.spend = spend
        return {
            # Cheap per death but small: 8 monthly averts fewer deaths per state than year-round.
            "pmc_m8_onset": _pool("8 monthly", {"A": (100, 20), "B": (100, 15), "C": (100, 10), "D": (100, 4)}),
            "pmc_m12": _pool("Year-round", {"A": (150, 16), "B": (150, 12), "C": (150, 8), "D": (150, 3)}),
        }

    def test_a_small_budget_buys_the_best_states_first(self):
        pooled = self.pooled()
        budget = self.spend(100, 20) + self.spend(100, 15) + 1

        (got,) = rank.budget_plans(pooled, self.STATES, [budget])

        # 8 monthly in A and B (200 deaths) beats year-round in A alone (150) for the same money.
        assert got["design_code"] == "pmc_m8_onset" and got["states"] == ["A", "B"]
        assert got["deaths_averted_per_year"] == 200

    def test_a_bigger_budget_can_switch_to_the_more_intensive_schedule(self):
        pooled = self.pooled()
        budget = sum(self.spend(150, m) for m in (16, 12, 8)) + 1

        (got,) = rank.budget_plans(pooled, self.STATES, [budget])

        assert got["design_code"] == "pmc_m12" and got["states"] == ["A", "B", "C"]
        assert got["states_worth_funding"] == 3  # D is below the bar with either schedule

    def test_it_never_buys_a_state_below_the_bar_or_with_an_unclear_effect(self):
        pooled = self.pooled()
        for p in pooled.values():
            next(r for r in p["per_state"] if r["state"] == "A")["clear"] = False

        (got,) = rank.budget_plans(pooled, self.STATES, [1e12])

        assert "A" not in got["states"] and "D" not in got["states"]

    def test_the_age_band_limits_the_schedules(self):
        pooled = {"pmc_m8_onset_y2": _pool("Second year", {"A": (100, 20)})}

        (got,) = rank.budget_plans(pooled, ["A"], [1e9])

        assert got["design_code"] is None and got["states"] == []

    def test_rank_pairs_plans_ten_twenty_and_thirty_million_by_default(self, grid):
        got = rank.rank_pairs(["Ondo", "Kano"], grid=grid, deaths={"Ondo": 4_000.0, "Kano": 20_000.0})

        assert [p["budget"] for p in got["budget_plans"]] == [10_000_000, 20_000_000, 30_000_000]

    def test_bad_budgets_are_refused(self, grid):
        with pytest.raises(ValueError, match="budgets"):
            rank.rank_pairs(["Ondo"], grid=grid, budgets=[-1])
