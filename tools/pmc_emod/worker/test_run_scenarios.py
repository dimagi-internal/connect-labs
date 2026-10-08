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
    assert {r["drug"] for r in out["runs"]} == {"SP"}  # schedules without "drug" give SP, as before
    for r in out["runs"]:  # under-5 outcomes ride alongside the 3-24-month ones
        assert r["kids_u5"] > r["kids_3_24m"] > 0 and r["cases_u5"] >= r["cases_3_24m"] >= 0
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
        (lambda r: r["schedules"][0].update(drug="AQ"), "drug"),
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


def test_emod_image_is_pinned_by_digest_and_matches_bootstrap():
    import pathlib
    import re

    assert "@sha256:" in run_scenarios.EMOD_IMAGE
    boot = (pathlib.Path(run_scenarios.__file__).parent / "bootstrap.sh").read_text()
    m = re.search(r"^EMOD_IMAGE=(\S+)$", boot, re.M)
    assert m and m.group(1) == run_scenarios.EMOD_IMAGE


# ---- calibrate mode ------------------------------------------------------------------------------------------


def calibrate_request(target=0.3, **extra):
    setting = small_request(500, [0], ["none"])["setting"]
    setting.pop("larval_capacity")
    return dict({"mode": "calibrate", "setting": setting, "target_pfpr": target}, **extra)


def fake_pfpr(larval):
    """A saturating PfPR 2-5y curve in log10(larval): ~0 at 1e6, ~0.81 at 1e9."""
    import math

    return 0.85 / (1 + math.exp(-2.5 * (math.log10(larval) - 7.8)))


def test_calibration_grid_is_8_log_spaced_values_from_1e6_to_1e9():
    grid = run_scenarios.calibration_grid()
    assert len(grid) == 8 and grid[0] == 1e6 and grid[-1] == 1e9
    assert all(isinstance(x, float) for x in grid)


def test_calibrate_refines_around_the_crossing_and_picks_the_nearest_evaluated_value():
    rounds = []

    def evaluate(round_no, larvals):
        rounds.append((round_no, list(larvals)))
        return [fake_pfpr(x) for x in larvals]

    fit = run_scenarios.calibrate(evaluate, 0.27)
    assert [r for r, _ in rounds] == [1, 2] and len(rounds[1][1]) == 4
    est = run_scenarios.interpolate_crossing([(x, fake_pfpr(x)) for x in rounds[0][1]], 0.27)[0]
    import math

    logs = sorted(math.log10(x) for x in rounds[1][1])
    assert logs[0] < est < logs[-1]  # round 2 brackets the estimate
    assert fit["fit"] == "ok" and abs(fit["fit_error"]) <= 0.03 and fit["iterations"] == 2
    assert fit["larval_capacity"] in rounds[0][1] + rounds[1][1]
    assert abs(fit["pfpr_2_5y"] - fake_pfpr(fit["larval_capacity"])) < 1e-12
    assert len(fit["candidates"]) == 12


def test_calibrate_reports_an_unreachable_target_after_one_round():
    fit = run_scenarios.calibrate(lambda r, xs: [fake_pfpr(x) for x in xs], 0.99)
    assert fit["fit"] == "unreachable" and fit["iterations"] == 1
    assert fit["larval_capacity"] == 1e9 and fit["fit_error"] < -0.03


def test_calibrate_is_loose_when_reachable_but_not_hit():
    # a step: nothing between 0.1 and 0.8, so 0.45 is inside the range but never within 0.03
    fit = run_scenarios.calibrate(lambda r, xs: [0.1 if x < 1e8 else 0.8 for x in xs], 0.45)
    assert fit["fit"] == "loose"


