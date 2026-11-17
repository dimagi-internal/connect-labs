"""A place's mortality carries its published interval through to the multiple, the plan and how far the plan holds."""

import pytest

from connect_labs.effectiveness import baseline, market, people, reliability
from connect_labs.effectiveness.tests.test_place_risk import selection

pytestmark = pytest.mark.django_db


def bound(boundary, indicator, low, high, level=0.90):
    boundary.indicator_values.filter(indicator=indicator).update(ci_low=low, ci_high=high, extra={"interval_level": level})


def add_intervals(nigeria):
    """IGME's bounds on the Nigeria fixture's national newborn mortality and on Kano's."""
    bound(nigeria["ng"], "nmr", 24.0, 38.0)
    bound(nigeria["kano"], "nmr", 20.0, 70.0)
    return nigeria


class TestWhatTheBaselineKeeps:
    def test_a_territory_with_a_published_interval_has_a_standard_error_and_shares_the_countrys(self, nigeria):
        add_intervals(nigeria)
        got = baseline.Baselines([nigeria["kano"]]).newborn_mortality("NGA", nigeria["kano"])
        assert got.kind == "own" and got.se > got.shared_se > 0

    def test_without_an_interval_there_is_no_standard_error_rather_than_a_guess(self, nigeria):
        got = baseline.Baselines([nigeria["kano"]]).newborn_mortality("NGA", nigeria["kano"])
        assert got.se is None and got.shared_se is None

    def test_a_figure_made_from_the_countrys_carries_both_errors(self, nigeria):
        add_intervals(nigeria)
        bound(nigeria["lagos"], "u5mr", 35.0, 70.0)
        got = baseline.Baselines([nigeria["lagos"]]).newborn_mortality("NGA", nigeria["lagos"])
        assert got.kind == "ratio" and got.se > got.shared_se > 0


def sized(nigeria, target=None, **kw):
    found = people.attach_risk(people.group_selection(selection(nigeria), "territory"))
    return market.size("kc", people.for_model(found, "kc"), None, 5.0, target=target, **kw)


class TestTheMarketsRange:
    def test_a_place_with_an_interval_has_a_range_around_its_multiple(self, nigeria):
        kano = next(r for r in sized(add_intervals(nigeria))["rows"] if r["name"] == "Kano")
        assert kano["multiple_low"] < kano["multiple"] < kano["multiple_high"]
        assert kano["multiple_elasticity"] > 0 and kano["deaths_elasticity"] > 0

    def test_a_place_without_one_has_no_range(self, nigeria):
        plain = next(r for r in sized(nigeria)["rows"] if r["name"] == "Kano")
        assert "multiple_low" not in plain and plain["risk_se"] is None

    def test_the_interval_does_not_move_the_multiple_itself(self, nigeria):
        a = next(r for r in sized(nigeria)["rows"] if r["name"] == "Kano")["multiple"]
        for row in nigeria["kano"].indicator_values.filter(indicator="nmr"):
            row.ci_low, row.ci_high = 20.0, 70.0
            row.save()
        b = next(r for r in sized(nigeria)["rows"] if r["name"] == "Kano")["multiple"]
        assert a == pytest.approx(b)

    def test_a_plan_reports_how_far_it_holds_only_when_there_is_something_to_redraw(self, nigeria):
        assert sized(nigeria, 150_000)["plan"]["reliability"] is None
        got = sized(add_intervals(nigeria), 150_000)["plan"]["reliability"]
        assert 0 <= got["overlap"] <= 1 and got["deaths"]["p05"] <= got["deaths"]["p50"] <= got["deaths"]["p95"]


class TestShrinkageIsAppliedPerCountry:
    def test_only_figures_measured_below_the_country_are_shrunk(self):
        places = {i: {"iso3": "NGA"} for i in range(10)}
        got = {
            i: baseline.Nmr(10.0 + 5 * i, "x", 0.25, 0.1, "own" if i < 9 else "ratio") for i in range(10)
        }
        shrunk = people._shrink_by_country(places, got)
        assert set(shrunk) == set(range(9))


class TestReliability:
    def row(self, key, multiple, units=100.0, se=0.3, iso="AAA", shared=0.0, name=None):
        return {
            "key": key,
            "name": name or key,
            "iso3": iso,
            "units": units,
            "multiple": multiple,
            "deaths_per_person": 0.001,
            "risk_se": se,
            "risk_shared_se": shared,
            "multiple_elasticity": 1.0,
            "deaths_elasticity": 1.0,
        }

    def test_nothing_to_redraw_is_none(self):
        assert reliability.assess([{**self.row("a", 10.0), "risk_se": None}], 50) is None

    def test_a_clear_winner_is_always_in_the_plan_and_a_marginal_one_is_not(self):
        rows = [self.row("sure", 40.0), self.row("near", 10.2), self.row("also", 10.0), self.row("low", 2.0)]
        got = reliability.assess(rows, 200)
        assert got["p_in_plan"]["sure"] == 1.0 and got["p_in_plan"]["low"] < 0.05
        assert 0.2 < got["p_in_plan"]["near"] < 0.95 and 0.2 < got["p_in_plan"]["also"] < 0.95

    def test_wider_intervals_mean_a_less_reliable_plan(self):
        def overlap(se):
            rows = [self.row(k, m, se=se) for k, m in {"a": 10.0, "b": 9.5, "c": 9.0, "d": 8.5}.items()]
            return reliability.assess(rows, 200)["overlap"]

        assert overlap(0.05) > overlap(0.6)

    def test_it_is_deterministic(self):
        rows = [self.row("a", 10.0), self.row("b", 9.5), self.row("c", 9.0)]
        assert reliability.assess(rows, 150) == reliability.assess(rows, 150)

    def test_territories_of_a_country_share_the_national_error(self):
        # Two places, one per country, against two in one country: a shared national error moves them together, so the
        # ranking between them is steadier than when they err independently.
        def steadiness(iso_b, shared):
            rows = [self.row("a", 10.0, iso="AAA", shared=shared, se=0.4), self.row("b", 9.8, iso=iso_b, shared=shared, se=0.4)]
            return reliability.assess(rows, 100)["p_in_plan"]["a"]

        together = steadiness("AAA", 0.4)
        apart = steadiness("BBB", 0.4)
        assert abs(together - 1.0) < abs(apart - 1.0) or together > apart
