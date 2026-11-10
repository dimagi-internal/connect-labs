"""GiveWell's valuation has one source: the vendored Connect models."""

from connect_labs.impact import engine, valuation
from connect_labs.labs.indicators import cost_effectiveness as ors_chain


def test_the_three_models_agree_on_the_benchmark():
    got = {m: engine.defaults(m)["benchmark"] for m in engine.MODELS}
    assert len(set(got.values())) == 1, got
    assert valuation.benchmark() == got["chc"]


def test_the_ors_and_pmc_constants_are_the_models_own():
    assert ors_chain.MORAL_WEIGHT.value == valuation.value_per_death() == engine.defaults("chc")["uv"]
    assert ors_chain.BENCHMARK_UOV_PER_DOLLAR.value == valuation.benchmark()
    assert ors_chain.BAR.value == valuation.bar()


def test_the_values_are_the_ones_every_published_figure_used():
    # Moving any of these moves every multiple Labs shows, so a change must be deliberate.
    assert (valuation.value_per_death(), valuation.benchmark(), valuation.bar()) == (113.0, 0.003, 6.0)
