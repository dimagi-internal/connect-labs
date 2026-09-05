"""Diarrhoea, ORS and breastfeeding — and targeting on them.

These bring a second failure mode the mortality path never had: a *coverage*
indicator, where a low value is the problem. Thresholding above one would select
the places already doing well.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.models import IndicatorValue, Source
from connect_labs.labs.indicators.resolve import select_above
from connect_labs.labs.indicators.sources import derive
from connect_labs.labs.indicators.sources.base import Row
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


class TestUncertainty:
    """Published intervals were being stored and then discarded."""

    def test_a_row_whose_interval_spans_the_threshold_is_flagged(self, client=None):
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse

        make_boundary("NGA", 0, "Nigeria", "NGA-0")
        r = make_boundary("NGA", 1, "Borderline", "NGA-1", x=2)
        v = set_value(r, "malaria_prevalence", 22.0, source=Source.DHS)
        v.ci_low, v.ci_high = 12.0, 32.0
        v.save()

        u = get_user_model().objects.create_user(username="ci", password="pw")  # noqa: S106
        c = Client()
        c.force_login(u)
        row = c.get(
            reverse("targeting:selection"),
            {"indicator": "malaria_prevalence", "threshold": 20, "method": "subnational_survey"},
        ).json()["rows"][0]

        # 20 sits inside 12-32, so being above the line is not distinguishable
        # from chance here.
        assert row["ci_low"] == 12.0
        assert row["straddles_threshold"] is True

    def test_a_confident_row_is_not_flagged(self):
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse

        make_boundary("NGA", 0, "Nigeria", "NGA-0")
        r = make_boundary("NGA", 1, "Clear", "NGA-1", x=2)
        v = set_value(r, "malaria_prevalence", 54.0, source=Source.DHS)
        v.ci_low, v.ci_high = 44.0, 64.0
        v.save()

        u = get_user_model().objects.create_user(username="ci2", password="pw")  # noqa: S106
        c = Client()
        c.force_login(u)
        row = c.get(
            reverse("targeting:selection"),
            {"indicator": "malaria_prevalence", "threshold": 20, "method": "subnational_survey"},
        ).json()["rows"][0]

        assert row["straddles_threshold"] is False


class TestConditionalCoverageGaps:
    """A rate measured among those who had an episode applies only to them.

    ORS coverage is measured among children who *had diarrhoea*. The generic
    unreached count multiplied the whole under-five population by the untreated
    share, which reads as "children not covered by ORS" and is not a quantity
    that exists. For Liberia it said 295,899 where the truth is 51,429 — a
    factor of six, and the number a proposal would have quoted.

    Found by re-running the targeting skill against a hand-built proposal whose
    own metric was "children with diarrhoea not receiving ORS".
    """

    def test_a_conditional_gap_is_multiplied_by_prevalence(self, db):
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_u5", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "ors_coverage", 40.0, source=Source.DHS)
        set_value(b, "diarrhoea_prevalence", 20.0, source=Source.DHS)

        rows = {r.indicator: r.value for r in derive.load_coverage_gaps(iso_codes=["LBR"])}

        # 100,000 x 20% had diarrhoea, of whom 60% got no ORS.
        assert rows["ors_coverage_gap"] == pytest.approx(12_000)

    def test_an_unconditional_gap_is_unchanged(self, db):
        """Sanitation is not conditional on anything; its gap is the whole population."""
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_total", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "improved_sanitation", 40.0, source=Source.DHS)

        rows = {r.indicator: r.value for r in derive.load_coverage_gaps(iso_codes=["LBR"])}

        assert rows["improved_sanitation_gap"] == pytest.approx(60_000)

    def test_no_prevalence_means_no_gap_rather_than_a_wrong_one(self, db):
        """The number would overstate by the inverse of a prevalence we cannot see."""
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_u5", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "ors_coverage", 40.0, source=Source.DHS)
        # No diarrhoea_prevalence for this boundary.

        rows = {r.indicator for r in derive.load_coverage_gaps(iso_codes=["LBR"])}

        assert "ors_coverage_gap" not in rows

    def test_the_generic_gap_now_agrees_with_the_hand_written_one(self, db):
        """ors_gap_children was hand-written because the generic one was wrong.

        They are the same quantity and now compute the same number, which is a
        free cross-check on the derivation rather than a duplication.
        """
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_u5", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "ors_coverage", 40.0, source=Source.DHS)
        set_value(b, "diarrhoea_prevalence", 20.0, source=Source.DHS)

        generic = {r.indicator: r.value for r in derive.load_coverage_gaps(iso_codes=["LBR"])}
        hand = {r.indicator: r.value for r in derive.load_ors_gap(iso_codes=["LBR"])}

        assert generic["ors_coverage_gap"] == pytest.approx(hand["ors_gap_children"])

    def test_every_conditional_measure_names_a_registered_prevalence(self):
        """Enforced at import, pinned here so the reason survives."""
        for m in measures.MEASURES.values():
            if m.conditional_on:
                episode = measures.get(m.conditional_on)
                assert episode.is_rate, f"{m.code} conditions on a count"


class TestAnnualEpisodes:
    """A fortnight's worth of illness is not a year's.

    DHS asks whether a child had diarrhoea in the last two weeks. Every count
    derived from that answer is a fortnight's worth of cases, and it reads —
    to anyone who does not check — like a year's. It was read that way: a
    document written from this system called Liberia's 60,671 "episodes a year"
    when the annual figure is 1.2 million.
    """

    def test_the_factor_comes_from_the_window_and_the_episode(self):
        # 365 days over a 14-day recall window plus a 4.3-day episode.
        assert measures.annualisation_factor("diarrhoea_prevalence") == pytest.approx(365 / 18.3)

    def test_a_measure_with_no_declared_window_gets_no_factor(self):
        """Better no conversion than a guessed one."""
        assert measures.annualisation_factor("stunting") is None
        assert measures.annualisation_factor("not_a_measure") is None

    def test_the_conversion_reproduces_liberias_own_incidence(self):
        """The check that the arithmetic is the right arithmetic.

        Liberia's measured 15.7% two-week prevalence implies 3.13 episodes per
        child-year, between the sub-Saharan average of 3.3 and the global
        low-and-middle-income figure of 2.7. A conversion that landed outside
        that range would be wrong whatever its algebra looked like.
        """
        implied = 0.157 * measures.annualisation_factor("diarrhoea_prevalence")
        assert 2.7 <= implied <= 3.3

    def test_a_conditional_gap_gets_an_annual_sibling(self, db):
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_u5", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "ors_coverage", 40.0, source=Source.DHS)
        set_value(b, "diarrhoea_prevalence", 20.0, source=Source.DHS)

        rows = {r.indicator: r.value for r in derive.load_coverage_gaps(iso_codes=["LBR"])}

        assert rows["ors_coverage_gap"] == pytest.approx(12_000)
        assert rows["ors_coverage_gap_annual"] == pytest.approx(12_000 * 365 / 18.3)

    def test_an_unconditional_gap_gets_no_annual_sibling(self, db):
        """Sanitation is a state, not an episode; there is nothing to annualise."""
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_total", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "improved_sanitation", 40.0, source=Source.DHS)

        rows = {r.indicator for r in derive.load_coverage_gaps(iso_codes=["LBR"])}

        assert "improved_sanitation_gap" in rows
        assert "improved_sanitation_gap_annual" not in rows

    def test_the_annual_row_says_how_it_was_carried(self, db):
        """A reader must be able to see the conversion, not just its result."""
        b = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        set_value(b, "pop_u5", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(b, "ors_coverage", 40.0, source=Source.DHS)
        set_value(b, "diarrhoea_prevalence", 20.0, source=Source.DHS)

        annual = next(
            r for r in derive.load_coverage_gaps(iso_codes=["LBR"]) if r.indicator == "ors_coverage_gap_annual"
        )

        assert "Carried to a year" in annual.method
        assert "14-day recall window" in annual.method


class TestDerivedRowsAreSwept:
    """A derived row is nothing but a function of other rows.

    ``upsert`` refreshes what a loader emits and leaves the rest alone, which
    is right for a source and wrong for a derivation: when the arithmetic
    changes, the rows the new version cannot produce are not stale data, they
    are the OLD ARITHMETIC, and they look identical to fresh ones.

    This is not hypothetical. ``malaria_treatment_gap`` kept 1,700 rows of the
    pre-``conditional_on`` shape -- the whole under-five population times the
    untreated share, the exact error that had just been fixed for ORS -- because
    its ``fever_prevalence`` had never been loaded, so the fixed derivation
    correctly produced nothing and the wrong rows simply stayed.
    """

    def test_a_row_the_derivation_no_longer_produces_is_removed(self):
        b = make_boundary("LBR", 1, "Bong", "LBR-1")
        stale = IndicatorValue.objects.create(
            indicator="ors_coverage_gap",
            boundary=b,
            iso_code="LBR",
            admin_level=1,
            year=2019,
            source=Source.DERIVED,
            value=329_230.0,
            retrieved_at=timezone.now(),
        )

        removed = derive.sweep_derived([], derive.gap_indicators(), iso_codes=["LBR"])

        assert removed == 1
        assert not IndicatorValue.objects.filter(pk=stale.pk).exists()

    def test_a_row_the_derivation_still_produces_survives(self):
        b = make_boundary("LBR", 1, "Bong", "LBR-1")
        kept = IndicatorValue.objects.create(
            indicator="ors_coverage_gap",
            boundary=b,
            iso_code="LBR",
            admin_level=1,
            year=2019,
            source=Source.DERIVED,
            value=51_429.0,
            retrieved_at=timezone.now(),
        )
        produced = [
            Row(
                indicator="ors_coverage_gap",
                boundary=b,
                year=2019,
                value=51_429.0,
                source=Source.DERIVED,
            )
        ]

        assert derive.sweep_derived(produced, derive.gap_indicators(), iso_codes=["LBR"]) == 0
        assert IndicatorValue.objects.filter(pk=kept.pk).exists()

    def test_it_never_touches_a_measured_source(self):
        """Only derived rows. A survey not returning a region this year does
        not make last year's survey void."""
        b = make_boundary("LBR", 1, "Bong", "LBR-1")
        survey = IndicatorValue.objects.create(
            indicator="ors_coverage_gap",
            boundary=b,
            iso_code="LBR",
            admin_level=1,
            year=2019,
            source=Source.DHS,
            value=1.0,
            retrieved_at=timezone.now(),
        )

        derive.sweep_derived([], derive.gap_indicators(), iso_codes=["LBR"])

        assert IndicatorValue.objects.filter(pk=survey.pk).exists()

    def test_it_stays_inside_the_scope_it_was_given(self):
        """Deriving one country must not delete another's rows."""
        lbr = make_boundary("LBR", 1, "Bong", "LBR-1")
        nga = make_boundary("NGA", 1, "Kano", "NGA-1", x=6)
        for b, iso in ((lbr, "LBR"), (nga, "NGA")):
            IndicatorValue.objects.create(
                indicator="ors_coverage_gap",
                boundary=b,
                iso_code=iso,
                admin_level=1,
                year=2019,
                source=Source.DERIVED,
                value=1.0,
                retrieved_at=timezone.now(),
            )

        assert derive.sweep_derived([], derive.gap_indicators(), iso_codes=["LBR"]) == 1
        assert IndicatorValue.objects.filter(iso_code="NGA", indicator="ors_coverage_gap").exists()

    def test_the_annual_siblings_are_in_scope(self):
        """They are derived by the same pass, so they are swept by it."""
        assert "ors_coverage_gap_annual" in derive.gap_indicators()
        assert "ors_coverage_gap" in derive.gap_indicators()
