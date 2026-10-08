"""Tests for the batch driver, with a fake worker behind a fake instance and S3. Plain pytest, no AWS."""

import importlib.util
import json
import pathlib
from types import SimpleNamespace

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_grid = _load("run_grid_under_test", HERE / "run_grid.py")
STATES = json.loads((HERE / "inputs.json").read_text())["states"]
BY_NAME = {s["name"]: s for s in STATES}
RUNTIME = {"image": "img@sha256:x", "emodpy_commit": "abc"}
GRID_KEYS = {
    "label",
    "kind",
    "drug",
    "target_pop_fraction",
    "averted_u5_pct",
    "averted_u5_ci",
    "doses_per_child_per_year",
    "averted_3_24m_pct",
}


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        body = self.objects[(Bucket, Key)]
        return {"Body": SimpleNamespace(read=lambda: body)}


class FakeInstance:
    """Plays the worker: reads the request from fake S3, writes the result, like run_scenarios.py does."""

    def __init__(self, s3, bucket, fit="ok", larval=2.5e7):
        self.s3, self.bucket, self.fit, self.larval = s3, bucket, fit, larval
        self.commands, self.requests, self.started = [], [], 0

    def ensure_running(self, boot_timeout_s, ready_timeout_s):
        self.started += 1
        return SimpleNamespace(cold=False, timings={})

    def run(self, commands, timeout_s=600):
        (cmd,) = commands
        self.commands.append((cmd, timeout_s))
        tokens = cmd.split()
        req_uri = tokens[tokens.index("--request") + 1]
        out_uri = tokens[tokens.index("--out") + 1]
        req = json.loads(self.s3.objects[(self.bucket, req_uri.split("/", 3)[3])])
        self.requests.append(req)
        result = self._calibrate(req) if req.get("mode") == "calibrate" else self._runs(req)
        self.s3.objects[(self.bucket, out_uri.split("/", 3)[3])] = json.dumps(result).encode()
        return SimpleNamespace(ok=True, exit_code=0, status="Success", stdout="", stderr="")

    def _calibrate(self, req):
        target = req["target_pfpr"]
        pfpr = {"ok": target + 0.01, "loose": target + 0.08, "unreachable": 0.8}[self.fit]
        return {
            "mode": "calibrate",
            "larval_capacity": self.larval,
            "pfpr_2_5y": pfpr,
            "fit_error": round(pfpr - target, 4),
            "fit": self.fit,
            "iterations": 2,
            "burnin_hash": "h",
        }

    def _runs(self, req):
        runs = []
        for seed in req["seeds"]:
            for i, s in enumerate(req["schedules"]):
                keep = 1 - 0.1 * i if s["code"] != "none" else 1
                runs.append(
                    {
                        "code": s["code"],
                        "drug": s.get("drug", "SP"),
                        "seed": seed,
                        "cases_3_24m": 100 * keep,
                        "kids_3_24m": 350,
                        "cases_u5": 300 * keep,
                        "kids_u5": 1000,
                        "pfpr_2_5y": 0.3,
                        "doses": 700,
                    }
                )
        return {"hash": "x", "runs": runs}


def make_box(**kw):
    s3 = FakeS3()
    inst = FakeInstance(s3, "bkt", **kw)
    return run_grid.Box(inst, s3, "bkt"), inst


def test_one_state_end_to_end_writes_the_schema(tmp_path):
    out = tmp_path / "grid.json"
    box, inst = make_box()
    grid, failed = run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano"])
    assert failed == []
    on_disk = json.loads(out.read_text())
    assert on_disk == grid
    assert set(on_disk) == {"version", "runtime", "states"} and on_disk["version"] == 1
    assert on_disk["runtime"] == RUNTIME
    assert list(on_disk["states"]) == ["Kano"]
    k = on_disk["states"]["Kano"]
    assert set(k) == {"inputs", "setting", "fit", "designs"}
    assert set(k["inputs"]) == {"pfpr_target", "incidence_per_1000", "pop_u5", "rain_wettest_quarter", "onset_month"}
    assert set(k["setting"]) == {"larval_capacity", "habitat_times", "habitat_values"}
    assert k["setting"]["larval_capacity"] == 2.5e7
    assert set(k["fit"]) == {"fit", "pfpr_2_5y", "fit_error"} and k["fit"]["fit"] == "ok"
    expected_codes = [d["code"] for d in run_grid.designs.designs_for(BY_NAME["Kano"])]
    assert list(k["designs"]) == expected_codes
    assert all(set(d) == GRID_KEYS for d in k["designs"].values())
    # Two requests: calibrate, then one grid request carrying none + every design x 3 seeds.
    cal, grid_req = inst.requests
    assert cal["mode"] == "calibrate" and "larval_capacity" not in cal["setting"]
    assert cal["target_pfpr"] == BY_NAME["Kano"]["pfpr_target"]
    assert [s["code"] for s in grid_req["schedules"]] == ["none", *expected_codes]
    assert grid_req["seeds"] == [0, 1, 2]
    assert all("drug" in s and s["rounds"] for s in grid_req["schedules"][1:])
    smc = [s for s in grid_req["schedules"] if s["code"].startswith("smc")]
    assert smc and smc[0]["drug"] == "SPAQ"
    # Grid requests get the long deadline over SSM.
    for cmd, timeout in inst.commands:
        assert "EMOD_REQUEST_TIMEOUT_S=2800" in cmd and "/opt/emod/.venv/bin/python /opt/emod/run_scenarios.py" in cmd
        assert timeout >= 3000


