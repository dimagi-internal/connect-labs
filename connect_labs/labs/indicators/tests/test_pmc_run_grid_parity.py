"""The batch driver (tools/pmc_emod/states/run_grid.py) must agree with the Django-side code it mirrors."""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from connect_labs.labs.indicators.emod import live, runner, states

REPO = pathlib.Path(__file__).resolve().parents[4]
STATES = json.loads((REPO / "tools/pmc_emod/states/inputs.json").read_text())["states"]


@pytest.fixture(scope="module")
def run_grid():
    path = REPO / "tools/pmc_emod/states/run_grid.py"
    spec = importlib.util.spec_from_file_location("run_grid_parity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_setting_is_runners(run_grid):
    assert run_grid.DEFAULT_SETTING == runner.DEFAULT_SETTING
    assert isinstance(run_grid.DEFAULT_SETTING["larval_capacity"], float)


def test_state_setting_is_the_overlay_a_live_run_builds(run_grid, monkeypatch):
    """runner.fitted_setting overlays the grid entry's three keys on DEFAULT_SETTING; so must the batch."""
    state = STATES[0]
    sent = run_grid.state_setting(state["rain_monthly"], 2.5e7)
    times, values = states.habitat_curve(state["rain_monthly"])
    grid = {"states": {state["name"]: {"fit": {"fit": "ok"}, "setting": {k: sent[k] for k in run_grid.SETTING_KEYS}}}}
    monkeypatch.setattr("connect_labs.labs.indicators.emod.rank.load_grid", lambda path=None: grid)
    live_setting = runner.fitted_setting(state["name"])
    assert {k: v for k, v in live_setting.items() if k != "name"} == {k: v for k, v in sent.items() if k != "name"}
    assert sent["habitat_times"] == times and sent["habitat_values"] == values


def test_effect_and_ci_match_live(run_grid):
    runs = []
    for seed, (b, d) in enumerate([(100, 80), (200, 100), (150, 90)]):
        runs.append(
            {"code": "none", "seed": seed, "cases_u5": b, "kids_u5": 1000, "cases_3_24m": b, "kids_3_24m": 300}
        )
        runs.append(
            {
                "code": "d",
                "seed": seed,
                "cases_u5": d,
                "kids_u5": 1000,
                "cases_3_24m": d,
                "kids_3_24m": 300,
                "doses": 600,
            }
        )
    row = run_grid.design_effect(
        {"runs": runs}, {"code": "d", "label": "L", "kind": "pmc", "drug": "SP", "target_pop_fraction": 0.35}
    )
    base = {r["seed"]: r for r in runs if r["code"] == "none"}
    mine = {r["seed"]: r for r in runs if r["code"] == "d"}
    expected = live._under5_effect(base, mine, [0, 1, 2])
    assert row["averted_u5_pct"] == expected["averted_u5_pct"]
    assert row["averted_u5_ci"] == expected["averted_u5_ci"]


def test_driver_output_ranks(run_grid, tmp_path):
    """A grid the driver wrote (fake worker, one state) is one rank_pairs can rank: no silent empty ranking."""
    from connect_labs.labs.indicators.emod import rank

    spec = importlib.util.spec_from_file_location(
        "test_run_grid_for_rank", REPO / "tools/pmc_emod/states/test_run_grid.py"
    )
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    box, _ = fake.make_box()
    out = tmp_path / "grid.json"
    grid, failed = run_grid.run_grid(box, STATES, out, fake.RUNTIME, only=["Kano"])
    assert failed == []
    ranked = rank.rank_pairs(grid=json.loads(out.read_text()))
    assert ranked["pairs_ranked"] > 0
