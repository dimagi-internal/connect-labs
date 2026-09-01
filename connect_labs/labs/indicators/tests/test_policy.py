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
from connect_labs.labs.indicators.resolve import BulkResolver, resolve, select_above
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


class TestInheritanceWalksTheChain:
    """A district must take its province's figure, not its country's.

    ``BulkResolver`` jumped straight to ADM0. That was correct while the system
    held only ADM0 and ADM1 — its docstring said so, and said it would need to
    grow when deeper levels arrived. ADM2 arrived and it did not.

    The result was a coarser answer presented as the same thing: 784 districts
    were reading a national under-5 mortality rate while their own province had
    one. Where the country had nothing eligible they got nothing at all, which
    is how it surfaced — Liberia measures ORS coverage for all fifteen counties
    and no district, so all 136 districts came back empty.
    """

    def _liberia(self):
        country = make_boundary("LBR", 0, "Liberia", "LBR-0", x=0)
        county = make_boundary("LBR", 1, "Grand Kru", "LBR-1-1", x=2)
        district = make_boundary("LBR", 2, "Barclayville", "LBR-2-1", x=4)
        district.parent_boundary_id = county.boundary_id
        district.save(update_fields=["parent_boundary_id"])
        return country, county, district

    def test_a_district_takes_its_province_over_its_country(self):
        country, county, district = self._liberia()
        set_value(country, "ors_coverage", 20, source=Source.DHS)
        set_value(county, "ors_coverage", 45, source=Source.DHS)

        got = resolve("ors_coverage", district)

        assert got.value == 45
        assert got.inherited
        assert got.measured_at.pk == county.pk

    def test_the_bulk_resolver_agrees_with_the_single_one(self):
        """These are two implementations of one rule, and they had diverged."""
        country, county, district = self._liberia()
        set_value(country, "ors_coverage", 20, source=Source.DHS)
        set_value(county, "ors_coverage", 45, source=Source.DHS)

        bulk = BulkResolver([district])
        one = resolve("ors_coverage", district)
        many = bulk.get("ors_coverage", district)

        assert (many.value, many.measured_at.pk) == (one.value, one.measured_at.pk)

    def test_a_district_still_reaches_its_country_when_the_province_has_nothing(self):
        country, county, district = self._liberia()
        set_value(country, "ors_coverage", 20, source=Source.DHS)

        got = BulkResolver([district]).get("ors_coverage", district)

        assert got.value == 20
        assert got.measured_at.pk == country.pk

    def test_a_count_still_refuses_to_inherit_at_any_depth(self):
        """Walking the chain must not quietly make counts inheritable."""
        country, county, district = self._liberia()
        set_value(county, "pop_total", 500_000, source=Source.WORLDPOP)

        assert BulkResolver([district]).get("pop_total", district) is None

    def test_a_missing_parent_link_does_not_break_the_walk(self):
        """geoBoundaries ships no parent for ADM1, so this is the common case."""
        country, county, _ = self._liberia()
        set_value(country, "ors_coverage", 20, source=Source.DHS)

        assert not county.parent_boundary_id
        got = BulkResolver([county]).get("ors_coverage", county)
        assert got.value == 20 and got.measured_at.pk == country.pk
