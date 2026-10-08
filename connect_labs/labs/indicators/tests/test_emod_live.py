"""Schedule conversion and result summary for live EMOD runs."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

from connect_labs.labs.indicators.emod import live

REPO = pathlib.Path(__file__).resolve().parents[4]


@pytest.fixture
def sweep_module(monkeypatch):
    """tools/pmc_emod/pmc_sweep.py loaded by path, with the EMOD libraries it imports at module level stubbed."""
    for name in (
        "manifest",
        "emodpy",
        "emodpy.campaign",
        "emodpy.campaign.common",
        "emodpy.emod_task",
        "idmtools",
        "idmtools.builders",
        "idmtools.core",
        "idmtools.core.platform_factory",
        "idmtools.entities",
        "idmtools.entities.experiment",
    ):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    spec = importlib.util.spec_from_file_location("pmc_sweep_under_test", REPO / "tools/pmc_emod/pmc_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_grid_rounds_match_the_sweep_scenarios(sweep_module):
    for code, rounds in live.GRID_ROUNDS.items():
        assert [list(r) for r in sweep_module.SCENARIOS[code]] == rounds, code


def test_the_demo_question_is_four_monthly_rounds_from_may():
    rounds, text = live.to_rounds({"months": [5, 6, 7, 8], "age_min_months": 3, "age_max_months": 24})
    # May starts on day 122 (the grid starts April on day 91); one block of four 30-day rounds a year.
    assert rounds == [[122, 30, 4, 0.25, 2.0, 0.85], [487, 30, 4, 0.25, 2.0, 0.85]]
    assert "May" in text and "August" in text and "3-24 months" in text


def test_april_to_september_is_the_grids_monthly_in_season_schedule():
    rounds, _ = live.to_rounds({"months": [4, 5, 6, 7, 8, 9]})
    assert live.grid_match(rounds) == "connect_monthly_in_season_3_24"


def test_rounds_per_year_and_interval_days_follow_the_grid_rule():
    assert live.to_rounds({"rounds_per_year": 4})[0] == [[0, 91, 8, 0.25, 2.0, 0.85]]
    assert live.grid_match(live.to_rounds({"rounds_per_year": 4})[0]) == "connect_quarterly_3_24"
    assert live.to_rounds({"interval_days": 91})[0] == [[0, 91, 8, 0.25, 2.0, 0.85]]
    assert live.to_rounds({"rounds_per_year": 6, "age_min_months": 12})[0] == [[0, 61, 12, 1.0, 2.0, 0.85]]


def test_non_adjacent_months_make_separate_blocks():
    rounds, _ = live.to_rounds({"months": [10, 5, 6]})
    assert [r[:3] for r in rounds] == [[122, 30, 2], [274, 30, 1], [487, 30, 2], [639, 30, 1]]


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"months": [5], "rounds_per_year": 4},
        {"months": [13]},
        {"months": []},
        {"rounds_per_year": 0},
        {"rounds_per_year": 4, "coverage": 1.5},
        {"rounds_per_year": 4, "age_min_months": 24, "age_max_months": 3},
        {"rounds_per_year": 4, "colour": "red"},
    ],
)
def test_malformed_schedules_are_refused(spec):
    with pytest.raises(ValueError):
        live.to_rounds(spec)


def test_the_custom_code_is_deterministic_and_depends_on_the_rounds():
    a, _ = live.to_rounds({"months": [5, 6, 7, 8]})
    b, _ = live.to_rounds({"months": [8, 7, 6, 5]})
    c, _ = live.to_rounds({"months": [5, 6, 7]})
    assert live.schedule_code(a) == live.schedule_code(b) != live.schedule_code(c)
    assert live.schedule_code(a).startswith("custom_")


def _result(base, mine):
    runs = []
    for seed, (b, m) in enumerate(zip(base, mine)):
        runs.append({"code": "none", "seed": seed, "cases_3_24m": b, "kids_3_24m": 370, "doses": 0})
        runs.append({"code": "x", "seed": seed, "cases_3_24m": m, "kids_3_24m": 370, "doses": 1850})
    return {"runs": runs}


def test_summarise_pairs_each_seed_with_its_own_baseline():
    got = live.summarise(_result([2000, 2000, 2000], [1500, 1400, 1450]), "x")
    assert got["averted_pct"] == 27.5
    assert got["doses_per_child_per_year"] == 2.5
    assert got["seeds"] == 3 and got["too_noisy"] is False


def test_an_effect_inside_its_own_noise_is_flagged():
    got = live.summarise(_result([2000, 2000, 2000], [1990, 2100, 1900]), "x")
    assert got["too_noisy"] is True


def test_describe_uses_month_names_only_for_month_anchored_rounds():
    may_aug, _ = live.to_rounds({"months": [5, 6, 7, 8]})
    text = live.describe_rounds(may_aug)
    assert "May, June, July, August" in text

    for spec in ({"interval_days": 30}, {"rounds_per_year": 12}):
        rounds, _ = live.to_rounds(spec)
        text = live.describe_rounds(rounds)
        assert "every 30 days" in text and "12 rounds a year" in text and "January" not in text

    quarterly = live.describe_rounds(live.to_rounds({"rounds_per_year": 4})[0])
    assert "every 91 days" in quarterly and "May" not in quarterly
