"""A case pool is the same however many processes sample it."""

import numpy as np

from connect_labs.labs.synthetic.generator.fixtures import case_model as cm
from connect_labs.labs.synthetic.generator.fixtures.tests.kmc_like_pool import FROZEN, kmc_like_pool


def test_the_pool_is_the_same_on_one_process_or_several(monkeypatch):
    monkeypatch.setattr(cm, "_PARALLEL_MIN_CASES", 0)
    pool = kmc_like_pool(seed=11, workers=30)
    one, one_report = cm.synthesize_case_pool(pool, seed=5, frozen_paths=FROZEN)
    several, several_report = cm.synthesize_case_pool(pool, seed=5, frozen_paths=FROZEN, processes=3)
    assert several == one
    assert several_report == one_report


def test_a_workers_cases_do_not_depend_on_the_workers_sampled_before_it():
    """Each worker draws from its own streams -- what lets caseloads be split across processes."""
    pool = kmc_like_pool(seed=11, workers=30)
    model = cm.fit_case_model(pool, frozen_paths=FROZEN)
    gate = cm._Gate(pool, FROZEN)
    owner = sorted(model.owners)[-1]
    alone = cm._sample_owner(model, gate, owner, seed=5)
    for other in sorted(model.owners)[:5]:
        cm._sample_owner(model, gate, other, seed=5)
    assert cm._sample_owner(model, gate, owner, seed=5) == alone


def test_the_gate_measures_the_same_neighbours_on_several_processes(monkeypatch):
    monkeypatch.setattr(cm._Gate, "_BLOCK", 16)  # several blocks per schedule on a small pool
    rng = np.random.default_rng(3)
    # A regular schedule, as KMC's are: many cases share one shape.
    pool = [
        {"visits": [{"day": d, "values": {"w": float(1500 + 20 * d + rng.normal(0, 30))}} for d in days]}
        for days in [(0, 1, 3, 7), (0, 2, 4, 8)] * 40
    ]
    one = cm._Gate(pool)._shapes
    several = cm._Gate(pool, processes=3)._shapes
    assert one.keys() == several.keys()
    assert all(len(nn) == 40 for _, _, _, nn in one.values())
    for shape in one:
        np.testing.assert_array_equal(one[shape][3], several[shape][3])
