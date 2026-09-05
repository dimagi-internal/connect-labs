"""Rainfall seasonality.

The arithmetic here is simple; what is not simple is the two ways it can be
read wrong. A seasonal *shape* can be right while the *level* looks wrong, and
a concentration can be right while meaning something else entirely.
"""

from __future__ import annotations

import numpy as np
import pytest

from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.sources import rainfall as R

pytestmark = pytest.mark.django_db


class TestWettestQuarter:
    """Three CONSECUTIVE months, wrapping the year end."""

    def test_a_single_concentrated_season(self):
        # All the rain in Jun-Aug.
        profile = [0, 0, 0, 0, 0, 100.0, 100.0, 100.0, 0, 0, 0, 0]
        assert R._wettest_quarter_share(profile) == pytest.approx(100.0)

    def test_a_season_straddling_the_new_year_is_still_one_season(self):
        """Southern-hemisphere rain falls Dec-Feb. Treating the profile as a
        flat list rather than a cycle would report that as three separate
        months and understate the concentration badly."""
        profile = [100.0, 100.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 100.0]
        assert R._wettest_quarter_share(profile) == pytest.approx(100.0)

    def test_two_separate_short_seasons_are_not_reported_as_concentrated(self):
        """Taking the three LARGEST months regardless of adjacency would call
        this 100% concentrated. It is bimodal, and a programme planning around
        one closed road window would be planning for the wrong country."""
        profile = [0, 0, 100.0, 100.0, 0, 0, 0, 0, 100.0, 0, 0, 0]
        share = R._wettest_quarter_share(profile)
        assert share == pytest.approx(200.0 / 300.0 * 100.0)
        assert share < 100.0

    def test_an_evenly_watered_place_is_a_quarter(self):
        assert R._wettest_quarter_share([10.0] * 12) == pytest.approx(25.0)

    def test_a_dry_place_yields_nothing_rather_than_dividing_by_zero(self):
        assert R._wettest_quarter_share([0.0] * 12) is None


class TestProfileIsWeightedByPeople:
    def test_empty_cells_do_not_vote_on_when_the_rains_come(self):
        """A district that is nine-tenths desert has its rainy season in the
        tenth where people live."""
        people = np.array([[0.0, 1000.0]])
        # The empty cell is wet in January; the inhabited one in July.
        january = np.array([[500.0, 10.0]])
        july = np.array([[0.0, 400.0]])
        mask = np.ones((1, 2), dtype=bool)

        profile = R._profile(people, [january, july], mask)

        assert profile == pytest.approx([10.0, 400.0])

    def test_a_boundary_with_no_people_yields_nothing_rather_than_zero(self):
        people = np.array([[0.0]])
        assert R._profile(people, [np.array([[100.0]])], np.ones((1, 1), dtype=bool)) is None


class TestRegistration:
    def test_the_peak_month_is_not_targetable(self):
        """Month 12 is not eleven more than month 1, so a threshold over it
        would invite a comparison the number cannot support."""
        assert "rain_peak_month" in measures.MEASURES
        assert "rain_peak_month" not in measures.TARGETABLE

    def test_concentration_is_targetable(self):
        """It is a real logistics constraint independent of the totals."""
        assert "rain_wettest_quarter" in measures.TARGETABLE

    def test_none_of_them_grow_a_coverage_gap(self):
        """Rainfall is not something anyone is unreached by."""
        for code in ("rain_annual_mm", "rain_peak_month", "rain_wettest_quarter"):
            assert measures.get(code).coverage_of is None
            assert f"{code}_gap" not in measures.MEASURES
