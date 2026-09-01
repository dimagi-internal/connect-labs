"""Tests for the rule that decides what a number is allowed to be made of.

The failure this guards against does not raise and does not look wrong. Under
the old rule an ineligible source was ranked last rather than excluded, so
asking for "Survey as measured" returned a large, confident answer that was
mostly a national model repeated across regions. Every test here is about
something being *absent* that used to be quietly present.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators import measures, methods, policy
from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.resolve import resolve, select_above
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

pytestmark = pytest.mark.django_db


class TestTheRegistry:
    def test_every_targetable_indicator_states_an_opinion(self):
        missing = [c for c in measures.TARGETABLE if c not in policy.POLICY]
        assert not missing, f"no source policy for {missing}"

    def test_every_entry_carries_a_reason(self):
        """The reason is the artifact. A list of sources without one is a guess."""
        for indicator, entries in policy.POLICY.items():
            for e in entries:
                assert e.why.strip(), f"{indicator}/{e.source} has no reason"

    def test_an_unknown_indicator_raises_rather_than_accepting_anything(self):
        with pytest.raises(KeyError, match="no source policy"):
            policy.for_indicator("not_a_measure")

    def test_a_gap_measure_falls_back_to_derived_without_an_entry_each(self):
        assert policy.sources("anc4_gap") == (Source.DERIVED,)

    def test_a_method_narrows_but_never_reorders(self):
        """The indicator knows which of its sources is better; a method does not."""
        full = policy.sources("u5mr")
        assert full.index(Source.IGME_SUBNATIONAL) < full.index(Source.DHS)
        # A lens listing them the other way round must not flip the preference.
        assert policy.order_for("u5mr", (Source.DHS, Source.IGME_SUBNATIONAL)) == (
            Source.IGME_SUBNATIONAL,
            Source.DHS,
        )

    def test_a_lens_sharing_nothing_with_the_policy_leaves_nothing(self):
        assert policy.order_for("share_rural", (Source.DHS,)) == ()

    def test_every_method_can_answer_something(self):
        """A method whose lens excludes every policy is dead weight in the picker."""
        for code, method in methods.METHODS.items():
            answerable = [c for c in measures.TARGETABLE if policy.order_for(c, method.source_order)]
            assert answerable, f"method {code} can answer no targetable indicator"


class TestEligibilityAtResolution:
    def test_an_ineligible_source_is_not_used_even_when_it_is_the_only_row(self):
        """The whole point. A worse answer and no answer are different things."""
        region = make_boundary("KEN", 1, "Nairobi", "KEN-1-1", x=2)
        set_value(region, "share_rural", 60, source=Source.DHS)  # policy says GHSL only

        assert resolve("share_rural", region) is None

    def test_an_eligible_source_still_resolves(self):
        region = make_boundary("KEN", 1, "Nairobi", "KEN-1-1", x=2)
        set_value(region, "share_rural", 60, source=Source.GHSL)

        got = resolve("share_rural", region)
        assert got is not None and got.value == 60

    def test_preference_order_comes_from_the_policy(self):
        region = make_boundary("KEN", 1, "Nairobi", "KEN-1-1", x=2)
        set_value(region, "malaria_prevalence", 10, source=Source.MAP)
        set_value(region, "malaria_prevalence", 40, source=Source.DHS)

        # DHS first for prevalence: it is measured, MAP is modelled.
        assert resolve("malaria_prevalence", region).source == Source.DHS

    def test_a_method_lens_narrows_within_the_policy(self):
        region = make_boundary("KEN", 1, "Nairobi", "KEN-1-1", x=2)
        set_value(region, "malaria_prevalence", 10, source=Source.MAP)
        set_value(region, "malaria_prevalence", 40, source=Source.DHS)

        surface = resolve("malaria_prevalence", region, source_order=(Source.MAP,))
        assert surface.source == Source.MAP

    def test_inheritance_may_only_take_an_eligible_ancestor(self):
        """A district borrows its country's survey, never a source ruled out."""
        country = make_boundary("COD", 0, "DR Congo", "COD-0", x=0)
        region = make_boundary("COD", 1, "Sankuru", "COD-1-1", x=2)
        set_value(country, "u5mr", 150, source=Source.IGME)

        assert resolve("u5mr", region, source_order=(Source.DHS,)) is None
        # ...and with an eligible ancestor it does inherit, and says so.
        set_value(country, "u5mr", 140, source=Source.DHS)
        got = resolve("u5mr", region, source_order=(Source.DHS,))
        assert got.value == 140
        assert got.inherited


class TestWhatASelectionReports:
    def test_a_selection_built_on_an_ineligible_source_is_empty_not_wrong(self):
        country = make_boundary("COD", 0, "DR Congo", "COD-0", x=0)
        make_boundary("COD", 1, "Sankuru", "COD-1-1", x=2)
        set_value(country, "u5mr", 150, source=Source.IGME)

        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        assert selection.areas == []
        assert "Democratic Republic of the Congo" in selection.countries_unsupported

    def test_carried_counts_are_not_narrowed_by_the_rates_method(self):
        """A mortality method has no opinion about where a population comes from.

        Left unbounded this erased every birth count on the map, because
        'derived' is not in IGME's source list.
        """
        make_boundary("COD", 0, "DR Congo", "COD-0", x=0)
        region = make_boundary("COD", 1, "Sankuru", "COD-1-1", x=2)
        set_value(region, "u5mr", 150, source=Source.IGME_SUBNATIONAL)
        set_value(region, "births", 5000, source=Source.DERIVED)

        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_igme")

        assert selection.totals["births"] == 5000


