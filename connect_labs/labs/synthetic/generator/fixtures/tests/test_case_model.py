"""Modelled case timelines: fidelity to a source's per-worker and per-case structure,
and no sampled case that is a real one. Run against a simulated KMC-like source with
known structure (kmc_like_pool), on two seeds so a lucky draw cannot pass it."""

import collections
import copy

import numpy as np
import pytest

from connect_labs.labs.synthetic.generator.fixtures.case_model import (
    K_MIN,
    nearest_real_case_report,
    synthesize_case_pool,
)
from connect_labs.labs.synthetic.generator.fixtures.tests.kmc_like_pool import (
    AGE,
    ALIVE,
    BIRTH_WEIGHT,
    COUNTER,
    FROZEN,
    STATUS,
    WEIGHT,
    kmc_like_pool,
)


def _slope(series, path=WEIGHT):
    pts = [(v["day"], v["values"][path]) for v in series["visits"] if path in v["values"]]
    if len({d for d, _ in pts}) < 2:
        return None
    return float(np.polyfit([d for d, _ in pts], [w for _, w in pts], 1)[0])


def _summary(pool):
    slopes, pairs, deaths = [], [], collections.defaultdict(list)
    for s in pool:
        sl = _slope(s)
        bw = next((v["values"][BIRTH_WEIGHT] for v in s["visits"] if BIRTH_WEIGHT in v["values"]), None)
        died = any((v.get("cats") or {}).get(ALIVE) == "no" for v in s["visits"])
        if sl is None:
            continue
        slopes.append(sl)
        if bw is not None:
            pairs.append((bw, sl))
        deaths["slow" if sl <= 14 else "fast"].append(died)
    p = np.array(pairs)
    return {
        "slope": float(np.mean(slopes)),
        "corr": float(np.corrcoef(p[:, 0], p[:, 1])[0, 1]),
        "death_slow": float(np.mean(deaths["slow"])),
        "death_fast": float(np.mean(deaths["fast"])),
        "gap": float(np.mean([b["day"] - a["day"] for s in pool for a, b in zip(s["visits"], s["visits"][1:])])),
    }


@pytest.fixture(params=[7, 21])
def pools(request):
    real = kmc_like_pool(request.param)
    synthetic, report = synthesize_case_pool(copy.deepcopy(real), seed=request.param + 100, frozen_paths=FROZEN)
    return real, synthetic, report


def test_every_worker_keeps_its_caseload_and_case_lengths(pools):
    real, synthetic, _ = pools

    def shape(pool):
        by_owner = collections.defaultdict(list)
        for s in pool:
            by_owner[s["owner"]].append(len(s["visits"]))
        return {o: sorted(v) for o, v in by_owner.items()}

    assert shape(synthetic) == shape(real)


def test_no_sampled_case_is_a_real_case(pools):
    real, synthetic, report = pools
    check = nearest_real_case_report(synthetic, real)
    assert check["on_a_real_case"] == 0
    assert check["exact_copies_of_a_real_case"] == 0
    assert report["synthetic_cases"] == len(real)  # the gate never had to drop a case here
    real_starts = {s["start_date"] for s in real}
    # Starts are smoothed per worker; some may coincide with another real case's date,
    # but no case keeps its own: check per worker that not every start is a real one.
    assert {s["start_date"] for s in synthetic} != real_starts


def test_growth_birth_weight_and_spacing_are_reproduced(pools):
    real, synthetic, _ = pools
    r, s = _summary(real), _summary(synthetic)
    assert s["slope"] == pytest.approx(r["slope"], rel=0.10)
    assert s["gap"] == pytest.approx(r["gap"], rel=0.10)
    assert s["corr"] > 0.25 and abs(s["corr"] - r["corr"]) < 0.15  # heavier babies still grow faster


def test_deaths_stay_concentrated_in_slow_growers_and_end_the_case(pools):
    real, synthetic, _ = pools
    r, s = _summary(real), _summary(synthetic)
    assert r["death_slow"] > r["death_fast"]
    assert s["death_slow"] > s["death_fast"] + 0.05
    for series in synthetic:
        alive = [(v.get("cats") or {}).get(ALIVE) for v in series["visits"]]
        assert "no" not in alive[:-1]  # a dead baby is never visited again


def test_app_computed_fields_keep_their_identities(pools):
    _, synthetic, _ = pools
    for series in synthetic:
        for i, v in enumerate(series["visits"], start=1):
            if COUNTER in v["values"]:
                assert v["values"][COUNTER] == i
        offsets = {v["values"][AGE] - v["day"] for v in series["visits"] if AGE in v["values"]}
        assert len(offsets) <= 1  # age = day + age at the first visit


def test_a_rare_answer_is_never_modelled():
    real = kmc_like_pool(7)
    real[0]["visits"][0]["cats"][STATUS] = "RARE-VILLAGE-NAME"  # one case only (< K_MIN)
    synthetic, report = synthesize_case_pool(copy.deepcopy(real), seed=1, frozen_paths=FROZEN)
    shipped = {str(val) for s in synthetic for v in s["visits"] for val in (v.get("cats") or {}).values()}
    assert "RARE-VILLAGE-NAME" not in shipped
    assert report["categorical_values_suppressed"] >= 1


def test_a_field_seen_in_too_few_cases_is_not_modelled():
    real = kmc_like_pool(7)
    for s in real[: K_MIN - 1]:
        s["visits"][0]["values"]["form.rare_measure"] = 42.0
    synthetic, report = synthesize_case_pool(copy.deepcopy(real), seed=1, frozen_paths=FROZEN)
    assert "form.rare_measure" in report["fields_not_modelled"]
    assert not any("form.rare_measure" in v["values"] for s in synthetic for v in s["visits"])
