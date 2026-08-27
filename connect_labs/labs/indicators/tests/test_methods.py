"""Methods, resolutions, and knowing when a country cannot answer."""

from __future__ import annotations

import pytest

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


class TestInterventionCosting:
    """The question the whole thing was built for."""

    def test_every_intervention_points_at_a_real_count(self):
        from connect_labs.labs.indicators import interventions, measures

        for i in interventions.all_interventions():
            m = measures.get(i.cases)
            assert not m.is_rate, f"{i.slug} costs a rate"
            measures.get(i.targets)

    def test_every_intervention_states_a_caveat(self):
        from connect_labs.labs.indicators import interventions

        # A costed number with no stated caveat is the one most likely to be
        # quoted back at us.
        for i in interventions.all_interventions():
            assert i.caveat, i.slug

    def test_cost_is_cases_times_unit_cost(self):
        from connect_labs.labs.indicators import interventions

        kmc = interventions.get("kmc")
        assert interventions.cost(kmc, 1_000) == 60_000
        assert interventions.cost(kmc, 1_000, unit_cost=45) == 45_000

    def test_registering_a_rate_as_cases_is_rejected(self):
        from connect_labs.labs.indicators import interventions

        with pytest.raises(ValueError, match="must be a count"):
            interventions.register(
                interventions.Intervention(
                    slug="bogus",
                    label="Bogus",
                    cases="u5mr",
                    unit_cost_usd=1.0,
                    targets="u5mr",
                )
            )

    def test_a_scenario_carries_its_case_measure_even_when_thresholding_elsewhere(self):
        from connect_labs.labs.indicators.resolve import carried_for

        # ITN is selected on malaria prevalence but costed on the ITN gap, so
        # the selection has to carry a count its indicator would not.
        carried = carried_for("malaria_prevalence", ("itn_use_children_gap",))
        assert "itn_use_children_gap" in carried