def test_run_calibration_caches_the_chosen_burnin_where_a_run_request_finds_it(tmp_path, monkeypatch):
    class Manifest:
        eradication_path = str(tmp_path / "bin" / "Eradication")

    monkeypatch.setattr(run_scenarios, "load_pmc_sweep", lambda: (Manifest, object()))
    monkeypatch.setattr(run_scenarios, "ensure_binary", lambda m: None)
    batches = []

    def burnins(manifest, sweep, settings, dest_dirs, job_dir, deadline=None):
        batches.append(len(settings))
        for s, d in zip(settings, dest_dirs):
            d.mkdir(parents=True, exist_ok=True)
            (d / run_scenarios.BURNIN_FILE).write_text(repr(s["larval_capacity"]))
        return [fake_pfpr(s["larval_capacity"]) for s in settings]

    published = []
    monkeypatch.setattr(run_scenarios, "run_burnins", burnins)
    req = calibrate_request(0.27)
    out = run_scenarios.run_request(req, tmp_path, publish_burnin=lambda k, f: published.append((k, f.read_text())))
    assert batches == [8, 4]
    assert out["fit"] == "ok" and [r["round"] for r in out["rounds"]] == [1, 2]
    chosen = dict(req["setting"], larval_capacity=out["larval_capacity"])
    key = run_scenarios.setting_hash(chosen)
    assert out["burnin_hash"] == key
    dtk = tmp_path / "burnin" / key / run_scenarios.BURNIN_FILE
    assert dtk.read_text() == repr(out["larval_capacity"])  # the chosen candidate's own population
    assert published == [(key, repr(out["larval_capacity"]))]
    assert not list(tmp_path.glob("calibrate-*"))  # scratch candidates removed

    # A run request with the fitted value (round-tripped through JSON) starts from that burn-in.
    monkeypatch.setattr(run_scenarios, "run_pickups", lambda *a, **k: [])
    run_req = json.loads(json.dumps(dict(small_request(500, [0], ["none"]), setting=chosen)))
    assert run_scenarios.run_request(run_req, tmp_path)["burnin"]["cached"] is True


def test_valid_calibrate_request_passes():
    run_scenarios.validate_request(calibrate_request(0.27))
    run_scenarios.validate_request(calibrate_request(0.27, tolerance=0.05))


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda r: r["setting"].update(larval_capacity=6e7), "omitted"),
        (lambda r: r.pop("target_pfpr"), "target_pfpr"),
        (lambda r: r.update(target_pfpr=0), "target_pfpr"),
        (lambda r: r.update(target_pfpr=1), "target_pfpr"),
        (lambda r: r.update(target_pfpr="0.3"), "target_pfpr"),
        (lambda r: r.update(tolerance=0), "tolerance"),
        (lambda r: r.update(mode="fit"), "mode"),
        (lambda r: r["setting"].pop("habitat_values"), "habitat_values"),
        (lambda r: r["setting"].update(pop=0), "pop"),
    ],
)
def test_invalid_calibrate_requests_are_rejected(mutate, fragment):
    req = calibrate_request(0.27)
    mutate(req)
    with pytest.raises(run_scenarios.RequestError, match=fragment):
        run_scenarios.validate_request(req)


def test_a_run_request_still_needs_larval_capacity_and_ignores_no_mode():
    req = small_request(500, [0], ["none"])
    run_scenarios.validate_request(dict(req, mode="run"))
    req["setting"].pop("larval_capacity")
    with pytest.raises(run_scenarios.RequestError, match="larval_capacity"):
        run_scenarios.validate_request(req)


def test_setting_hash_treats_an_integer_larval_capacity_as_the_same_float():
    a = small_request(500, [0], ["none"])["setting"]
    assert run_scenarios.setting_hash(a) == run_scenarios.setting_hash(dict(a, larval_capacity=60000000))


# The live model's default setting (connect_labs/labs/indicators/emod/runner.py DEFAULT_SETTING), whose burn-in
# is stored under e894aa5178f45cff; canonicalising numbers must not move it.
RUNNER_DEFAULT_SETTING = {
    "name": "SW_Nigeria_like",
    "larval_capacity": 6e7,
    "habitat_times": [0, 30, 60, 91, 122, 152, 182, 213, 243, 274, 304, 334, 365],
    "habitat_values": [1.0, 0.8, 1.0, 2.0, 4.0, 6.0, 6.0, 5.0, 6.0, 5.0, 3.0, 1.5, 1.0],
    "pop": 5000,
    "case_mgmt": 0.5,
    "net_coverage": 0.5,
}


def test_setting_hash_canonicalises_every_number_and_keeps_the_deployed_key():
    a = RUNNER_DEFAULT_SETTING
    assert run_scenarios.setting_hash(a) == "e894aa5178f45cff"
    b = json.loads(json.dumps(a))
    b["habitat_values"] = [int(v) if v == int(v) else v for v in b["habitat_values"]]  # 1, 2, 4, ... as ints
    b["habitat_times"] = [float(t) for t in b["habitat_times"]]
    b["pop"] = 5000.0
    b["larval_capacity"] = 60000000
    assert run_scenarios.setting_hash(b) == "e894aa5178f45cff"
    assert run_scenarios.setting_hash(dict(a, case_mgmt=1)) == run_scenarios.setting_hash(dict(a, case_mgmt=1.0))
    assert run_scenarios.setting_hash(dict(a, pop=5001)) != "e894aa5178f45cff"


