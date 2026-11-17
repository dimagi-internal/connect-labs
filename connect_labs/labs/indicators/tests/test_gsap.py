"""Poverty by region from the World Bank's GSAP: reading the sheet, joining regions to boundaries by name."""

import io

import openpyxl
import pytest

from connect_labs.effectiveness.tests.helpers import boundary
from connect_labs.labs.indicators import measures, policy
from connect_labs.labs.indicators.models import IndicatorValue, Source
from connect_labs.labs.indicators.sources import base, gsap
from connect_labs.labs.indicators.sources.base import BoundaryMatcher

HEAD = [
    "code",
    "baseyear",
    "lineupyear",
    "survname",
    "welfaretype",
    "sample",
    "poor300_ln",
    "poor420_ln",
    "poor830_ln",
]


def workbook(rows, sheet="Data 2024", extra_sheets=("Data 2023", "Dictionary")):
    wb = openpyxl.Workbook()
    wb.active.title = extra_sheets[0] if extra_sheets else sheet
    for name in list(extra_sheets[1:]) + [sheet]:
        wb.create_sheet(name)
    ws = wb[sheet]
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


NGA = [
    ("NGA", 2022, 2024, "LSS", "CONS", "Kano", 0.536, 0.782, 0.974),
    ("NGA", 2022, 2024, "LSS", "CONS", "Lagos", 0.079, 0.242, 0.782),
    ("NGA", 2022, 2024, "LSS", "CONS", "Nassarawa", 0.3, 0.5, 0.9),
    ("NGA", 2022, 2024, "LSS", "CONS", "Nowhere", 0.1, 0.2, 0.3),
]


class TestReadingTheSheet:
    def test_the_latest_line_up_sheet_is_the_one_read(self):
        assert gsap.latest_sheet(["Dictionary", "Data 2021", "Data 2024", "Data 2010"]) == "Data 2024"
        with pytest.raises(ValueError):
            gsap.latest_sheet(["Dictionary"])

    def test_rates_are_percent_and_the_survey_and_line_up_years_are_kept(self):
        got = gsap.parse(workbook(NGA))
        kano = next(r for r in got if r.name == "Kano")
        assert kano.rates == {"poverty_rate_300": pytest.approx(53.6), "poverty_rate_420": pytest.approx(78.2)}
        assert (kano.baseyear, kano.lineupyear, kano.survey) == (2022, 2024, "LSS")

    def test_only_the_asked_countries_are_read(self):
        rows = NGA + [("KEN", 2022, 2024, "KCHS", "CONS", "Nairobi", 0.06, 0.18, 0.69)]
        assert {r.iso for r in gsap.parse(workbook(rows), ["ken"])} == {"KEN"}

    def test_a_region_with_no_rate_is_skipped_not_read_as_zero(self):
        rows = [("NGA", 2022, 2024, "LSS", "CONS", "Blank", None, None, None)]
        assert gsap.parse(workbook(rows)) == []


@pytest.mark.django_db
class TestJoiningToBoundaries:
    @pytest.fixture
    def nigeria(self):
        ng = boundary(0, "Nigeria", "NGA", 0, 0, size=4)
        states = {
            n: boundary(1, n, f"NGA-{n}", i, 0)
            for i, n in enumerate(["Kano", "Lagos", "Nasarawa", "Zamfara"])  # GSAP has no Zamfara
        }
        return {"ng": ng, **states}

    def test_regions_are_joined_by_name_through_the_alias_table_and_the_rest_are_reported(self, nigeria):
        rows, misses = gsap.rows_for_country(gsap.parse(workbook(NGA)), BoundaryMatcher("NGA", 1), nigeria["ng"])
        joined = {r.boundary.name for r in rows}
        assert joined == {"Kano", "Lagos", "Nasarawa"}  # Nassarawa -> Nasarawa
        assert misses == ["Nowhere"]
        assert "Zamfara" not in joined  # a boundary GSAP has no region for is left blank, not filled

    def test_a_row_says_surveyed_when_and_lined_up_to_when(self, nigeria):
        rows, _ = gsap.rows_for_country(gsap.parse(workbook(NGA)), BoundaryMatcher("NGA", 1), nigeria["ng"])
        kano = next(r for r in rows if r.boundary.name == "Kano" and r.indicator == "poverty_rate_300")
        assert kano.year == 2024 and kano.source == Source.GSAP and kano.license_code == "cc-by-4.0"
        assert kano.extra["baseyear"] == 2022 and "lined up to 2024" in kano.source_ref
        assert "modelled figure" in kano.method

    def test_a_country_reported_as_one_region_is_written_on_the_country(self, nigeria):
        one = gsap.parse(workbook([("NGA", 2022, 2024, "LSS", "CONS", "Nigeria", 0.4, 0.7, 0.9)]))
        rows, misses = gsap.rows_for_country(one, BoundaryMatcher("NGA", 1), nigeria["ng"])
        assert {r.boundary.admin_level for r in rows} == {0} and misses == []

    def test_two_regions_matching_one_boundary_write_it_once_and_say_so(self, nigeria):
        twice = NGA[:1] + [("NGA", 2022, 2024, "LSS", "CONS", "Kano State", 0.1, 0.2, 0.3)]
        rows, misses = gsap.rows_for_country(gsap.parse(workbook(twice)), BoundaryMatcher("NGA", 1), nigeria["ng"])
        assert len([r for r in rows if r.indicator == "poverty_rate_300"]) == 1
        assert misses and "same boundary as Kano" in misses[0]

    def test_load_writes_through_upsert_and_is_repeatable(self, nigeria):
        payload = workbook(NGA)
        for _ in range(2):
            rows, misses = gsap.load(["NGA"], payload)
            base.upsert(rows)
        assert IndicatorValue.objects.filter(source=Source.GSAP).count() == 6  # 3 regions x 2 lines, not 12
        assert misses == {"NGA": ["Nowhere"]}

    def test_drc_provinces_in_either_language_meet(self):
        cd = boundary(0, "Democratic Republic of the Congo", "COD", 0, 0, size=2, iso="COD")
        boundary(1, "South Kivu", "COD-1", 0, 0, iso="COD")
        rows, misses = gsap.rows_for_country(
            gsap.parse(
                workbook(
                    [
                        ("COD", 2020, 2024, "EGI", "CONS", "Sud-Kivu", 0.85, 0.93, 0.98),
                        ("COD", 2020, 2024, "EGI", "CONS", "Tshopo", 0.9, 0.98, 1.0),
                    ]
                )
            ),
            BoundaryMatcher("COD", 1),
            cd,
        )
        assert {r.boundary.name for r in rows} == {"South Kivu"} and misses == ["Tshopo"]


def test_the_measures_are_registered_percent_rates_the_policy_and_methods_know():
    for code in ("poverty_rate_300", "poverty_rate_420"):
        m = measures.get(code)
        assert m.unit == "% of people" and m.weight_by == "pop_total" and m.downscale
        assert [e.source for e in policy.POLICY[code]] == [Source.GSAP]
        assert measures.group_of(code) == "Poverty"
