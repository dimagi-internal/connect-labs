"""Tests for the per-state design grid. Run with plain pytest from this directory or the repo root."""

import importlib.util
import json
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


designs = _load("pmc_designs_under_test", HERE / "designs.py")
STATES = json.loads((HERE / "inputs.json").read_text())["states"]
BY_NAME = {s["name"]: s for s in STATES}


def _worker():
    try:
        return _load("run_scenarios_under_test", REPO / "tools/pmc_emod/worker/run_scenarios.py")
    except Exception as exc:  # worker imports EMOD libraries at module level
        pytest.skip(f"worker not importable here: {exc}")


def _state(onset=6, wettest=45.0):
    return {"name": "X", "onset_month": onset, "rain_wettest_quarter": wettest}


def _by_code(onset=6, wettest=45.0):
    return {d["code"]: d for d in designs.designs_for(_state(onset, wettest))}


def test_smc_only_for_seasonal_states():
    assert "smc_m4_onset" not in _by_code(wettest=59.9)
    smc = _by_code(wettest=60)["smc_m4_onset"]
    assert smc["kind"] == "smc" and smc["target_pop_fraction"] == pytest.approx(56 / 60)
    assert smc["rounds"][0][3:] == [3 / 12, 59 / 12, 0.85]
    for s in STATES:
        has = any(d["code"] == "smc_m4_onset" for d in designs.designs_for(s))
        assert has == (s["rain_wettest_quarter"] >= 60), s["name"]


def test_pmc_designs_target_3_to_24_months():
    for d in designs.designs_for(_state()):
        if d["kind"] == "pmc":
            assert d["target_pop_fraction"] == pytest.approx(21 / 60)
            assert all(r[3:] == [3 / 12, 2.0, 0.85] for r in d["rounds"]), d["code"]


def test_codes_unique_and_labels_have_months():
    for s in STATES:
        ds = designs.designs_for(s)
        codes = [d["code"] for d in ds]
        assert len(set(codes)) == len(codes)
        for d in ds:
            if d["code"] != "pmc_m12":
                assert any(m in d["label"] for m in designs.MONTH_ABBR), d["label"]
    assert _by_code(6)["pmc_m6_onset"]["label"] == "6 monthly rounds, Jun–Nov, 3–24 months"
    assert _by_code(6)["pmc_m12"]["label"] == "Year-round monthly, 3–24 months"
    assert _by_code(6, 70)["smc_m4_onset"]["label"] == "SMC (SPAQ): 4 monthly rounds, Jun–Sep, 3–59 months"
    assert _by_code(11)["pmc_m4_onset"]["label"].startswith("4 monthly rounds, Nov–Feb")


def _times(d):
    """(year, start day) of every round, from the groups."""
    return [(int(off // 365), off + k * iv) for off, iv, reps, *_ in d["rounds"] for k in range(reps)]


def _all_designs(wettest=80):
    return [(o, d) for o in range(1, 13) for d in designs.designs_for(_state(o, wettest))]


def test_periodic_every_year_identical():
    for onset, d in _all_designs():
        times = _times(d)
        assert all(0 <= t < 730 for _, t in times), (onset, d["code"])
        y0 = sorted(t for y, t in times if y == 0)
        y1 = sorted(t - 365 for y, t in times if y == 1)
        assert y0 == y1, (onset, d["code"])
        nominal = {"pmc_m4_onset": 4, "pmc_m6_onset": 6, "pmc_m8_onset": 8, "pmc_m12": 12, "pmc_q4": 4, "pmc_b6": 6}
        assert len(y0) == nominal.get(d["code"], 4), (onset, d["code"])


def test_offsets_follow_onset():
    for onset in range(1, 13):
        start = designs.month_offset(onset)
        for code in ("pmc_m4_onset", "pmc_m6_onset", "pmc_m8_onset", "pmc_q4", "pmc_b6", "smc_m4_onset"):
            d = {x["code"]: x for x in designs.designs_for(_state(onset, 80))}[code]
            first = sorted(t for y, t in _times(d) if y == 0)
            # the head of the season starts exactly at the onset month's day (when it fits in the year)
            assert start in first, (code, onset)
        assert min(t for _, t in _times({x["code"]: x for x in designs.designs_for(_state(onset))}["pmc_m12"])) == 0
    a, b = _by_code(5)["pmc_m6_onset"], _by_code(9)["pmc_m6_onset"]
    assert b["rounds"][-1][0] > a["rounds"][-1][0]


def test_known_shapes():
    d = _by_code(6)
    assert d["pmc_m6_onset"]["rounds"][0][:3] == [152, 30, 6]
    assert d["pmc_m12"]["rounds"][1][:3] == [365, 30, 12]
    # Jul start, 8 monthly rounds: rounds from day 365 wrap to the start of the year (182+7*30=392 -> 27)
    r = _by_code(7)["pmc_m8_onset"]["rounds"]
    assert r[0][:3] == [27, 30, 1] and r[1][:3] == [182, 30, 7]


def test_drug_and_smc_label():
    for d in designs.designs_for(_state(6, 80)):
        assert d["drug"] == ("SPAQ" if d["kind"] == "smc" else "SP")
    assert _by_code(6, 70)["smc_m4_onset"]["label"] == "SMC (SPAQ): 4 monthly rounds, Jun\u2013Sep, 3\u201359 months"


def test_missing_wettest_quarter_fails_loudly():
    with pytest.raises(KeyError):
        designs.designs_for({"onset_month": 6})


def test_every_design_passes_worker_validate_request():
    worker = _worker()
    setting = {
        "name": "t",
        "larval_capacity": 1.0,
        "habitat_times": [15, 380],
        "habitat_values": [1.0, 1.0],
        "pop": 5000,
        "case_mgmt": 0.5,
        "net_coverage": 0.5,
    }
    for s in STATES:
        schedules = [{"code": d["code"], "rounds": d["rounds"], "drug": d["drug"]} for d in designs.designs_for(s)]
        worker.validate_request(
            {"setting": setting, "schedules": schedules, "seeds": [1, 2, 3], "intervention_years": 2}
        )


def test_month_offset_matches_live():
    try:
        live = _load("live_under_test", REPO / "connect_labs/labs/indicators/emod/live.py")
    except Exception as exc:
        pytest.skip(f"live.py needs Django here: {exc}")
    assert [designs.month_offset(m) for m in range(1, 13)] == [live.month_offset(m) for m in range(1, 13)]