def test_each_request_gets_its_own_work_dir_and_it_is_removed(tmp_path, monkeypatch):
    import os

    class Manifest:
        eradication_path = str(tmp_path / "bin" / "Eradication")

    monkeypatch.setattr(run_scenarios, "load_pmc_sweep", lambda: (Manifest, object()))
    monkeypatch.setattr(run_scenarios, "ensure_binary", lambda m: None)

    def burnin(manifest, sweep, setting, dest_dir, job_dir, deadline=None):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / run_scenarios.BURNIN_FILE).write_text("x")

    cwds = []

    def pickups(*a, **k):
        cwds.append(os.getcwd())
        if len(cwds) == 1:  # a second request starting and finishing while the first is mid-run
            run_scenarios.run_request(small_request(500, [0], ["none"]), tmp_path, heartbeat_s=60)
            assert os.path.isdir(cwds[0]), "the other request removed this one's cwd"
        return []

    monkeypatch.setattr(run_scenarios, "build_burnin", burnin)
    monkeypatch.setattr(run_scenarios, "run_pickups", pickups)
    run_scenarios.run_request(small_request(500, [0], ["none"]), tmp_path, heartbeat_s=60)
    assert len(cwds) == 2 and cwds[0] != cwds[1]
    root = str(tmp_path.resolve())
    assert all(c.startswith(root) and os.path.basename(c).startswith("work-") for c in cwds)
    assert not list(tmp_path.glob("work*"))


