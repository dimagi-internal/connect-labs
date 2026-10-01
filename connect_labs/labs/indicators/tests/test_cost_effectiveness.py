"""The ORS cost-effectiveness chain — pinned to the numbers GiveWell published.

The point of reproducing GiveWell's chain rather than writing our own is that a
reviewer can check it against theirs. So the tests are the published columns:
if any parameter or formula moves, the Bauchi column stops reproducing and this
fails before a changed number reaches a funder.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators import cost_effectiveness as ce


def _per_100k(cost_per_child, coverage, mortality):
    """GiveWell reports per 100,000 children reached at its cost per child."""
    return ce.ors_chain(
        spend=cost_per_child * 100_000,
        unit_cost=cost_per_child,
        children_per_household=1.0,
        baseline_coverage=coverage,
        diarrhoea_mortality=mortality,
    )


class TestPublishedColumns:
    def test_units_of_value_per_death_is_givewells(self):
        assert ce.units_of_value_per_death() == pytest.approx(122.9, abs=0.05)

    def test_it_reproduces_givewells_chai_bauchi_column(self):
        """GiveWell CHAI Bauchi: $2.74/child, 45.6% coverage, 6.34/1,000 -> 134.9 deaths, 20.2x."""
        r = _per_100k(2.74, 0.456, 6.34)

        assert r.uptake_gain == pytest.approx(0.174, abs=0.001)
        assert r.non_ors_mortality_per_1000 == pytest.approx(13.05, abs=0.01)
        assert r.deaths_averted == pytest.approx(134.9, abs=0.2)
        assert r.cost_per_death_averted == pytest.approx(2031, abs=3)
        assert r.multiple_of_benchmark == pytest.approx(20.2, abs=0.1)

    def test_it_reproduces_the_borno_central_column_already_sent_to_a_funder(self):
        """'Connect Cholera Questions' Central: $3.33/child, 50%, 4.5 -> 91.3 deaths, 11.2x."""
        r = _per_100k(3.33, 0.50, 4.50)

        assert r.deaths_averted == pytest.approx(91.3, abs=0.2)
        assert r.multiple_of_benchmark == pytest.approx(11.2, abs=0.1)


class TestRound:
    def test_fifty_thousand_at_two_fifty_in_borno(self):
        """The round-two ask, on DHS 2024 coverage and IGME-scaled mortality."""
        mortality = ce.derived_diarrhoea_mortality(92.3, 153.7)
        r = ce.ors_chain(
            spend=50_000,
            unit_cost=2.50,
            children_per_household=1.2,
            baseline_coverage=0.611,
            diarrhoea_mortality=mortality,
        )

        assert mortality == pytest.approx(3.81, abs=0.01)
        assert r.households == 20_000
        assert r.children_reached == 24_000
        assert r.deaths_averted == pytest.approx(15.9, abs=0.1)
        assert r.multiple_of_benchmark == pytest.approx(13.0, abs=0.1)
        assert r.clears_bar is True

    def test_break_even_price_lands_exactly_on_the_bar(self):
        kw = dict(spend=50_000, children_per_household=1.2, baseline_coverage=0.611, diarrhoea_mortality=3.81)
        r = ce.ors_chain(unit_cost=2.50, **kw)

        at_breakeven = ce.ors_chain(unit_cost=r.breakeven_unit_cost, **kw)

        assert at_breakeven.multiple_of_benchmark == pytest.approx(ce.BAR.value, rel=1e-9)

    def test_higher_baseline_coverage_lowers_the_return(self):
        """The counterfactual is the point: where ORS is already used, a campaign adds less."""
        kw = dict(spend=50_000, unit_cost=2.50, children_per_household=1.2, diarrhoea_mortality=3.81)

        low = ce.ors_chain(baseline_coverage=0.40, **kw)
        high = ce.ors_chain(baseline_coverage=0.80, **kw)

        assert high.deaths_averted < low.deaths_averted

    def test_the_sensitivity_grid_contains_the_quoted_answer(self):
        grid = ce.sensitivity(
            spend=50_000, children_per_household=1.2, diarrhoea_mortality=3.81, baseline_coverage=0.611
        )

        col = grid["baseline_coverages"].index(0.611)
        row = grid["unit_costs"].index(2.50)
        assert grid["multiples"][row][col] == pytest.approx(13.0, abs=0.1)

    @pytest.mark.parametrize(
        "bad",
        [
            dict(spend=0),
            dict(unit_cost=0),
            dict(children_per_household=0),
            dict(baseline_coverage=1.0),
            dict(diarrhoea_mortality=0),
        ],
    )
    def test_impossible_inputs_are_refused(self, bad):
        kw = dict(
            spend=50_000, unit_cost=2.5, children_per_household=1.2, baseline_coverage=0.5, diarrhoea_mortality=4
        )
        kw.update(bad)
        with pytest.raises(ValueError):
            ce.ors_chain(**kw)

    def test_every_parameter_names_its_source(self):
        params = ce.parameters()

        assert params["ors_mortality_effect"]["value"] == 0.595
        assert all(p["source"] for p in params.values())
