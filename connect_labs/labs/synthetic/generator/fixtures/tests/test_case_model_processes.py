"""A case pool is the same however many processes sample it."""

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
    owner = sorted(model.owners)[-1]
    alone = cm._sample_owner(model, owner, seed=5)
    for other in sorted(model.owners)[:5]:
        cm._sample_owner(model, other, seed=5)
    assert cm._sample_owner(model, owner, seed=5) == alone


def _die(_):
    import os

    os._exit(1)  # as a child killed for memory does


def test_a_process_that_dies_hands_the_work_back_instead_of_failing():
    assert cm._map_forked(_die, range(4), processes=2) is None