class TestWholeCountryRollup:
    """ "Every region cleared it" must mean every region.

    A country row is the strongest claim this tool makes and the one most
    likely to be quoted alone, so it has to be earned. Sudan has nineteen
    regions; three carried a survey; all three were above the threshold — and
    the country was emitted as one whole-country row carrying all nineteen
    regions' population.

    The bug was always there and always latent: while sources were substituted
    for one another nearly every region resolved something, so "regions we
    evaluated" and "regions there are" agreed by accident.
    """

    def _country_of(self, iso, name, regions, method="subnational_survey"):
        make_boundary(iso, 0, name, f"{iso}-0", x=0)
        made = []
        for i in range(regions):
            made.append(make_boundary(iso, 1, f"{name}{i}", f"{iso}-1-{i}", x=2 + i * 2))
        return made

    def test_a_country_evaluated_whole_still_rolls_up(self):
        regions = self._country_of("SLE", "Sierra Leone", 3)
        for r in regions:
            set_value(r, "u5mr", 150, source=Source.DHS)

        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        assert [a.is_whole_country for a in selection.areas] == [True]
        assert selection.areas[0].units_covered == 3

    def test_a_country_evaluated_in_part_is_emitted_as_its_regions(self):
        regions = self._country_of("SDN", "Sudan", 5)
        # Only two of five carry a survey; both are above.
        for r in regions[:2]:
            set_value(r, "u5mr", 150, source=Source.DHS)

        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        assert len(selection.areas) == 2
        assert not any(a.is_whole_country for a in selection.areas)
        assert "Sudan" not in selection.countries_fully_above

    def test_the_partial_country_does_not_carry_its_unevaluated_regions_population(self):
        """The row's counts must describe the regions it actually stands for."""
        regions = self._country_of("SDN", "Sudan", 4)
        for r in regions[:2]:
            set_value(r, "u5mr", 150, source=Source.DHS)
        for r in regions:
            set_value(r, "births", 1000, source=Source.DERIVED)

        selection = select_above(indicator="u5mr", threshold=80.0, method="subnational_survey")

        assert selection.totals["births"] == 2000  # not 4000


class TestSmallSampleIsCarried:
    """How much a survey figure is worth, not just whether it exists.

    Found by comparing a generated county table against one a person built by
    hand. Theirs carried DHS's small-sample flag in its own column; six of
    Liberia's fifteen counties wore it, and Bomi's 75.5% ORS coverage rests on
    35 unweighted cases — 21 once weighted. Ours presented all fifteen as
    equals. The denominators come back from the same API call for free; we
    simply were not asking for them.
    """

    def _with_sample(self, boundary, n):
        v = set_value(boundary, "ors_coverage", 75.5, source=Source.DHS)
        v.extra = {"sample_unweighted": n}
        v.save(update_fields=["extra"])
        return v

    def test_a_thin_estimate_is_flagged(self):
        b = make_boundary("LBR", 1, "Bomi", "LBR-1-1", x=2)
        self._with_sample(b, 35)
        assert resolve("ors_coverage", b).small_sample

    def test_a_solid_estimate_is_not(self):
        b = make_boundary("LBR", 1, "Montserrado", "LBR-1-2", x=4)
        self._with_sample(b, 300)
        assert not resolve("ors_coverage", b).small_sample

    def test_the_boundary_is_dhs_own_convention(self):
        """49 unweighted is bracketed by DHS; 50 is not. We hold the same line."""
        from connect_labs.labs.indicators.models import SMALL_SAMPLE_UNWEIGHTED

        assert SMALL_SAMPLE_UNWEIGHTED == 50
        b1 = make_boundary("LBR", 1, "Edge", "LBR-1-3", x=6)
        b2 = make_boundary("LBR", 1, "Over", "LBR-1-4", x=8)
        self._with_sample(b1, 49)
        self._with_sample(b2, 50)
        assert resolve("ors_coverage", b1).small_sample
        assert not resolve("ors_coverage", b2).small_sample

    def test_a_source_that_reports_no_sample_size_is_not_flagged(self):
        """Absence of a denominator is not evidence of a thin one.

        Only DHS publishes these. A modelled surface has no sample at all, and
        flagging every MAP value as unreliable would make the field useless.
        """
        b = make_boundary("LBR", 1, "Modelled", "LBR-1-5", x=10)
        set_value(b, "malaria_prevalence", 24.0, source=Source.MAP)
        assert not resolve("malaria_prevalence", b).small_sample

    def test_a_selection_counts_the_units_resting_on_thin_estimates(self):
        make_boundary("LBR", 0, "Liberia", "LBR-0", x=0)
        thin = make_boundary("LBR", 1, "Bomi", "LBR-1-1", x=2)
        solid = make_boundary("LBR", 1, "Montserrado", "LBR-1-2", x=4)
        self._with_sample(thin, 35)
        self._with_sample(solid, 300)

        selection = select_above(indicator="ors_coverage", threshold=80.0, method="subnational_survey")

        assert selection.small_sample_units == 1
        assert sum(a.units_covered for a in selection.areas) == 2
