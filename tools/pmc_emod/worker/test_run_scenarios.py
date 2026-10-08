"""Run locally on a Mac with Docker Desktop:

    EMOD_TUTORIALS_DIR=<emodpy-malaria>/tutorials <venv>/bin/python -m pytest tools/pmc_emod/worker -v

The Django test config is not involved; this directory carries its own pytest.ini.
"""

import pytest
import run_scenarios

emod = pytest.mark.emod

ROUNDS = {
    "none": [],
    "connect_monthly_in_season_3_24": [[y * 365 + 91, 30, 6, 0.25, 2.0, 0.85] for y in range(2)],
}


def small_request(pop, seeds, schedules, years=2):
    setting = {
        "name": "test",
        "larval_capacity": 6e7,
        "habitat_times": [0, 30, 60, 91, 122, 152, 182, 213, 243, 274, 304, 334, 365],
        "habitat_values": [1.0, 0.8, 1.0, 2.0, 4.0, 6.0, 6.0, 5.0, 6.0, 5.0, 3.0, 1.5, 1.0],
        "pop": pop,
        "case_mgmt": 0.5,
        "net_coverage": 0.5,
    }
    return {
        "setting": setting,
        "schedules": [{"code": c, "rounds": ROUNDS[c]} for c in schedules],
        "seeds": seeds,
        "intervention_years": years,
    }


def run_local(req, cache_dir, heartbeat_s=60):
    return run_scenarios.run_request(req, cache_dir, heartbeat_s=heartbeat_s)


@emod
def test_missing_burnin_is_built_and_reported(tmp_path):
    req = small_request(pop=500, seeds=[0], schedules=["none", "connect_monthly_in_season_3_24"])
    out = run_local(req, cache_dir=tmp_path)
    assert out["burnin"]["cached"] is False and len(out["runs"]) == 2
    out2 = run_local(req, cache_dir=tmp_path)
    assert out2["burnin"]["cached"] is True and out2["seconds"] < out["seconds"]
    by = {r["code"]: r for r in out["runs"]}
    assert by["none"]["doses"] == 0 and by["connect_monthly_in_season_3_24"]["doses"] > 0
    assert out2["runs"] == out["runs"]  # same seed, same serialized state: deterministic


@emod
def test_runner_touches_activity_every_minute(tmp_path, monkeypatch):
    touched = []
    monkeypatch.setattr("run_scenarios.touch_activity", lambda: touched.append(1))
    run_local(small_request(pop=500, seeds=[0], schedules=["none"]), cache_dir=tmp_path, heartbeat_s=1)
    assert len(touched) >= 2


def test_setting_hash_ignores_name_and_tracks_model_inputs():
    a = small_request(500, [0], ["none"])["setting"]
    b = dict(a, name="other")
    c = dict(a, pop=501)
    assert run_scenarios.setting_hash(a) == run_scenarios.setting_hash(b) != run_scenarios.setting_hash(c)
