"""Run locally on a Mac with Docker Desktop:

    EMOD_TUTORIALS_DIR=<emodpy-malaria>/tutorials <venv>/bin/python -m pytest tools/pmc_emod/worker -v

The Django test config is not involved; this directory carries its own pytest.ini.
"""

import copy
import json
import time

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
        "schedules": [{"code": c, "rounds": copy.deepcopy(ROUNDS[c])} for c in schedules],
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


def test_setting_hash_ignores_name_and_tracks_model_inputs():
    a = small_request(500, [0], ["none"])["setting"]
    b = dict(a, name="other")
    c = dict(a, pop=501)
    assert run_scenarios.setting_hash(a) == run_scenarios.setting_hash(b) != run_scenarios.setting_hash(c)


@emod
def test_run_with_heartbeat_touches_activity(tmp_path, monkeypatch):
    touched = []
    monkeypatch.setattr("run_scenarios.touch_activity", lambda: touched.append(1))
    run_local(small_request(pop=500, seeds=[0], schedules=["none"]), cache_dir=tmp_path, heartbeat_s=1)
    assert len(touched) >= 2


def test_heartbeat_ticks(monkeypatch):
    touched = []
    monkeypatch.setattr("run_scenarios.touch_activity", lambda: touched.append(1))
    with run_scenarios.Heartbeat(0.05):
        time.sleep(0.4)
    assert len(touched) >= 3


def test_touch_activity_failure_is_loud_not_fatal(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("EMOD_ACTIVITY_FILE", str(blocker / "sub" / "last-activity"))
    run_scenarios.touch_activity()
    assert "WARNING" in capsys.readouterr().err


def test_valid_request_passes():
    run_scenarios.validate_request(small_request(500, [0, 1], ["none", "connect_monthly_in_season_3_24"]))


def _bad(mutate):
    req = small_request(500, [0], ["connect_monthly_in_season_3_24"])
    mutate(req)
    return req


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda r: r["setting"].pop("larval_capacity"), "larval_capacity"),
        (lambda r: r["setting"].update(pop=0), "pop"),
        (lambda r: r["setting"].update(pop="500"), "pop"),
        (lambda r: r.update(schedules=[]), "schedules"),
        (lambda r: r["schedules"].append(dict(r["schedules"][0])), "unique"),
        (lambda r: r["schedules"][0]["rounds"][0].pop(), "6 numbers"),
        (lambda r: r["schedules"][0]["rounds"][0].__setitem__(5, 1.5), "coverage"),
        (lambda r: r["schedules"][0]["rounds"][0].__setitem__(0, -1), "offset"),
        (lambda r: r["schedules"][0]["rounds"][0].__setitem__(1, 0), "interval"),
        (lambda r: r["schedules"][0]["rounds"][0].__setitem__(2, 0), "reps"),
        (lambda r: r["schedules"][0]["rounds"][0].__setitem__(3, 3.0), "age_min"),
        (lambda r: r.update(seeds=[]), "seeds"),
        (lambda r: r.update(seeds=[1, 1]), "unique"),
        (lambda r: r.update(seeds=["a"]), "integers"),
        (lambda r: r.update(intervention_years=0), "intervention_years"),
    ],
)
def test_invalid_requests_are_rejected_before_any_run(mutate, fragment):
    with pytest.raises(run_scenarios.RequestError, match=fragment):
        run_scenarios.validate_request(_bad(mutate))


def test_run_request_validates_before_burnin(tmp_path):
    with pytest.raises(run_scenarios.RequestError):
        run_scenarios.run_request(_bad(lambda r: r.update(seeds=[])), tmp_path)
    assert not (tmp_path / "burnin").exists()


def test_cli_exits_nonzero_on_invalid_request(tmp_path, capsys):
    path = tmp_path / "req.json"
    path.write_text(json.dumps(_bad(lambda r: r.update(seeds=[]))))
    assert run_scenarios.main(["--request", str(path), "--out", str(tmp_path / "o.json")]) == 2
    assert "invalid request" in capsys.readouterr().err


def test_only_not_found_is_a_cache_miss():
    from botocore.exceptions import ClientError

    def err(code, status):
        return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "GetObject")

    assert run_scenarios.is_missing(err("404", 404))
    assert run_scenarios.is_missing(err("NoSuchKey", 404))
    assert not run_scenarios.is_missing(err("403", 403))
    assert not run_scenarios.is_missing(err("SlowDown", 503))


def test_one_experiment_waits_at_most_what_is_left_of_the_request(monkeypatch):
    monkeypatch.setenv("EMOD_RUN_TIMEOUT_S", "1800")
    assert run_scenarios.experiment_timeout() == 1800
    assert run_scenarios.experiment_timeout(time.monotonic() + 300) in (299, 300)
    with pytest.raises(TimeoutError, match="EMOD_REQUEST_TIMEOUT_S"):
        run_scenarios.experiment_timeout(time.monotonic() - 1)


def test_the_request_deadline_defaults_to_2100s_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv("EMOD_REQUEST_TIMEOUT_S", raising=False)
    assert run_scenarios.request_deadline(100.0) == 2200.0
    monkeypatch.setenv("EMOD_REQUEST_TIMEOUT_S", "60")
    assert run_scenarios.request_deadline(100.0) == 160.0


def test_burnin_and_pickups_share_one_request_deadline(tmp_path, monkeypatch):
    """A cold request is bounded as a whole: both experiments get the same deadline, not 1800 s each."""
    seen = {}

    class Manifest:
        eradication_path = str(tmp_path / "bin" / "Eradication")

    monkeypatch.setenv("EMOD_REQUEST_TIMEOUT_S", "2100")
    monkeypatch.setattr(run_scenarios, "load_pmc_sweep", lambda: (Manifest, object()))
    monkeypatch.setattr(run_scenarios, "ensure_binary", lambda m: None)

    def burnin(manifest, sweep, setting, dest_dir, job_dir, deadline=None):
        seen["burnin"] = deadline
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / run_scenarios.BURNIN_FILE).write_text("x")

    def pickups(manifest, sweep, setting, schedules, seeds, years, burnin_dir, job_dir, deadline=None):
        seen["pickups"] = deadline
        return []

    monkeypatch.setattr(run_scenarios, "build_burnin", burnin)
    monkeypatch.setattr(run_scenarios, "run_pickups", pickups)
    before = time.monotonic()
    run_scenarios.run_request(small_request(500, [0], ["none"]), tmp_path, heartbeat_s=60)
    assert seen["burnin"] == seen["pickups"]
    assert before + 2100 <= seen["burnin"] <= time.monotonic() + 2100