def test_resume_skips_states_already_in_the_output(tmp_path):
    out = tmp_path / "grid.json"
    box, inst = make_box()
    run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano"])
    n = len(inst.requests)
    _, failed = run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano"])
    assert len(inst.requests) == n and failed == []
    run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano", "Ondo"])
    assert len(inst.requests) == n + 2
    assert set(json.loads(out.read_text())["states"]) == {"Kano", "Ondo"}


@pytest.mark.parametrize("fit", ["unreachable", "loose"])
def test_non_ok_fit_is_recorded_and_designs_skipped(tmp_path, fit):
    out = tmp_path / "grid.json"
    box, inst = make_box(fit=fit, larval=1e9)
    run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano"])
    k = json.loads(out.read_text())["states"]["Kano"]
    assert k["fit"]["fit"] == fit
    assert k["designs"] == {}
    assert k["setting"]["larval_capacity"] == 1e9
    assert len(inst.requests) == 1  # calibrate only


def test_a_failed_state_is_left_out_so_the_next_run_retries_it(tmp_path):
    out = tmp_path / "grid.json"
    box, inst = make_box()
    real = inst.run
    inst.run = lambda *a, **k: SimpleNamespace(ok=False, exit_code=1, status="Failed", stdout="", stderr="boom")
    _, failed = run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano"])
    assert failed == ["Kano"] and not out.exists()
    inst.run = real
    _, failed = run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano"])
    assert failed == [] and "Kano" in json.loads(out.read_text())["states"]


def test_unknown_state_name_is_an_error(tmp_path):
    box, _ = make_box()
    with pytest.raises(ValueError, match="nowhere"):
        run_grid.run_grid(box, STATES, tmp_path / "g.json", RUNTIME, only=["Nowhere"])


def test_concurrency_two_writes_every_state(tmp_path):
    out = tmp_path / "grid.json"
    box, _ = make_box()
    run_grid.run_grid(box, STATES, out, RUNTIME, only=["Kano", "Ondo", "Lagos"], concurrency=2)
    assert set(json.loads(out.read_text())["states"]) == {"Kano", "Ondo", "Lagos"}


def test_setting_is_default_overlaid_with_only_the_three_keys():
    state = BY_NAME["Kano"]
    s = run_grid.state_setting(state["rain_monthly"], 2.5e7)
    times, values = run_grid.states_mod.habitat_curve(state["rain_monthly"])
    assert s["habitat_times"] == times and s["habitat_values"] == values and len(times) == 14
    assert s["larval_capacity"] == 2.5e7 and isinstance(s["larval_capacity"], float)
    for key, val in run_grid.DEFAULT_SETTING.items():
        if key not in run_grid.SETTING_KEYS:
            assert s[key] == val
    assert "larval_capacity" not in run_grid.state_setting(state["rain_monthly"])


def test_effects_on_a_tiny_fixture():
    def run(code, seed, cases_u5, cases_3_24m, doses=0):
        return {
            "code": code,
            "seed": seed,
            "cases_u5": cases_u5,
            "cases_3_24m": cases_3_24m,
            "kids_u5": 1000,
            "kids_3_24m": 350,
            "doses": doses,
        }

    result = {
        "runs": [
            run("none", 0, 100, 50),
            run("none", 1, 200, 40),
            run("none", 2, 100, 100),
            run("d", 0, 80, 25, 700),  # u5 20%, 3-24m 50%
            run("d", 1, 100, 20, 700),  # u5 50%, 3-24m 50%
            run("d", 2, 50, 50, 700),  # u5 50%, 3-24m 50%
        ]
    }
    design = {"code": "d", "label": "L", "kind": "pmc", "drug": "SP", "target_pop_fraction": 0.35}
    row = run_grid.design_effect(result, design)
    assert row["averted_u5_pct"] == 40.0 and row["averted_3_24m_pct"] == 50.0
    # t(2) = 4.30, stdev(20, 50, 50) = 17.32, n = 3  ->  4.30 x 17.32 / sqrt(3) = 43.0
    assert row["averted_u5_ci"] == pytest.approx(43.0, abs=0.1)
    assert row["doses_per_child_per_year"] == 1.0  # 700 / 350 kids / 2 years
    smc = run_grid.design_effect(result, {**design, "kind": "smc", "target_pop_fraction": 56 / 60})
    assert smc["doses_per_child_per_year"] == round(700 / (1000 * 56 / 60) / 2, 2)
    # A baseline with no cases is dropped from the pairing; with none left there is no row.
    assert run_grid.design_effect({"runs": [run("none", 0, 0, 0), run("d", 0, 0, 0)]}, design) is None


def test_request_hash_is_key_order_independent():
    assert run_grid.request_hash({"a": 1, "b": 2}) == run_grid.request_hash({"b": 2, "a": 1})
