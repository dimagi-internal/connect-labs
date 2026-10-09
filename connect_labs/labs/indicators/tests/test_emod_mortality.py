"""Under-5 malaria deaths per state and their value on GiveWell's scale (pure functions)."""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators.emod import mortality

STATES = {
    # u5 deaths, MAP all-age malaria deaths, DHS prevalence, under-5 population
    "High": {"u5_deaths": 40_000, "map_malaria_deaths": 5_000, "pfpr": 0.6, "pop_u5": 1_000_000},
    "Low": {"u5_deaths": 15_000, "map_malaria_deaths": 15_000, "pfpr": 0.2, "pop_u5": 1_000_000},
    "Gap": {"u5_deaths": None, "map_malaria_deaths": 1_000, "pfpr": 0.3, "pop_u5": 500_000},
}


def test_prevalence_scaled_spreads_the_national_share_by_prevalence():
    got = mortality.malaria_u5_deaths(STATES)
    national_share = (5_000 + 15_000) * 0.76 / 55_000
    national_pfpr = 0.4
    assert got["High"] == pytest.approx(40_000 * national_share * 0.6 / national_pfpr)
    assert got["Low"] == pytest.approx(15_000 * national_share * 0.2 / national_pfpr)
    assert "Gap" not in got  # no all-cause figure: left out, not guessed
    # Malaria's share of a state's under-5 deaths is proportional to its prevalence.
    assert got["High"] / 40_000 == pytest.approx(3 * got["Low"] / 15_000)


def test_the_share_is_capped():
    states = {
        "Hot": {"u5_deaths": 1_000, "map_malaria_deaths": 50_000, "pfpr": 0.9, "pop_u5": 10},
        "Cold": {"u5_deaths": 100_000, "map_malaria_deaths": 1, "pfpr": 0.01, "pop_u5": 1_000_000},
    }
    assert mortality.malaria_u5_deaths(states)["Hot"] == 1_000 * mortality.MAX_MALARIA_SHARE


def test_map_basis_is_map_deaths_under_five():
    got = mortality.malaria_u5_deaths(STATES, "map")
    assert got == {n: s["map_malaria_deaths"] * 0.76 for n, s in STATES.items()}


def test_an_unknown_basis_is_an_error():
    with pytest.raises(ValueError, match="deaths_basis"):
        mortality.malaria_u5_deaths(STATES, "ihme")


def test_an_empty_registry_gives_no_deaths():
    assert mortality.malaria_u5_deaths({}) == {}


def test_value_against_givewells_benchmark_and_bar():
    # $37,667 a death is GiveWell's 1x (113 units / 0.003 units per $).
    v = mortality.value(deaths_averted=10, spend=376_667)
    assert v["multiple_of_benchmark"] == pytest.approx(1.0, rel=1e-4)
    assert v["cost_per_death_averted"] == pytest.approx(37_666.7)
    assert v["clears_bar"] is False
    assert mortality.value(61, 376_667)["clears_bar"] is True
