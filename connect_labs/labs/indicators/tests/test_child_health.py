"""Diarrhoea, ORS and breastfeeding — and targeting on them.

These bring a second failure mode the mortality path never had: a *coverage*
indicator, where a low value is the problem. Thresholding above one would select
the places already doing well.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.resolve import select_above
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

pytestmark = pytest.mark.django_db


@pytest.fixture
def ors_country():
    make_boundary("NGA", 0, "Nigeria", "NGA-0")
    bad = make_boundary("NGA", 1, "Kaduna", "NGA-1", x=2)
    good = make_boundary("NGA", 1, "Lagos", "NGA-2", x=4)

    # Kaduna: lots of diarrhoea, little ORS. Lagos: the reverse.
    set_value(bad, "diarrhoea_prevalence", 33.0, source=Source.DHS)
    set_value(bad, "ors_coverage", 30.0, source=Source.DHS)
    set_value(bad, "pop_u5", 1_000_000, source=Source.WORLDPOP)

    set_value(good, "diarrhoea_prevalence", 8.0, source=Source.DHS)
    set_value(good, "ors_coverage", 80.0, source=Source.DHS)
    set_value(good, "pop_u5", 1_000_000, source=Source.WORLDPOP)
    return {"bad": bad, "good": good}


class TestRegistry:
    def test_coverage_measures_are_marked_lower_is_worse(self):
        assert "ors_coverage" in measures.LOWER_IS_WORSE
        assert "exclusive_breastfeeding" in measures.LOWER_IS_WORSE
        assert "u5mr" not in measures.LOWER_IS_WORSE

    def test_every_targetable_measure_is_a_rate_with_its_own_scale(self):
        for m in measures.targetable():
            assert m.is_rate, f"{m.code} is a count; counts are outcomes, not criteria"
            assert m.threshold_min < m.threshold_default < m.threshold_max

    def test_prevalence_weights_by_the_population_it_describes(self):
        assert measures.get("diarrhoea_prevalence").weight_by == "pop_u5"
        # Breastfeeding describes infants, so it weights by the birth cohort.
        assert measures.get("exclusive_breastfeeding").weight_by == "births"


class TestOrsGap:
    def test_gap_is_children_with_diarrhoea_and_no_ors(self, ors_country):
        from connect_labs.labs.indicators.sources import derive

        rows = {r.boundary.name: r.value for r in derive.load_ors_gap(iso_codes=["NGA"])}

        # 1,000,000 x 33% x (1 - 30%)
        assert rows["Kaduna"] == pytest.approx(1_000_000 * 0.33 * 0.70)
        assert rows["Lagos"] == pytest.approx(1_000_000 * 0.08 * 0.20)
        assert rows["Kaduna"] > rows["Lagos"]

    def test_no_ors_reading_produces_no_gap_rather_than_a_guess(self):
        from connect_labs.labs.indicators.sources import derive

        make_boundary("TCD", 0, "Chad", "TCD-0", x=20)
        r = make_boundary("TCD", 1, "Region", "TCD-1", x=22)
        set_value(r, "diarrhoea_prevalence", 20.0, source=Source.DHS)
        set_value(r, "pop_u5", 500_000, source=Source.WORLDPOP)
        # no ors_coverage

        # Treating an absent coverage figure as zero would invent a gap the
        # size of every sick child in the region.
        assert derive.load_ors_gap(iso_codes=["TCD"]) == []


class TestLowerIsWorseSelection:
    def test_a_coverage_indicator_selects_below_the_threshold(self, ors_country):
        sel = select_above("ors_coverage", threshold=50, iso_codes=["NGA"], method="subnational_survey")

        # Kaduna at 30% is the problem; Lagos at 80% is not.
        assert {a.name for a in sel.areas} == {"Kaduna"}

    def test_a_prevalence_indicator_still_selects_above(self, ors_country):
        sel = select_above(
            "diarrhoea_prevalence",
            threshold=15,
            iso_codes=["NGA"],
            method="subnational_survey",
        )

        assert {a.name for a in sel.areas} == {"Kaduna"}

    def test_the_two_directions_do_not_select_the_same_place_by_accident(self, ors_country):
        low_coverage = select_above("ors_coverage", threshold=50, iso_codes=["NGA"], method="subnational_survey")
        high_coverage = select_above("ors_coverage", threshold=90, iso_codes=["NGA"], method="subnational_survey")

        # Raising a lower-is-worse threshold widens the selection, the opposite
        # of a mortality threshold. Counted in units, not rows: at 90% both
        # regions qualify and collapse into a single whole-country row.
        assert high_coverage.unit_count > low_coverage.unit_count
        assert high_coverage.areas[0].is_whole_country is True


class TestGenericCoverageGaps:
    """Every coverage measure gets an unreached count without bespoke code."""

    def test_a_gap_measure_exists_for_every_coverage_measure(self):
        for m in measures.coverage_measures():
            assert f"{m.code}_gap" in measures.MEASURES, m.code

    def test_gap_measures_are_summable_counts(self):
        for m in measures.coverage_measures():
            gap = measures.get(f"{m.code}_gap")
            assert gap.agg is measures.Agg.SUM
            assert not gap.downscale

    def test_unreached_is_denominator_times_the_shortfall(self):
        from connect_labs.labs.indicators.sources import derive

        make_boundary("NGA", 0, "Nigeria", "NGA-0")
        r = make_boundary("NGA", 1, "Kano", "NGA-1", x=2)
        set_value(r, "measles_vaccination", 40.0, source=Source.DHS)
        set_value(r, "births", 500_000, source=Source.DERIVED)

        rows = {x.indicator: x.value for x in derive.load_coverage_gaps(iso_codes=["NGA"])}
        # 500,000 births x (1 - 40%)
        assert rows["measles_vaccination_gap"] == pytest.approx(300_000)

    def test_absent_coverage_produces_no_gap(self):
        from connect_labs.labs.indicators.sources import derive

        make_boundary("NGA", 0, "Nigeria", "NGA-0")
        r = make_boundary("NGA", 1, "Kano", "NGA-1", x=2)
        set_value(r, "births", 500_000, source=Source.DERIVED)
        # no coverage reading at all

        gaps = [x for x in derive.load_coverage_gaps(iso_codes=["NGA"]) if x.indicator == "measles_vaccination_gap"]
        # Assuming zero coverage would invent an unreached population the size
        # of the entire denominator.
        assert gaps == []

    def test_selection_carries_only_the_relevant_gap(self):
        from connect_labs.labs.indicators.resolve import carried_for

        carried = carried_for("measles_vaccination")
        assert "measles_vaccination_gap" in carried
        # Resolving all eleven gaps on every query to display one would be
        # eleven lookups per boundary.
        assert "improved_water_gap" not in carried

    def test_a_prevalence_indicator_carries_no_gap(self):
        from connect_labs.labs.indicators.resolve import carried_for

        # Stunting is a prevalence, not a coverage figure — there is no
        # "unreached" population to compute.
        assert "stunting_gap" not in carried_for("stunting")
