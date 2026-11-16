"""A national figure for the survey indicators (ors_coverage, exclusive_breastfeeding, ...).

The regional DHS rows cannot make one: DHS withholds regions with too few cases, so
a country with some regions missing never rolls up to a country row. The survey also
publishes its own country figure (``breakdown=national``); it is stored apart, as
``dhs_national``, so no region ever inherits it by accident.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators import availability, methods, policy
from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.resolve import resolve, select_above
from connect_labs.labs.indicators.sources.dhs import national_records
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

EBF = "CN_BFSS_C_EBF"


def total(code, year, value, label="Total", category="Total", indicator=EBF):
    return {
        "DHS_CountryCode": code,
        "SurveyYear": year,
        "IndicatorId": indicator,
        "CharacteristicLabel": label,
        "CharacteristicCategory": category,
        "Value": value,
    }


class TestNationalRecords:
    def test_the_latest_survey_total_wins(self):
        got = national_records([total("KE", 2022, 59.9), total("KE", 2025, 71.5), total("ET", 2024, 57.3)], EBF)
        assert got["KE"]["Value"] == 71.5
        assert got["ET"]["Value"] == 57.3

    def test_only_the_country_total_counts(self):
        got = national_records([total("KE", 2025, 40.0, label="Urban", category="Residence")], EBF)
        assert got == {}

    def test_another_indicator_is_ignored(self):
        assert national_records([total("KE", 2025, 1.0, indicator="CH_DIAT_C_ORS")], EBF) == {}


class TestRegistration:
    def test_the_method_reads_only_the_national_source(self):
        m = methods.get("national_survey")
        assert m.resolution is methods.Resolution.NATIONAL
        assert m.source_order == (Source.DHS_NATIONAL,)

    @pytest.mark.parametrize(
        "indicator", ["ors_coverage", "exclusive_breastfeeding", "vitamin_a_coverage", "stunting"]
    )
    def test_survey_indicators_admit_the_national_row(self, indicator):
        assert Source.DHS_NATIONAL in policy.sources(indicator)

    def test_mortality_does_not(self):
        # IGME's national series answers mortality; a survey country figure is not a rival to it.
        assert Source.DHS_NATIONAL not in policy.sources("u5mr")

    def test_no_subnational_method_lets_a_region_read_it(self):
        for m in methods.for_resolution(methods.Resolution.SUBNATIONAL):
            assert Source.DHS_NATIONAL not in m.source_order, m.code


@pytest.fixture
def kenya():
    """Two regions, only one of which DHS reported, and the survey's own country figure."""
    country = make_boundary("KEN", 0, "Kenya", "KEN-0", x=0)
    nairobi = make_boundary("KEN", 1, "Nairobi", "KEN-1", x=2)
    wajir = make_boundary("KEN", 1, "Wajir", "KEN-2", x=4)  # DHS withheld it: too few cases
    set_value(country, "exclusive_breastfeeding", 71.5, year=2025, source=Source.DHS_NATIONAL)
    set_value(nairobi, "exclusive_breastfeeding", 62.0, year=2022, source=Source.DHS)
    for b in (nairobi, wajir):
        set_value(b, "births", 100_000, source=Source.DERIVED)
    return {"country": country, "nairobi": nairobi, "wajir": wajir}


@pytest.mark.django_db
class TestNationalAnswer:
    def test_a_split_country_returns_exactly_one_country_row(self, kenya):
        sel = select_above(
            "exclusive_breastfeeding", threshold=100, iso_codes=["KEN"], method="national_survey", rollup=True
        )
        assert [(a.name, a.admin_level) for a in sel.areas] == [("Kenya", 0)]
        assert sel.areas[0].values["exclusive_breastfeeding"].value == 71.5
        assert sel.areas[0].values["exclusive_breastfeeding"].year == 2025
        assert sel.countries_supported == ["Kenya"]

    def test_the_regional_method_still_cannot_roll_it_up(self, kenya):
        sel = select_above(
            "exclusive_breastfeeding", threshold=100, iso_codes=["KEN"], method="subnational_survey", rollup=True
        )
        assert [a.name for a in sel.areas] == ["Nairobi"]
        assert sel.countries_partly_above == ["Kenya"]

    def test_a_region_without_a_survey_value_does_not_inherit_the_country_figure(self, kenya):
        # The reason the national row has its own source: under any regional method Wajir stays empty.
        order = methods.get("subnational_survey").source_order
        assert resolve("exclusive_breastfeeding", kenya["wajir"], source_order=order) is None

    def test_a_country_survey_alone_makes_the_national_method_the_default(self, kenya):
        got = availability.default_method_for("exclusive_breastfeeding", methods.Resolution.NATIONAL)
        assert got.code == "national_survey"