def import_pmc_sweep():
    """pmc_sweep without EMOD: its emodpy / manifest imports are inside the functions that need them."""
    import importlib
    import sys

    parent = str(run_scenarios.HERE.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module("pmc_sweep")


def test_outcomes_add_under_5_fields_alongside_3_24_months():
    pmc_sweep_stub = import_pmc_sweep()

    msr = {
        "DataByTimeAndAgeBins": {
            "Annual Clinical Incidence by Age Bin": [[1.0, 2.0, 3.0, 0.5], [1.0, 1.0, 1.0, 0.5]],
            "Average Population by Age Bin": [[10, 20, 30, 100], [10, 20, 30, 100]],
            "PfPR by Age Bin": [[0.1, 0.2, 0.3, 0.4], [0.1, 0.2, 0.5, 0.4]],
        }
    }
    ec = {"Channels": {"PMC_Dose": {"Data": [1, 2, 3]}}}
    out = pmc_sweep_stub.outcomes(msr, ec)
    assert out["cases_3_24m"] == 40 + 20 and out["kids_3_24m"] == 20
    assert out["cases_u5"] == (10 + 40 + 90) + (10 + 20 + 30) and out["kids_u5"] == 60
    assert out["pfpr_2_5y"] == pytest.approx(0.4) and out["doses"] == 6
    assert pmc_sweep_stub.last_year_pfpr_2_5y(msr) == 0.5


@emod
def test_larval_setter_gives_the_config_a_direct_build_of_that_setting_gets(tmp_path, monkeypatch):
    from functools import partial

    monkeypatch.chdir(tmp_path)  # emodpy drops demographics_<timestamp>/ dirs in the cwd

    manifest, sweep = run_scenarios.load_pmc_sweep()
    a = small_request(500, [0], ["none"])["setting"]
    b = dict(a, larval_capacity=3.162e8)

    def task_for(setting):
        return run_scenarios.make_task(
            manifest,
            sweep,
            partial(sweep.build_config, setting=setting, duration_days=730, serialization=("write", [730])),
            partial(sweep.build_campaign, setting=setting, rounds=()),
            partial(sweep.build_demographics, setting),
            partial(sweep.build_reports, report_start=0, report_end=730, n_years=2),
        )

    swept = task_for(a)
    sweep.set_habitats(swept.config, b)
    assert json.dumps(swept.config, sort_keys=True) == json.dumps(task_for(b).config, sort_keys=True)
    assert json.dumps(swept.config, sort_keys=True) != json.dumps(task_for(a).config, sort_keys=True)


@emod
def test_calibrate_to_a_mid_target(tmp_path):
    req = calibrate_request(0.3, tolerance=0.05)
    out = run_scenarios.run_request(req, tmp_path)
    print(json.dumps({k: v for k, v in out.items() if k != "candidates"}, indent=1))
    print(json.dumps(out["candidates"]))
    assert out["fit"] == "ok" and abs(out["fit_error"]) <= 0.05
    assert out["iterations"] == 2 and len(out["candidates"]) == 12
    # The fitted value's burn-in is the cache entry a run request with it hits.
    chosen = dict(req["setting"], larval_capacity=out["larval_capacity"])
    run_req = dict(small_request(500, [0], ["none"], years=1), setting=chosen)
    run = run_scenarios.run_request(json.loads(json.dumps(run_req)), tmp_path)
    assert run["burnin"]["cached"] is True
    assert abs(run["runs"][0]["pfpr_2_5y"] - out["pfpr_2_5y"]) < 0.25  # same population, one year on


@emod
def test_unreachable_target_is_reported(tmp_path):
    out = run_scenarios.run_request(calibrate_request(0.99), tmp_path)
    print(json.dumps(out["candidates"]))
    assert out["fit"] == "unreachable" and out["iterations"] == 1
    assert out["fit_error"] < -0.03 and len(out["candidates"]) == 8


# ---- drug choice -----------------------------------------------------------------------------------------------


def test_spaq_schedules_validate_and_do_not_change_the_burnin_key():
    req = small_request(500, [0], ["none", "connect_monthly_in_season_3_24"])
    req["schedules"][1]["drug"] = "SPAQ"
    run_scenarios.validate_request(req)
    assert run_scenarios.setting_hash(req["setting"]) == run_scenarios.setting_hash(
        small_request(500, [0], ["none"])["setting"]
    )


def test_worker_drugs_match_pmc_sweep():
    sweep = import_pmc_sweep()
    assert run_scenarios.DRUGS == sweep.DRUGS
    # SMC is PMC's own SP entry plus amodiaquine, so the two differ only by the amodiaquine
    assert sweep.DRUG_ENTRIES == {
        "SP": ["SulfadoxinePyrimethamine"],
        "SPAQ": ["SulfadoxinePyrimethamine", "Amodiaquine"],
    }


@emod
def test_spaq_campaign_adds_amodiaquine_to_the_sp_entry(tmp_path, monkeypatch):
    from functools import partial

    monkeypatch.chdir(tmp_path)
    manifest, sweep = run_scenarios.load_pmc_sweep()
    setting = small_request(500, [0], ["none"])["setting"]
    rounds = [[91, 30, 4, 0.25, 5.0, 0.85]]

    def drugs_given(drug):
        task = run_scenarios.make_task(
            manifest,
            sweep,
            partial(sweep.build_config, setting=setting, duration_days=365),
            partial(sweep.build_campaign, setting=setting, rounds=rounds, drug=drug, start_shift=730),
            partial(sweep.build_demographics, setting),
            None,
        )
        found = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("class") == "AntimalarialDrug":
                    found.append(node["Drug_Type"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(json.loads(task.campaign.json)["Events"])
        return found

    background = ["Artemether", "Lumefantrine"] * 2
    assert sorted(drugs_given("SP")) == sorted(background + ["SulfadoxinePyrimethamine"])
    assert sorted(drugs_given("SPAQ")) == sorted(background + ["SulfadoxinePyrimethamine", "Amodiaquine"])


@emod
def test_spaq_schedule_runs_and_reports_doses(tmp_path):
    req = small_request(pop=500, seeds=[0], schedules=["none", "connect_monthly_in_season_3_24"], years=1)
    req["schedules"][0]["rounds"] = []
    req["schedules"][1]["rounds"] = [[91, 30, 4, 0.25, 5.0, 0.85]]
    req["schedules"][1]["drug"] = "SPAQ"
    out = run_local(req, cache_dir=tmp_path)
    by = {r["code"]: r for r in out["runs"]}
    smc = by["connect_monthly_in_season_3_24"]
    assert smc["drug"] == "SPAQ" and by["none"]["drug"] == "SP"
    assert smc["doses"] > 0 and by["none"]["doses"] == 0
    assert smc["cases_u5"] < by["none"]["cases_u5"]
