"""The statistics behind a place's mortality range: shrinkage, benchmarking, and the interval arithmetic."""

import math

import pytest

from connect_labs.effectiveness import mortality
from connect_labs.effectiveness.mortality import Area


def areas(values, se=0.2, weights=None):
    return [Area(i, v, se, (weights or {}).get(i, 1.0)) for i, v in enumerate(values)]


class TestTheIntervalArithmetic:
    def test_a_published_interval_gives_a_standard_error_on_the_log_scale(self):
        # Zimbabwe 2024 neonatal mortality: 33.7 with IGME's 90% bounds of 22.5 and 49.7
        se = mortality.se_ln(22.5, 49.7, 0.90)
        assert se == pytest.approx((math.log(49.7) - math.log(22.5)) / (2 * 1.6449), rel=1e-6)
        low, high = mortality.interval(33.7, se, 0.90)
        assert 22 < low < 25 and 46 < high < 52  # about the published 22.5 and 49.7, though the interval is symmetric in logs

    def test_the_same_bounds_read_as_a_95_percent_interval_mean_a_smaller_standard_error(self):
        assert mortality.se_ln(20, 50, 0.95) < mortality.se_ln(20, 50, 0.90)

    def test_a_bound_at_zero_does_not_break_the_log(self):
        assert mortality.se_ln(0.0, 7.0, 0.95) == pytest.approx((math.log(7.0) - math.log(mortality.FLOOR)) / (2 * 1.96))
        assert mortality.se_ln(-3.0, 7.0, 0.95) > 0

    @pytest.mark.parametrize("low, high", [(None, 5.0), (5.0, None), (None, None), (3.0, 0.0)])
    def test_no_usable_interval_is_none_not_a_guess(self, low, high):
        assert mortality.se_ln(low, high) is None

    def test_errors_add_in_quadrature_and_unknown_ones_are_skipped(self):
        assert mortality.combine(0.3, 0.4) == pytest.approx(0.5)
        assert mortality.combine(0.3, None) == pytest.approx(0.3)
        assert mortality.combine(None, None) is None

    def test_an_interval_is_multiplicative_so_it_is_wider_above_than_below(self):
        low, high = mortality.interval(30.0, 0.3, 0.90)
        assert high - 30 > 30 - low and low * high == pytest.approx(30.0**2, rel=1e-9)


class TestShrinkage:
    def test_a_country_with_too_few_territories_is_left_alone(self):
        got = mortality.shrink(areas([10, 20, 30, 40]), min_areas=8)
        assert [e.value for e in got.values()] == [10, 20, 30, 40] and not any(e.shrunk for e in got.values())

    def test_a_territory_with_no_interval_is_left_alone_even_among_others_that_have_one(self):
        rows = areas([20, 25, 30, 35, 40, 28, 33, 38]) + [Area("none", 5.0, None)]
        got = mortality.shrink(rows)
        assert got["none"].value == 5.0 and got["none"].shrunk is False and got[0].shrunk

    def test_a_noisy_extreme_is_pulled_a_long_way_in_and_a_firm_one_hardly_at_all(self):
        # Nine territories around 30. One reads 3 with a wide interval (Ondo), one reads 55 with a tight one (Kano).
        rows = [Area(i, v, 0.12) for i, v in enumerate([28, 31, 30, 33, 29, 32, 27])]
        rows += [Area("ondo", 3.0, 1.0), Area("kano", 55.0, 0.10)]
        got = mortality.shrink(rows)
        assert got["ondo"].value > 20 and got["ondo"].pull > 0.9  # almost entirely the country's value
        assert got["kano"].value == pytest.approx(55.0, rel=0.1) and got["kano"].pull < 0.5

    def test_shrinking_never_moves_a_value_outside_the_range_of_the_figures_and_never_raises_its_error(self):
        rows = areas([12, 18, 25, 31, 44, 21, 29, 37, 15], se=0.25)
        got = mortality.shrink(rows)
        assert all(12 <= e.value <= 44 for e in got.values())
        assert all(e.se <= 0.25 + 1e-12 for e in got.values())

    def test_territories_that_really_differ_a_lot_are_barely_shrunk(self):
        # a wide, real spread with tight intervals: the figures are believed
        rows = areas([8, 15, 30, 60, 120, 20, 45, 90], se=0.05)
        got = mortality.shrink(rows)
        assert all(abs(e.value / e.raw - 1) < 0.05 for e in got.values())

    def test_territories_that_agree_collapse_toward_the_country_but_not_to_a_point(self):
        rows = areas([29.0, 30.0, 31.0, 30.5, 29.5, 30.2, 29.8, 30.1], se=0.3)
        got = mortality.shrink(rows)
        assert max(e.pull for e in got.values()) > 0.9
        assert len({round(e.value, 6) for e in got.values()}) > 1  # the floor on tau keeps them distinct

    def test_the_result_does_not_depend_on_the_order_the_territories_come_in(self):
        rows = areas([12, 18, 25, 31, 44, 21, 29, 37, 15], se=0.25)
        a = mortality.shrink(rows)
        b = mortality.shrink(list(reversed(rows)))
        assert all(a[k].value == pytest.approx(b[k].value) for k in a)
