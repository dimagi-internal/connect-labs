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
