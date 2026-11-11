"""GiveWell's valuation has one source: the vendored Connect models."""

import pytest

from connect_labs.impact import engine, valuation
from connect_labs.labs.indicators.emod import mortality


def test_the_three_models_agree_on_the_benchmark():
    got = {m: engine.defaults(m)["benchmark"] for m in engine.MODELS}
    assert len(set(got.values())) == 1, got
    assert valuation.benchmark() == got["chc"]


def test_a_death_is_valued_as_the_chc_model_values_it():
    chc = engine.defaults("chc")
    # moral weight / mortality share x excluded effects, then leveraging (1: no government cost) and funging.
    by_hand = chc["uv"] / chc["mshare"] * chc["excl"] * 1.0 * chc["funging"]
    assert valuation.units_per_death() == pytest.approx(by_hand)
    assert valuation.units_per_death() == pytest.approx(118.0511)
    assert valuation.value_per_death() == chc["uv"] == 113


def test_the_values_are_the_ones_every_published_figure_uses():
    # Moving any of these moves every multiple Labs shows, so a change must be deliberate.
    assert (valuation.benchmark(), valuation.bar()) == (0.003, 6.0)


def test_the_pmc_explorer_values_a_death_the_same_way():
    one_x = valuation.units_per_death() / valuation.benchmark()  # dollars a death at GiveWell's 1x
    got = mortality.value(deaths_averted=4, spend=4 * one_x)
    assert got["multiple_of_benchmark"] == pytest.approx(1.0)
    assert valuation.multiple(4, 4 * one_x) == pytest.approx(1.0)
