"""Low birthweight: the loader, the count, and the honesty around a regional figure.

KMC was priced per birth, which overstated eligible babies about sevenfold. The
fix is a count, ``births_lbw``, built from a national rate that sixteen African
countries do not publish. What these tests guard is mostly the second half:
that a country carrying its region's rate can never be read as having been
measured, anywhere a number from it surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command

from connect_labs.labs.indicators import export, interventions, measures, policy
from connect_labs.labs.indicators.africa import ISO_CODES, M49_SUBREGION
from connect_labs.labs.indicators.models import IndicatorValue, Source
from connect_labs.labs.indicators.resolve import carried_for, resolve, select_above
from connect_labs.labs.indicators.sources import base, derive, dhs, unicef_lbw
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

pytestmark = pytest.mark.django_db

#: A real UNICEF SDMX response, trimmed to seven areas and two years with its
#: indices remapped and its values untouched. See the file's own meta note.
FIXTURE = Path(__file__).parent / "fixtures" / "unicef_lbw_nt_bw_lbw.json"
RELEASE = "UNICEF-WHO Global Low Birthweight Estimates Databases, July 2023"


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


# ---------------------------------------------------------------------------
# Decoding the recorded payload
# ---------------------------------------------------------------------------


class TestDecode:
    def test_dimensions_and_attributes_resolve_by_declared_position(self, payload):
        """Positional indices misattribute silently when read at a fixed offset."""
        rows = {(r["REF_AREA"], r["TIME_PERIOD"]): r for r in unicef_lbw.decode(payload)}

        kenya = rows[("KEN", "2020")]
        assert kenya["_value"] == "9.9711"
        assert kenya["LOWER_BOUND"] == "8.4990"
        assert kenya["UPPER_BOUND"] == "11.6829"
        assert kenya["INDICATOR"] == "NT_BW_LBW"
        assert kenya["SEX"] == "_T"

    def test_the_release_name_is_in_the_footnote_not_the_data_source(self, payload):
        """Filtering DATA_SOURCE on 'UNICEF-WHO' loses every country: it is a filename."""
        kenya = next(r for r in unicef_lbw.decode(payload) if r["REF_AREA"] == "KEN")

        assert kenya["DATA_SOURCE"] == "CMRS_SERIES_LBW.csv"
        assert kenya["SERIES_FOOTNOTE"] == RELEASE

    def test_regional_aggregates_carry_their_name_and_coverage_note(self, payload):
        west = next(
            r for r in unicef_lbw.decode(payload) if r["REF_AREA"] == "UNSDG_WESTERNAFR" and r["TIME_PERIOD"] == "2020"
        )
        assert west["REF_AREA_name"] == "Western Africa (UNSDG)"
        assert west["_value"] == "14.2631"
        assert "population coverage" in west["OBS_FOOTNOTE"]


# ---------------------------------------------------------------------------
# The loader: national where published, regional where not, nothing otherwise
# ---------------------------------------------------------------------------


class TestLoad:
    def _countries(self, *codes):
        return {c: make_boundary(c, 0, c, f"{c}-0", x=i * 2) for i, c in enumerate(codes)}

    def test_a_published_country_gets_its_latest_national_estimate(self, payload):
        self._countries("KEN")

        (row,) = unicef_lbw.load(["KEN"], payload=payload)

        assert row.indicator == "lbw_rate"
        assert row.year == 2020  # latest, not 2019
        assert row.value == pytest.approx(9.9711)
        assert (row.ci_low, row.ci_high) == (pytest.approx(8.499), pytest.approx(11.6829))
        assert row.source == Source.UNICEF_LBW
        assert RELEASE in row.source_ref
        assert row.extra["regional_proxy"] is False

    def test_a_country_with_no_estimate_carries_its_m49_subregion(self, payload):
        """Nigeria is absent from the national series; its subregion is Western Africa."""
        self._countries("NGA")

        (row,) = unicef_lbw.load(["NGA"], payload=payload)

        assert row.source == Source.UNICEF_LBW_REGIONAL
        # UN SDG Western Africa, not UNICEF's coarser West and Central Africa
        # (13.419), which the fixture also carries.
        assert row.value == pytest.approx(14.2631)
        assert row.extra["regional_proxy"] is True
        assert row.extra["proxy_region"] == "UNSDG_WESTERNAFR"
        assert "Western Africa" in row.source_ref and "no national estimate" in row.source_ref
        assert row.method.startswith("REGIONAL PROXY")
        assert "Nigeria" in row.method

    def test_an_eastern_african_country_takes_the_eastern_aggregate(self, payload):
        self._countries("ETH")
        (row,) = unicef_lbw.load(["ETH"], payload=payload)
        assert row.extra["proxy_region"] == "UNSDG_EASTERNAFR"
        assert row.value == pytest.approx(14.1087)

    def test_with_neither_a_national_nor_a_regional_figure_there_is_no_row(self, payload):
        """Chad's subregion, Middle Africa, is not in the fixture: refuse, don't approximate."""
        self._countries("TCD")
        assert unicef_lbw.load(["TCD"], payload=payload) == []

    def test_a_published_country_is_never_given_the_regional_figure(self, payload):
        self._countries("KEN", "RWA", "MOZ", "NGA")
        rows = {r.boundary.iso_code: r for r in unicef_lbw.load(["KEN", "RWA", "MOZ", "NGA"], payload=payload)}
        assert {iso for iso, r in rows.items() if r.extra["regional_proxy"]} == {"NGA"}

    def test_every_african_country_has_exactly_one_subregion(self):
        assert set(M49_SUBREGION) == set(ISO_CODES)
        assert set(M49_SUBREGION.values()) == {
            "UNSDG_NORTHAFR",
            "UNSDG_EASTERNAFR",
            "UNSDG_MIDDLEAFR",
            "UNSDG_SOUTHERNAFR",
            "UNSDG_WESTERNAFR",
        }


# ---------------------------------------------------------------------------
# Inheritance: a regional figure is never mistaken for a national one
# ---------------------------------------------------------------------------


@pytest.fixture
def nigeria_and_kenya(payload):
    """Two countries, one region each: Nigeria on a regional rate, Kenya on its own."""
    nga = make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
    kano = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
    ken = make_boundary("KEN", 0, "Kenya", "KEN-0", x=10)
    nairobi = make_boundary("KEN", 1, "Nairobi", "KEN-1-1", x=12)
    base.upsert(unicef_lbw.load(["NGA", "KEN"], payload=payload))
    for region, births, u5 in ((kano, 100_000, 460_000), (nairobi, 50_000, 230_000)):
        set_value(region, "births", births)
        set_value(region, "pop_u5", u5)
        set_value(region, "u5mr", 120, source=Source.DHS)
    return nga, kano, ken, nairobi


class TestRegionalProxyIsInherited:
    def test_a_regional_row_resolves_as_inherited_even_on_its_own_country(self, nigeria_and_kenya):
        nga, _, ken, _ = nigeria_and_kenya

        regional = resolve("lbw_rate", nga)
        national = resolve("lbw_rate", ken)

        assert regional.regional_proxy and regional.inherited
        assert "Western Africa" in regional.measured_at_label
        assert "measured for Western Africa" in regional.provenance
        assert not national.inherited

    def test_a_national_selection_counts_regional_countries_as_inherited(self, nigeria_and_kenya):
        sel = select_above(indicator="lbw_rate", threshold=0, method="national_modelled")

        by_country = {a.iso_code: a for a in sel.areas}
        assert by_country["NGA"].inherited_units == 1
        assert by_country["KEN"].inherited_units == 0
        assert sel.inherited_units == 1

    def test_the_regional_source_is_strictly_second(self):
        assert policy.sources("lbw_rate") == (Source.UNICEF_LBW, Source.UNICEF_LBW_REGIONAL)


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------


class TestLbwBirths:
    def test_it_is_births_times_the_rate(self, nigeria_and_kenya):
        _, kano, _, nairobi = nigeria_and_kenya

        rows = {r.boundary.pk: r for r in derive.load_lbw_births(iso_codes=["NGA", "KEN"])}

        assert rows[kano.pk].value == pytest.approx(100_000 * 14.2631 / 100)
        assert rows[nairobi.pk].value == pytest.approx(50_000 * 9.9711 / 100)

    def test_a_count_on_a_regional_rate_says_so(self, nigeria_and_kenya):
        _, kano, _, nairobi = nigeria_and_kenya
        rows = {r.boundary.pk: r for r in derive.load_lbw_births(iso_codes=["NGA", "KEN"])}

        assert rows[kano.pk].extra["regional_proxy"] is True
        assert "REGIONAL aggregate" in rows[kano.pk].method
        assert rows[nairobi.pk].extra["regional_proxy"] is False
        # The rate is national either way, so both regions inherited it.
        assert rows[kano.pk].extra["lbw_inherited"] and rows[nairobi.pk].extra["lbw_inherited"]

    def test_no_rate_means_no_count_rather_than_a_guess(self):
        region = make_boundary("TCD", 1, "Ouaddai", "TCD-1-1", x=0)
        set_value(region, "births", 10_000)
        assert derive.load_lbw_births(iso_codes=["TCD"]) == []

    def test_a_selection_reports_how_much_of_the_count_is_regional(self, nigeria_and_kenya):
        base.upsert(derive.load_lbw_births(iso_codes=["NGA", "KEN"]))

        sel = select_above(indicator="u5mr", threshold=80, method="subnational_survey")

        assert sel.totals["births_lbw"] == pytest.approx(100_000 * 0.142631 + 50_000 * 0.099711)
        assert sel.regional_proxy_units == {"births_lbw": 1}

    def test_newborn_indicators_carry_it_and_others_do_not(self):
        assert "births_lbw" in carried_for("u5mr")
        assert "births_lbw" in carried_for("nmr")
        assert "births_lbw" in carried_for("facility_delivery")
        assert "births_lbw" not in carried_for("improved_water")

    def test_the_export_names_the_regional_rows(self, nigeria_and_kenya):
        base.upsert(derive.load_lbw_births(iso_codes=["NGA", "KEN"]))
        sel = select_above(indicator="u5mr", threshold=80, method="subnational_survey")

        csv = export.to_csv(sel)
        md = export.to_methodology(sel, alternatives=False)

        assert "Est. annual low-birthweight births" in csv
        assert "regional aggregate (no national estimate)" in csv
        assert "national estimate" in csv
        assert "rest on a regional aggregate, not a national estimate" in md
        assert "Nigeria" in md


# ---------------------------------------------------------------------------
# KMC points at the count
# ---------------------------------------------------------------------------


class TestKmcBasis:
    def test_kmc_is_priced_per_low_birthweight_birth(self):
        kmc = interventions.get("kmc")
        assert kmc.basis is interventions.UnitBasis.LBW_BIRTH
        assert kmc.cases_measure() == "births_lbw"
        assert "2,500 g" in kmc.caveat and "NOT" in kmc.caveat

    def test_the_plain_birth_basis_still_counts_every_birth(self):
        assert interventions.measure_for(interventions.UnitBasis.BIRTH) == "births"

    def test_births_lbw_is_a_summable_count(self):
        m = measures.get("births_lbw")
        assert not m.is_rate and m.agg is measures.Agg.SUM


# ---------------------------------------------------------------------------
# Facility delivery: DHS's preferred recall window, not whichever came last
# ---------------------------------------------------------------------------


class TestPreferredRecallWindow:
    def _rec(self, value, preferred, window):
        return {
            "DHS_CountryCode": "NG",
            "SurveyYear": "2024",
            "CharacteristicLabel": "Kano",
            "Value": value,
            "IsPreferred": preferred,
            "ByVariableLabel": window,
        }

    @pytest.mark.parametrize("order", ["preferred_first", "preferred_last"])
    def test_the_preferred_window_wins_whatever_the_order(self, order):
        recs = [self._rec(40.0, 1, "Two years preceding the survey"), self._rec(38.0, 0, "Three years preceding")]
        if order == "preferred_last":
            recs.reverse()

        (kept,) = dhs._latest_survey_per_country(recs).values()

        assert kept["Value"] == 40.0

    def test_facility_delivery_is_a_registered_coverage_measure(self):
        assert dhs.INDICATORS["facility_delivery"]["value"] == "RH_DELP_C_DHF"
        assert "facility_delivery" in measures.LOWER_IS_WORSE
        assert measures.get("facility_delivery_gap").unit == "births/year"


# ---------------------------------------------------------------------------
# The births defect this work surfaced
# ---------------------------------------------------------------------------


class TestStaleDerivationsAreSwept:
    """Rwanda's births read 64,370 against a true ~400,000.

    Not a unit error and not a join: a derived row's natural key includes its
    year, and the year is its INPUTS' vintage. An old fertility-method row built
    on HAPI's 2023 table sat beside the current infant-cohort row built on
    WorldPop's 2022 grid, and the resolver prefers the most recent year, so the
    stale row won. Across Africa 196 of 2,294 units were outside a plausible
    births-per-under-five band on that account, and every honesty field passed.
    """

    @pytest.fixture
    def province(self):
        country = make_boundary("RWA", 0, "Rwanda", "RWA-0", x=0)
        region = make_boundary("RWA", 1, "Eastern Province", "RWA-1-1", x=2)
        set_value(region, "pop_u1", 110_000, year=2022)
        set_value(region, "pop_u5", 533_000, year=2022)
        set_value(country, "imr", 30.0, year=2024, source=Source.IGME)
        # The stale derivation: a later YEAR, from an earlier run.
        stale = set_value(region, "births", 12_588, year=2023, source=Source.DERIVED)
        stale.extra = {"method_key": derive.FERTILITY}
        stale.save()
        return region

    def test_the_plausibility_tripwire_catches_it_before_the_fix(self, province):
        set_value(province, "u5mr", 50, source=Source.DHS)
        sel = select_above(indicator="u5mr", threshold=10, method="subnational_survey")

        assert sel.coverage["births"] == (1, 1)  # complete, so coverage cannot see it
        assert sel.inherited_units == 0  # nor can this
        assert sel.births_implausible_units == 1  # this can

    def test_the_births_stage_leaves_only_the_current_derivation(self, province):
        call_command("load_indicators", "--stage", "births", "--iso", "RWA")

        rows = IndicatorValue.objects.filter(indicator="births", boundary=province)
        assert rows.count() == 1
        current = resolve("births", province)
        assert current.year == 2022
        assert current.value == pytest.approx(110_000 / (1 - 30 / 1000))
        assert not derive.births_implausible(current.value, 533_000)

    def test_the_bound_brackets_real_cohorts_and_rejects_rwandas_ratio(self):
        assert not derive.births_implausible(402_808, 1_895_545)  # Rwanda, fixed: 0.213
        assert not derive.births_implausible(8_059_862, 35_345_985)  # Nigeria: 0.228
        assert derive.births_implausible(64_370, 1_895_545)  # Rwanda, before: 0.034
        assert derive.births_implausible(243_408, 453_129)  # Sofala, before: 0.54
        assert not derive.births_implausible(None, 1_000)  # missing is coverage's job
