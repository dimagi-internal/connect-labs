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

    def test_resolution_maps_to_an_admin_level(self):
        assert methods.Resolution.NATIONAL.admin_levels == (0,)
        assert methods.Resolution.SUBNATIONAL.admin_levels == (1,)

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
