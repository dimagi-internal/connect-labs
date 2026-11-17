"""The UN's births kept to audit ours: reading the file, and comparing a country's total with it."""

import gzip

import pytest

from connect_labs.effectiveness.tests.helpers import boundary, value
from connect_labs.labs.indicators import measures, policy
from connect_labs.labs.indicators.models import IndicatorValue, Source
from connect_labs.labs.indicators.sources import base, derive, wpp

HEADER = "﻿SortOrder,ISO3_code,Location,Variant,Time,Births"


def gz(rows):
    return gzip.compress("\n".join([HEADER, *rows]).encode("utf-8"))


def row(iso, year, births_thousands):
    return f"1,{iso},Place,Medium,{year},{births_thousands}"


class TestReadingTheFile:
    def test_births_are_in_births_not_thousands_and_only_the_asked_years_and_countries_are_kept(self):
        got = wpp.parse(
            gz([row("NGA", 2023, 7510.5), row("NGA", 2005, 5000), row("NGA", 2040, 8000), row("KEN", 2023, 1500)]),
            ["nga"],
        )
        assert got == {"NGA": {2023: 7_510_500.0}}

    def test_a_row_with_no_births_is_skipped(self):
        assert wpp.parse(gz([row("NGA", 2023, "")])) == {}


@pytest.mark.django_db
class TestLoadingAndAuditing:
    @pytest.fixture
    def nigeria(self):
        return boundary(0, "Nigeria", "NGA", 0, 0, size=2)

    def test_rows_are_written_on_the_country_with_estimate_or_projection_said(self, nigeria):
        rows = wpp.load(["NGA"], gz([row("NGA", 2023, 7510), row("NGA", 2025, 7600)]))
        by_year = {r.year: r for r in rows}
        assert by_year[2023].value == 7_510_000 and by_year[2023].extra["projection"] is False
        assert by_year[2025].extra["projection"] is True and by_year[2023].license_code == "cc-by-3.0-igo"

    def test_a_reload_replaces_rather_than_duplicates(self, nigeria):
        for _ in range(2):
            base.upsert(wpp.load(["NGA"], gz([row("NGA", 2023, 7510)])))
        assert IndicatorValue.objects.filter(source=Source.WPP).count() == 1

    def test_the_audit_compares_our_total_with_the_uns_in_the_same_year(self, nigeria):
        value(nigeria, "births", 6_000_000.0, year=2023, source=Source.DERIVED)
        base.upsert(wpp.load(["NGA"], gz([row("NGA", 2023, 7500), row("NGA", 2015, 100)])))
        (got,) = derive.births_audit(["NGA"])
        assert got["ratio"] == pytest.approx(0.8) and got["off"] is True and got["wpp_year"] == 2023

    def test_a_country_within_the_tolerance_is_not_off(self, nigeria):
        value(nigeria, "births", 7_000_000.0, year=2023, source=Source.DERIVED)
        base.upsert(wpp.load(["NGA"], gz([row("NGA", 2023, 7500)])))
        assert derive.births_audit(["NGA"])[0]["off"] is False

    def test_a_country_missing_either_figure_is_not_audited_rather_than_called_fine(self, nigeria):
        value(nigeria, "births", 7_000_000.0, year=2023, source=Source.DERIVED)
        assert derive.births_audit(["NGA"]) == []


def test_it_is_a_registered_policy_governed_count():
    assert measures.get("births_wpp").unit == "births/year"
    assert [e.source for e in policy.POLICY["births_wpp"]] == [Source.WPP]
