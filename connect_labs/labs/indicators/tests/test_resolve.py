"""Tests for the parts where being wrong is silent.

Aggregation and inheritance are the two places this system can produce a
confident, plausible, wrong continental number. Everything here is aimed at
those two.
"""

from __future__ import annotations

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.utils import timezone

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.models import IndicatorValue, License, Source
from connect_labs.labs.indicators.resolve import BulkResolver, aggregate, resolve, select_above

pytestmark = pytest.mark.django_db


def _square(x: float, y: float) -> MultiPolygon:
    return MultiPolygon(Polygon(((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1), (x, y))), srid=4326)


def make_boundary(iso: str, level: int, name: str, bid: str, x: float = 0.0) -> AdminBoundary:
    return AdminBoundary.objects.create(
        iso_code=iso,
        admin_level=level,
        name=name,
        boundary_id=bid,
        geometry=_square(x, 0),
        source=AdminBoundary.Source.GEOBOUNDARIES,
    )


def set_value(boundary, indicator, value, year=2024, source=Source.DHS):
    return IndicatorValue.objects.create(
        indicator=indicator,
        boundary=boundary,
        iso_code=boundary.iso_code,
        admin_level=boundary.admin_level,
        year=year,
        value=value,
        source=source,
        source_ref=f"{source}-{year}",
        license_code=License.OPEN_API,
        retrieved_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_every_rate_declares_a_weight_that_is_a_count(self):
        measures.validate_registry()
        for m in measures.MEASURES.values():
            if m.is_rate:
                assert m.weight_by, f"{m.code} has no weight"
                assert not measures.get(m.weight_by).is_rate

    def test_u5mr_is_weighted_by_births_not_population(self):
        # Weighting a mortality rate by total population biases a continental
        # figure toward places with few children. This is the guard against
        # someone "simplifying" it later.
        assert measures.get("u5mr").weight_by == "births"

    def test_counts_never_inherit_downward(self):
        for m in measures.MEASURES.values():
            if not m.is_rate:
                assert not m.downscale, f"{m.code} would fabricate population"

    def test_registering_a_summed_rate_is_rejected(self):
        with pytest.raises(ValueError, match="weighted mean"):
            measures.register(
                measures.Measure(
                    code="bogus_rate",
                    label="Bogus",
                    kind=measures.Kind.RATE,
                    unit="per 1,000",
                    agg=measures.Agg.SUM,
                    weight_by="births",
                )
            )

    def test_registering_a_rate_without_a_weight_is_rejected(self):
        with pytest.raises(ValueError, match="weight_by"):
            measures.register(
                measures.Measure(
                    code="bogus_rate_2",
                    label="Bogus",
                    kind=measures.Kind.RATE,
                    unit="per 1,000",
                    agg=measures.Agg.WEIGHTED_MEAN,
                )
            )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_counts_sum(self):
        assert aggregate("births", [(100, None), (250, None)]) == 350

    def test_rates_take_a_weighted_mean_not_a_sum(self):
        # Two regions: one small with terrible mortality, one large with good.
        # The naive sum would be 220; the mean 110; the truthful answer is
        # dominated by the larger birth cohort.
        got = aggregate("u5mr", [(180, 1_000), (40, 9_000)])
        assert got == pytest.approx((180 * 1000 + 40 * 9000) / 10000)
        assert got == pytest.approx(54.0)

    def test_rate_falls_back_to_unweighted_mean_when_weights_missing(self):
        assert aggregate("u5mr", [(100, None), (200, None)]) == pytest.approx(150.0)

    def test_empty_returns_none(self):
        assert aggregate("births", []) is None


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


class TestInheritance:
    def test_region_without_a_rate_inherits_from_its_country(self):
        country = make_boundary("XXA", 0, "Testland", "XXA-0")
        region = make_boundary("XXA", 1, "Northern", "XXA-1", x=2)
        set_value(country, "u5mr", 95.0, source=Source.IGME)

        got = resolve("u5mr", region)

        assert got is not None
        assert got.value == 95.0
        assert got.inherited is True
        assert got.measured_at.pk == country.pk
        assert "Testland" in got.provenance

    def test_a_regions_own_survey_beats_the_national_figure(self):
        country = make_boundary("XXB", 0, "Testland B", "XXB-0")
        region = make_boundary("XXB", 1, "Northern", "XXB-1", x=2)
        set_value(country, "u5mr", 95.0, source=Source.IGME)
        set_value(region, "u5mr", 143.0, source=Source.DHS)

        got = resolve("u5mr", region)

        assert got.value == 143.0
        assert got.inherited is False

    def test_population_never_inherits(self):
        country = make_boundary("XXC", 0, "Testland C", "XXC-0")
        region = make_boundary("XXC", 1, "Northern", "XXC-1", x=2)
        set_value(country, "pop_u5", 5_000_000, source=Source.WORLDPOP)

        # A region must not acquire its country's population.
        assert resolve("pop_u5", region) is None

    def test_bulk_resolver_agrees_with_single_resolve(self):
        country = make_boundary("XXD", 0, "Testland D", "XXD-0")
        r1 = make_boundary("XXD", 1, "North", "XXD-1", x=2)
        r2 = make_boundary("XXD", 1, "South", "XXD-2", x=4)
        set_value(country, "u5mr", 90.0, source=Source.IGME)
        set_value(r1, "u5mr", 150.0, source=Source.DHS)

        bulk = BulkResolver([r1, r2])

        assert bulk.get("u5mr", r1).value == resolve("u5mr", r1).value == 150.0
        assert bulk.get("u5mr", r2).value == resolve("u5mr", r2).value == 90.0
        assert bulk.get("u5mr", r2).inherited is True


# ---------------------------------------------------------------------------
# Threshold selection and the coarsest-unit rule
# ---------------------------------------------------------------------------


@pytest.fixture
def two_countries():
    """One country entirely above threshold, one only partly."""
    all_above = make_boundary("XXE", 0, "Allabove", "XXE-0")
    a1 = make_boundary("XXE", 1, "A-North", "XXE-1", x=2)
    a2 = make_boundary("XXE", 1, "A-South", "XXE-2", x=4)

    partly = make_boundary("XXF", 0, "Partly", "XXF-0", x=6)
    p1 = make_boundary("XXF", 1, "P-North", "XXF-1", x=8)
    p2 = make_boundary("XXF", 1, "P-South", "XXF-2", x=10)

    for b, rate, births in [(a1, 120, 10_000), (a2, 110, 20_000), (p1, 130, 30_000), (p2, 40, 40_000)]:
        set_value(b, "u5mr", rate)
        set_value(b, "births", births, source=Source.DERIVED)
        set_value(b, "pop_u5", births * 5, source=Source.WORLDPOP)
        set_value(b, "pop_total", births * 30, source=Source.WORLDPOP)

    return {"all_above": all_above, "partly": partly, "a1": a1, "a2": a2, "p1": p1, "p2": p2}


class TestSelectAbove:
    def test_country_with_every_region_above_rolls_up_to_one_row(self, two_countries):
        sel = select_above("u5mr", threshold=80, iso_codes=["XXE"])

        assert len(sel.areas) == 1
        row = sel.areas[0]
        assert row.is_whole_country is True
        assert row.name == "Allabove"
        assert row.units_covered == 2
        # Counts come from the regions, not from a separate national row.
        assert row.counts["births"] == 30_000

    def test_country_only_partly_above_lists_its_qualifying_regions(self, two_countries):
        sel = select_above("u5mr", threshold=80, iso_codes=["XXF"])

        assert len(sel.areas) == 1
        row = sel.areas[0]
        assert row.is_whole_country is False
        assert row.name == "P-North"
        assert row.counts["births"] == 30_000
        assert "Partly" in sel.countries_partly_above

    def test_totals_sum_counts_and_weight_the_rate(self, two_countries):
        sel = select_above("u5mr", threshold=80)

        assert sel.totals["births"] == 60_000  # 10k + 20k + 30k
        # Weighted by births: (120*10k + 110*20k + 130*30k) / 60k
        expected = (120 * 10_000 + 110 * 20_000 + 130 * 30_000) / 60_000
        assert sel.totals["u5mr"] == pytest.approx(expected)
        # Emphatically not the sum of the rates.
        assert sel.totals["u5mr"] < 200

    def test_raising_the_threshold_shrinks_the_selection(self, two_countries):
        low = select_above("u5mr", threshold=80)
        high = select_above("u5mr", threshold=125)

        assert low.totals["births"] > high.totals["births"]
        assert high.unit_count < low.unit_count

    def test_threshold_above_everything_selects_nothing(self, two_countries):
        sel = select_above("u5mr", threshold=500)
        assert sel.areas == []
        assert sel.totals["births"] is None

    def test_country_with_no_mortality_data_is_reported_not_silently_dropped(self):
        make_boundary("XXG", 0, "Nodata", "XXG-0")
        make_boundary("XXG", 1, "N-North", "XXG-1", x=2)

        sel = select_above("u5mr", threshold=80, iso_codes=["XXG"])

        assert sel.areas == []
        assert "Nodata" in sel.skipped_no_data

    def test_single_region_country_is_not_relabelled_as_whole_country(self):
        # A one-region country trivially has "all regions above", but calling
        # the row a whole-country rollup overstates what was checked.
        make_boundary("XXH", 0, "Solo", "XXH-0")
        only = make_boundary("XXH", 1, "Only Region", "XXH-1", x=2)
        set_value(only, "u5mr", 150)
        set_value(only, "births", 5_000, source=Source.DERIVED)

        sel = select_above("u5mr", threshold=80, iso_codes=["XXH"])

        assert len(sel.areas) == 1
        assert sel.areas[0].is_whole_country is False
        assert sel.areas[0].name == "Only Region"


# ---------------------------------------------------------------------------
# Name matching — where a wrong answer is worse than no answer
# ---------------------------------------------------------------------------


class TestBoundaryMatcher:
    def test_accents_are_folded(self):
        from connect_labs.labs.indicators.sources.base import normalize_name

        assert normalize_name("Tillabéri") == normalize_name("Tillaberi")
        assert normalize_name("Ségou") == normalize_name("Segou")

    def test_dhs_nesting_prefix_is_stripped(self):
        from connect_labs.labs.indicators.sources.base import normalize_name

        assert normalize_name("..Benue") == "benue"

    def test_alias_values_are_already_normalised(self):
        # An alias whose value still carries punctuation or accents can never
        # match, because boundary keys go through normalisation too.
        from connect_labs.labs.indicators.sources.base import ALIASES, normalize_name

        for key, value in ALIASES.items():
            assert normalize_name(value) == value, f"alias {key!r} -> {value!r} is not normalised"

    def test_qualifier_variant_matches_by_token_subset(self):
        from connect_labs.labs.indicators.sources.base import BoundaryMatcher

        make_boundary("XXI", 1, "Tharaka", "XXI-1")
        matcher = BoundaryMatcher("XXI", 1)

        assert matcher.match("..Tharaka-Nithi").name == "Tharaka"

    def test_short_qualifier_cannot_capture_a_different_region(self):
        from connect_labs.labs.indicators.sources.base import BoundaryMatcher

        make_boundary("XXJ", 1, "North West", "XXJ-1")
        make_boundary("XXJ", 1, "North East", "XXJ-2", x=2)
        matcher = BoundaryMatcher("XXJ", 1)

        # "North" is a qualifier shared by two regions — matching either would
        # attach a mortality rate to the wrong population.
        assert matcher.match("North") is None

    def test_unmatched_labels_are_recorded_not_guessed(self):
        from connect_labs.labs.indicators.sources.base import BoundaryMatcher

        make_boundary("XXK", 1, "Kano", "XXK-1")
        matcher = BoundaryMatcher("XXK", 1)

        assert matcher.match("Somewhere Else Entirely") is None
        assert "Somewhere Else Entirely" in matcher.misses


class TestMissingIsNotZero:
    """A count we could not estimate must never render as 0.

    Found by looking at the real page: South Sudan showed "births 0" beside an
    under-5 population of 2.3 million. The regions simply had no births
    estimate, and every one of them contributed 0 to the continental headline —
    an undercount presented as a measurement.
    """

    def test_area_with_no_births_reports_none_not_zero(self):
        make_boundary("XZA", 0, "Nodata", "XZA-0")
        r1 = make_boundary("XZA", 1, "R1", "XZA-1", x=2)
        set_value(r1, "u5mr", 150)
        set_value(r1, "pop_u5", 500_000, source=Source.WORLDPOP)
        # deliberately no births

        sel = select_above("u5mr", threshold=80, iso_codes=["XZA"])

        assert sel.areas[0].counts["births"] is None
        assert sel.areas[0].counts["pop_u5"] == 500_000

    def test_total_is_none_when_nothing_has_a_births_estimate(self):
        make_boundary("XZB", 0, "Nodata B", "XZB-0")
        r1 = make_boundary("XZB", 1, "R1", "XZB-1", x=2)
        set_value(r1, "u5mr", 150)

        sel = select_above("u5mr", threshold=80, iso_codes=["XZB"])

        assert sel.totals["births"] is None

    def test_coverage_reports_the_shortfall(self):
        make_boundary("XZC", 0, "Partial", "XZC-0")
        r1 = make_boundary("XZC", 1, "Has", "XZC-1", x=2)
        r2 = make_boundary("XZC", 1, "Hasnt", "XZC-2", x=4)
        for b in (r1, r2):
            set_value(b, "u5mr", 150)
        set_value(r1, "births", 10_000, source=Source.DERIVED)

        sel = select_above("u5mr", threshold=80, iso_codes=["XZC"])

        assert sel.totals["births"] == 10_000
        assert sel.coverage["births"] == (1, 2)
        assert sel.is_complete("births") is False
        assert sel.missing_units("births") == 1

    def test_rolled_up_country_row_flags_partial_births(self):
        make_boundary("XZD", 0, "Rollup", "XZD-0")
        r1 = make_boundary("XZD", 1, "R1", "XZD-1", x=2)
        r2 = make_boundary("XZD", 1, "R2", "XZD-2", x=4)
        for b in (r1, r2):
            set_value(b, "u5mr", 150)
        set_value(r1, "births", 7_000, source=Source.DERIVED)

        sel = select_above("u5mr", threshold=80, iso_codes=["XZD"])

        row = sel.areas[0]
        assert row.is_whole_country is True
        # The sum of what we have, not a confident figure for the whole country.
        assert row.counts["births"] == 7_000
        assert row.is_complete("births") is False
        assert row.coverage["births"] == (1, 2)

    def test_complete_selection_reports_complete(self):
        make_boundary("XZE", 0, "Full", "XZE-0")
        r1 = make_boundary("XZE", 1, "R1", "XZE-1", x=2)
        set_value(r1, "u5mr", 150)
        set_value(r1, "births", 3_000, source=Source.DERIVED)

        sel = select_above("u5mr", threshold=80, iso_codes=["XZE"])

        assert sel.is_complete("births") is True
        assert sel.missing_units("births") == 0


class TestCalibration:
    """Old surveys are re-levelled to the present.

    Found by asking why Uganda's figure was dated 2016: a third of the
    continent's subnational mortality came from surveys 8+ years old, and nine
    countries were selected as high-mortality whose current national rate was
    already below the threshold.
    """

    def _country_with_series(self, iso, survey_year, survey_value, then, now, now_year=2024):
        """A country with a survey and the two IGME endpoints the factor needs."""
        assert survey_year != now_year, "the two IGME endpoints must be distinct years"
        country = make_boundary(iso, 0, f"{iso}land", f"{iso}-0")
        region = make_boundary(iso, 1, "Region", f"{iso}-1", x=2)
        set_value(region, "u5mr", survey_value, year=survey_year, source=Source.DHS)
        set_value(country, "u5mr", then, year=survey_year, source=Source.IGME)
        set_value(country, "u5mr", now, year=now_year, source=Source.IGME)
        return country, region

    def test_a_falling_trend_scales_the_survey_down(self):
        from connect_labs.labs.indicators.sources import calibrate

        _, region = self._country_with_series("ERI", 2002, 154.0, then=77.3, now=34.3)
        rows = calibrate.load("u5mr", iso_codes=["ERI"])

        assert len(rows) == 1
        # 154 * (34.3 / 77.3) ~= 68
        assert rows[0].value == pytest.approx(154.0 * 34.3 / 77.3)
        assert rows[0].value < 80
        assert rows[0].extra["raw_value"] == 154.0
        assert rows[0].extra["raw_year"] == 2002

    def test_a_rising_trend_scales_the_survey_up(self):
        from connect_labs.labs.indicators.sources import calibrate

        # Not a one-way downward adjustment: Zimbabwe's rate rose.
        _, region = self._country_with_series("ZWE", 2015, 50.0, then=60.0, now=65.0)
        rows = calibrate.load("u5mr", iso_codes=["ZWE"])

        assert rows[0].value > 50.0

    def test_a_recent_survey_is_near_untouched(self):
        from connect_labs.labs.indicators.sources import calibrate

        # A survey one year old against a near-flat trend: the adjustment
        # should be a rounding error, which is why calibration runs uniformly
        # rather than only on old surveys.
        _, region = self._country_with_series("NGA", 2023, 158.0, then=111.0, now=110.0)
        rows = calibrate.load("u5mr", iso_codes=["NGA"])

        assert rows[0].value == pytest.approx(158.0 * 110.0 / 111.0)
        assert abs(rows[0].value - 158.0) < 2.0

    def test_an_implausible_factor_is_refused_not_published(self):
        from connect_labs.labs.indicators.sources import calibrate

        # A tenfold "trend" means the series or the survey year is wrong.
        self._country_with_series("TCD", 2010, 100.0, then=10.0, now=100.0)
        rows = calibrate.load("u5mr", iso_codes=["TCD"])

        assert rows == []

    def test_a_survey_predating_the_series_keeps_its_raw_value(self):
        from connect_labs.labs.indicators.sources import calibrate

        country = make_boundary("TUN", 0, "Tunisia", "TUN-0")
        region = make_boundary("TUN", 1, "Region", "TUN-1", x=2)
        set_value(region, "u5mr", 48.0, year=1988, source=Source.DHS)
        set_value(country, "u5mr", 12.0, year=2024, source=Source.IGME)  # no 1988 value

        assert calibrate.load("u5mr", iso_codes=["TUN"]) == []
        # and the raw survey still resolves, so the region does not vanish
        assert resolve("u5mr", region).value == 48.0

    def test_the_calibrated_value_wins_over_the_raw_survey(self):
        from connect_labs.labs.indicators.sources import base, calibrate

        _, region = self._country_with_series("SWZ", 2006, 106.0, then=100.0, now=45.0)
        base.upsert(calibrate.load("u5mr", iso_codes=["SWZ"]))

        got = resolve("u5mr", region)
        assert got.source == Source.DHS_CALIBRATED
        assert got.value < 106.0
        # The survey year is what a reader needs to judge it, not the year the
        # arithmetic targeted.
        assert got.measured_year == 2006
        assert got.adjusted is True

    def test_the_raw_survey_row_is_kept_for_audit(self):
        from connect_labs.labs.indicators.sources import base, calibrate

        _, region = self._country_with_series("SWZ", 2006, 106.0, then=100.0, now=45.0)
        base.upsert(calibrate.load("u5mr", iso_codes=["SWZ"]))

        raw = IndicatorValue.objects.get(indicator="u5mr", boundary=region, source=Source.DHS)
        assert raw.value == 106.0
