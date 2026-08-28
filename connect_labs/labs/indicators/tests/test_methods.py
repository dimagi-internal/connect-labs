"""Methods, resolutions, and knowing when a country cannot answer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from connect_labs.labs.indicators import availability, methods
from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.resolve import select_above
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_kinds_of_country():
    """One country with a subnational survey, one with only a national estimate."""
    # Nigeria: national estimate AND regions with survey values.
    ng = make_boundary("NGA", 0, "Nigeria", "NGA-0")
    ng1 = make_boundary("NGA", 1, "Kano", "NGA-1", x=2)
    ng2 = make_boundary("NGA", 1, "Lagos", "NGA-2", x=4)
    set_value(ng, "u5mr", 110, source=Source.IGME)
    set_value(ng1, "u5mr", 158, source=Source.DHS)
    set_value(ng2, "u5mr", 46, source=Source.DHS)
    for b in (ng1, ng2):
        set_value(b, "births", 100_000, source=Source.DERIVED)

    # Chad: national only, with regions that carry no survey.
    td = make_boundary("TCD", 0, "Chad", "TCD-0", x=6)
    td1 = make_boundary("TCD", 1, "Borkou", "TCD-1", x=8)
    set_value(td, "u5mr", 97, source=Source.IGME)
    set_value(td1, "births", 50_000, source=Source.DERIVED)
    return {"ng": ng, "ng1": ng1, "ng2": ng2, "td": td, "td1": td1}


class TestRegistry:
    def test_every_method_names_at_least_one_source(self):
        for m in methods.METHODS.values():
            assert m.source_order

    def test_each_resolution_has_exactly_one_default(self):
        for res in methods.Resolution:
            defaults = [m for m in methods.for_resolution(res) if m.default]
            assert len(defaults) == 1, f"{res.value} has {len(defaults)} defaults"

    def test_resolution_maps_to_admin_levels(self):
        assert methods.Resolution.NATIONAL.admin_levels == (0,)
        # Subnational spans ADM1 and ADM2: IGME models to district level in most
        # countries it covers. Which one a country uses is decided per country.
        assert methods.Resolution.SUBNATIONAL.admin_levels == (1, 2)

    def test_every_method_states_a_caveat(self):
        # A method with no stated caveat invites being read as the plain truth.
        for m in methods.METHODS.values():
            assert m.caveat, f"{m.code} has no caveat"


class TestAvailability:
    def test_national_is_available_wherever_there_is_a_national_estimate(self, two_kinds_of_country):
        rows = availability.for_method(methods.get("national_igme"), "u5mr", iso_codes=["NGA", "TCD"])
        assert {r.iso_code for r in rows if r.available} == {"NGA", "TCD"}

    def test_subnational_is_unavailable_without_a_survey(self, two_kinds_of_country):
        rows = availability.for_method(methods.get("subnational_survey"), "u5mr", iso_codes=["NGA", "TCD"])
        by_iso = {r.iso_code: r for r in rows}

        assert by_iso["NGA"].available is True
        assert by_iso["NGA"].units == 2
        assert by_iso["TCD"].available is False
        assert "no subnational survey" in by_iso["TCD"].reason

    def test_the_matrix_summarises_each_method(self, two_kinds_of_country):
        m = availability.matrix("u5mr", iso_codes=["NGA", "TCD"])
        assert m["methods"]["national_igme"]["countries_available"] == 2
        assert m["methods"]["subnational_survey"]["countries_available"] == 1
        assert [c["iso"] for c in m["methods"]["subnational_survey"]["unavailable"]] == ["TCD"]


class TestSelectionByMethod:
    def test_national_method_returns_one_row_per_country(self, two_kinds_of_country):
        sel = select_above("u5mr", threshold=80, iso_codes=["NGA", "TCD"], method="national_igme")

        assert sel.resolution == "national"
        assert {a.name for a in sel.areas} == {"Nigeria", "Chad"}
        assert all(a.admin_level == 0 for a in sel.areas)

    def test_national_rows_still_report_births_from_their_regions(self, two_kinds_of_country):
        # Counts live on regions; a country row has to reach them or it reports
        # a population of nothing.
        sel = select_above("u5mr", threshold=80, iso_codes=["NGA"], method="national_igme")
        assert sel.areas[0].counts["births"] == 200_000

    def test_subnational_method_excludes_countries_it_cannot_answer_for(self, two_kinds_of_country):
        sel = select_above("u5mr", threshold=80, iso_codes=["NGA", "TCD"], method="subnational_survey")

        # Chad is above the threshold nationally, but this method has no
        # subnational figure for it — so it is reported as unsupported rather
        # than silently answered at country level.
        assert {a.iso_code for a in sel.areas} == {"NGA"}
        assert "Chad" in sel.countries_unsupported

    def test_a_national_answer_is_never_substituted_for_a_missing_subnational_one(self, two_kinds_of_country):
        sel = select_above("u5mr", threshold=80, iso_codes=["TCD"], method="subnational_survey")
        assert sel.areas == []
        assert sel.countries_unsupported == ["Chad"]

    def test_methods_disagree_which_is_the_point(self, two_kinds_of_country):
        national = select_above("u5mr", threshold=80, iso_codes=["NGA", "TCD"], method="national_igme")
        subnational = select_above("u5mr", threshold=80, iso_codes=["NGA", "TCD"], method="subnational_survey")

        # Nationally Nigeria qualifies as a whole; subnationally only Kano does.
        assert national.country_count == 2
        assert subnational.country_count == 1
        assert {a.name for a in subnational.areas} == {"Kano"}

    def test_no_method_keeps_the_previous_behaviour(self, two_kinds_of_country):
        sel = select_above("u5mr", threshold=80, iso_codes=["NGA", "TCD"])
        assert sel.method == ""
        assert sel.resolution == ""


class TestIgmeSubnationalLevelFit:
    """IGME's ADMIN_LEVEL is its own numbering and does not match ours.

    Trusting it scored Madagascar 1 of 22 — its IGME "level 2" is the country's
    22 regions, which geoBoundaries calls ADM1.
    """

    def test_level_is_chosen_by_what_actually_matches(self):
        from connect_labs.labs.indicators.sources.igme_subnational import _best_fit

        # Boundaries exist at ADM1 under these names; ADM2 holds something else.
        make_boundary("MDG", 1, "Analamanga", "MDG-1-1", x=2)
        make_boundary("MDG", 1, "Bongolava", "MDG-1-2", x=4)
        make_boundary("MDG", 2, "Ambalavao", "MDG-2-1", x=6)
        make_boundary("MDG", 2, "Ambanja", "MDG-2-2", x=8)

        areas = [
            {"area_name": "Analamanga", "level": 2, "area_code": "MDG-1"},
            {"area_name": "Bongolava", "level": 2, "area_code": "MDG-2"},
        ]
        chosen, boundary_level, rate = _best_fit("MDG", areas)

        # IGME calls them level 2; they are our ADM1, and the fit finds that.
        assert boundary_level == 1
        assert rate == 1.0
        assert len(chosen) == 2

    def test_a_poor_fit_is_rejected_rather_than_published(self):
        from connect_labs.labs.indicators.sources import igme_subnational

        make_boundary("UGA", 2, "Aringa", "UGA-2-1", x=2)
        make_boundary("UGA", 2, "Aruu", "UGA-2-2", x=4)
        areas = [
            {"area_name": "Wakiso", "level": 2, "area_code": "UGA-1"},
            {"area_name": "Nebbi", "level": 2, "area_code": "UGA-2"},
            {"area_name": "Abim", "level": 2, "area_code": "UGA-3"},
        ]
        _, _, rate = igme_subnational._best_fit("UGA", areas)

        # Uganda's IGME districts have no counterpart in our county-level ADM2,
        # so the fit is poor and the loader leaves the country to the survey path
        # rather than shipping a half-matched map.
        assert rate < igme_subnational.MIN_MATCH_RATE

    def test_refusing_a_country_retracts_what_an_earlier_run_stored(self):
        """Skipping a country stops the write; it does not undo an old one.

        Tightening the match floor left 59 Uganda districts in the database from
        a looser run. Availability is computed from stored values, so Uganda kept
        presenting as covered while 92 of its 151 units were blank — the
        half-matched map the floor exists to prevent, arriving through the back
        door. A refusal that does not retract is not a refusal.
        """
        from connect_labs.labs.indicators.models import IndicatorValue, License, Source
        from connect_labs.labs.indicators.sources import igme_subnational

        boundary = make_boundary("UGA", 2, "Aringa", "UGA-2-1", x=2)
        IndicatorValue.objects.create(
            indicator="u5mr",
            boundary=boundary,
            iso_code="UGA",
            admin_level=2,
            year=2021,
            value=45.8,
            source=Source.IGME_SUBNATIONAL,
            license_code=License.CC_BY_3_IGO,
            retrieved_at=timezone.now(),
        )
        # A country that passed must not be touched by another's refusal.
        kept = make_boundary("AGO", 2, "Cazenga", "AGO-2-1", x=4)
        IndicatorValue.objects.create(
            indicator="u5mr",
            boundary=kept,
            iso_code="AGO",
            admin_level=2,
            year=2021,
            value=61.2,
            source=Source.IGME_SUBNATIONAL,
            license_code=License.CC_BY_3_IGO,
            retrieved_at=timezone.now(),
        )

        deleted = igme_subnational._retract("u5mr", ["UGA"])

        assert deleted == 1
        assert not IndicatorValue.objects.filter(iso_code="UGA", source=Source.IGME_SUBNATIONAL).exists()
        assert IndicatorValue.objects.filter(iso_code="AGO", source=Source.IGME_SUBNATIONAL).exists()

    def test_retraction_is_scoped_to_the_measure_being_loaded(self):
        """A u5mr refusal must not delete the nmr layer, which fits separately."""
        from connect_labs.labs.indicators.models import IndicatorValue, License, Source
        from connect_labs.labs.indicators.sources import igme_subnational

        boundary = make_boundary("UGA", 2, "Aruu", "UGA-2-2", x=6)
        for indicator in ("u5mr", "nmr"):
            IndicatorValue.objects.create(
                indicator=indicator,
                boundary=boundary,
                iso_code="UGA",
                admin_level=2,
                year=2021,
                value=30.0,
                source=Source.IGME_SUBNATIONAL,
                license_code=License.CC_BY_3_IGO,
                retrieved_at=timezone.now(),
            )

        igme_subnational._retract("u5mr", ["UGA"])

        remaining = list(IndicatorValue.objects.filter(iso_code="UGA").values_list("indicator", flat=True))
        assert remaining == ["nmr"]

    def test_a_superseded_level_is_dropped_when_the_fit_moves(self):
        """One level per country. An earlier run's tier must not linger.

        _best_fit settles on a single boundary level, so two levels in the table
        means a previous run chose differently and was never cleared. Six
        countries carried both ADM1 and ADM2 that way.
        """
        from connect_labs.labs.indicators.models import IndicatorValue, License, Source
        from connect_labs.labs.indicators.sources import igme_subnational

        region = make_boundary("AGO", 1, "Luanda", "AGO-1-1", x=2)
        district = make_boundary("AGO", 2, "Cazenga", "AGO-2-1", x=4)
        for boundary, level in ((region, 1), (district, 2)):
            IndicatorValue.objects.create(
                indicator="u5mr",
                boundary=boundary,
                iso_code="AGO",
                admin_level=level,
                year=2021,
                value=61.2,
                source=Source.IGME_SUBNATIONAL,
                license_code=License.CC_BY_3_IGO,
                retrieved_at=timezone.now(),
            )

        igme_subnational._retract_other_levels("u5mr", {"AGO": 2})

        levels = list(IndicatorValue.objects.filter(iso_code="AGO").values_list("admin_level", flat=True))
        assert levels == [2]


class TestInterventionCosting:
    """A cost is a unit price and a unit of measure — both must be fixed."""

    def test_every_intervention_resolves_to_a_real_count(self):
        from connect_labs.labs.indicators import interventions, measures

        for i in interventions.all_interventions():
            m = i.cases_measure()
            assert m is not None, i.slug
            assert not measures.get(m).is_rate, f"{i.slug} costs a rate"
            measures.get(i.targets)

    def test_every_intervention_states_a_caveat(self):
        from connect_labs.labs.indicators import interventions

        # A costed number with no stated caveat is the one most likely to be
        # quoted back at us.
        for i in interventions.all_interventions():
            assert i.caveat, i.slug

    def test_cost_is_units_times_unit_price(self):
        from connect_labs.labs.indicators import interventions

        assert interventions.cost(1_000, 60.0) == 60_000
        assert interventions.cost(1_000, 2.5) == 2_500

    def test_each_basis_maps_to_a_count(self):
        from connect_labs.labs.indicators import interventions, measures

        for basis in interventions.UnitBasis:
            m = interventions.measure_for(basis, "u5mr")
            if m is None:
                continue
            assert not measures.get(m).is_rate, basis.value

    def test_the_fixed_bases_are_what_you_would_expect(self):
        from connect_labs.labs.indicators.interventions import UnitBasis, measure_for

        assert measure_for(UnitBasis.BIRTH, "u5mr") == "births"
        assert measure_for(UnitBasis.UNDER_5, "u5mr") == "pop_u5"
        assert measure_for(UnitBasis.PERSON, "u5mr") == "pop_total"
        assert measure_for(UnitBasis.HOUSEHOLD, "u5mr") == "households"

    def test_a_case_means_something_different_per_indicator(self):
        from connect_labs.labs.indicators.interventions import UnitBasis, measure_for

        # "A case" is untreated diarrhoea here...
        assert measure_for(UnitBasis.DISEASE_CASE, "diarrhoea_prevalence") == "ors_gap_children"
        # ...an unvaccinated child here...
        assert measure_for(UnitBasis.DISEASE_CASE, "measles_vaccination") == "measles_vaccination_gap"
        # ...and an expected death when targeting mortality.
        assert measure_for(UnitBasis.DISEASE_CASE, "u5mr") == "expected_deaths"

    def test_a_case_basis_declines_when_there_is_no_case_count(self):
        from connect_labs.labs.indicators.interventions import UnitBasis, measure_for

        # Stunting is a prevalence with no coverage figure, so nothing says how
        # many cases go untreated. Declining beats inventing.
        assert measure_for(UnitBasis.DISEASE_CASE, "stunting") is None

    def test_registering_an_impossible_basis_is_rejected(self):
        from connect_labs.labs.indicators import interventions

        with pytest.raises(ValueError, match="no count"):
            interventions.register(
                interventions.Intervention(
                    slug="bogus",
                    label="Bogus",
                    basis=interventions.UnitBasis.DISEASE_CASE,
                    unit_cost_usd=1.0,
                    targets="stunting",
                )
            )

    def test_a_scenario_carries_its_unit_measure_even_when_thresholding_elsewhere(self):
        from connect_labs.labs.indicators.resolve import carried_for

        # A household-priced water programme selected on water coverage still
        # needs the household count, which that indicator would not carry.
        carried = carried_for("improved_water", ("households",))
        assert "households" in carried


class TestOffMethodUnits:
    """How much of a selection the chosen method did not actually answer."""

    def test_units_inherited_from_outside_the_method_are_counted(self):
        """source_order ranks sources; it does not restrict them.

        Most of DR Congo's provinces under "Survey as measured" carry IGME's
        national figure, because the survey never reached them. Each row says so,
        but only a count tells a reader how much of the total is really survey
        data.
        """
        from connect_labs.labs.indicators.resolve import Area, Selection

        def area(source, units):
            boundary = make_boundary("COD", 1, f"P{source}{units}", f"COD-1-{source}{units}", x=units * 2)
            resolved = SimpleNamespace(source=source)
            return Area(
                boundary=boundary,
                iso_code="COD",
                country_name="Democratic Republic of the Congo",
                name=boundary.name,
                admin_level=1,
                units_covered=units,
                values={"u5mr": resolved},
            )

        selection = Selection(
            indicator="u5mr",
            threshold=80.0,
            year=None,
            areas=[area("dhs", 2), area("igme", 5)],
            totals={},
            coverage={},
            countries_fully_above=[],
            countries_partly_above=[],
            skipped_no_data=[],
            method="subnational_survey",
        )

        assert selection.off_method_units == 5

    def test_a_rolled_up_row_counts_as_off_method_unless_all_its_sources_qualify(self):
        from connect_labs.labs.indicators.resolve import Area, Selection

        boundary = make_boundary("CAF", 1, "Ouham", "CAF-1-1", x=2)
        selection = Selection(
            indicator="u5mr",
            threshold=80.0,
            year=None,
            areas=[
                Area(
                    boundary=boundary,
                    iso_code="CAF",
                    country_name="Central African Republic",
                    name="Central African Republic",
                    admin_level=0,
                    units_covered=17,
                    values={"u5mr": SimpleNamespace(source="dhs+igme")},
                )
            ],
            totals={},
            coverage={},
            countries_fully_above=[],
            countries_partly_above=[],
            skipped_no_data=[],
            method="subnational_survey",
        )

        assert selection.off_method_units == 17

    def test_no_method_means_nothing_to_be_off(self):
        from connect_labs.labs.indicators.resolve import Selection

        selection = Selection(
            indicator="u5mr",
            threshold=80.0,
            year=None,
            areas=[],
            totals={},
            coverage={},
            countries_fully_above=[],
            countries_partly_above=[],
            skipped_no_data=[],
        )

        assert selection.off_method_units == 0
