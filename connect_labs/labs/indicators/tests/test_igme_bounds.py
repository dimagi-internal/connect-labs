"""The IGME loaders keep the uncertainty bounds UN IGME publishes.

The national and subnational answers carry a lower and an upper bound on every modelled estimate, as coded observation
attributes (a pointer into a list of values, not the number). The loaders used to read only the value, so
``ci_low`` and
``ci_high`` were empty for every neonatal mortality figure and a planner could not tell a firm estimate from a guess.
The fixtures are real answers from sdmx.data.unicef.org (Nigeria and Zimbabwe national; Zimbabwe's provinces).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from connect_labs.labs.indicators.models import IndicatorValue, Source
from connect_labs.labs.indicators.sources import base, igme, igme_subnational
from connect_labs.labs.indicators.tests.test_resolve import make_boundary

FIXTURES = Path(__file__).parent / "fixtures"


def national():
    return json.loads((FIXTURES / "igme_nmr_national.json").read_text())


def subnational():
    return json.loads((FIXTURES / "igme_nmr_subnational_zwe.json").read_text())


class TestDecodingABound:
    def test_an_attribute_is_a_pointer_into_its_own_list_of_values(self):
        structure = {"attributes": {"observation": [{"id": "LOWER_BOUND", "values": [{"id": "7.5"}, {"id": "9.25"}]}]}}
        where = base.sdmx_attribute_positions(structure)
        assert where == {"LOWER_BOUND": (1, ["7.5", "9.25"])}
        assert base.sdmx_number([33.7, 1], where, "LOWER_BOUND") == 9.25  # the value at pointer 1, not the number 1

    def test_no_pointer_no_attribute_and_a_short_observation_are_all_none_not_a_crash(self):
        where = {"LOWER_BOUND": (1, ["7.5"])}
        assert base.sdmx_number([33.7, None], where, "LOWER_BOUND") is None
        assert base.sdmx_number([33.7, 0], where, "UPPER_BOUND") is None
        assert base.sdmx_number([33.7], where, "LOWER_BOUND") is None
        assert base.sdmx_number([33.7, 5], where, "LOWER_BOUND") is None  # a pointer past the end of the list

    def test_a_value_that_is_not_a_number_is_none(self):
        assert base.sdmx_number([1.0, 0], {"STD_ERR": (1, ["n/a"])}, "STD_ERR") is None


class TestNational:
    def test_zimbabwes_estimate_comes_with_its_wide_range(self):
        got = {(iso, year): (v, lo, hi) for iso, year, v, lo, hi in igme._parse(national())}
        v, lo, hi = got[("ZWE", 2024)]
        assert (
            v == pytest.approx(33.7, abs=0.05)
            and lo == pytest.approx(22.5, abs=0.05)
            and hi == pytest.approx(49.7, abs=0.05)
        )

    def test_nigerias_is_narrower_and_the_bounds_bracket_the_estimate_everywhere(self):
        rows = igme._parse(national())
        nga = {year: (v, lo, hi) for iso, year, v, lo, hi in rows if iso == "NGA"}
        assert nga[2024][1] == pytest.approx(31.9, abs=0.05) and nga[2024][2] == pytest.approx(48.1, abs=0.05)
        assert all(lo < v < hi for _, _, v, lo, hi in rows)

    @pytest.mark.django_db
    def test_loading_stores_the_bounds_and_says_how_wide_an_interval_they_are(self, monkeypatch):
        make_boundary("ZWE", 0, "Zimbabwe", "ZWE-ADM0")
        make_boundary("NGA", 0, "Nigeria", "NGA-ADM0", x=5)
        monkeypatch.setattr(igme, "http_json", lambda url, params=None, **k: national())
        igme.load("nmr", ["ZWE", "NGA"])  # rows are returned, then written by the caller
        rows = igme.load("nmr", ["ZWE", "NGA"])
        from connect_labs.labs.indicators.sources.base import upsert

        upsert(rows)
        zwe = IndicatorValue.objects.get(indicator="nmr", iso_code="ZWE", year=2024, source=Source.IGME)
        assert zwe.ci_low == pytest.approx(22.5, abs=0.05) and zwe.ci_high == pytest.approx(49.7, abs=0.05)
        assert zwe.extra["interval_level"] == 0.90

    def test_a_row_with_no_bounds_says_nothing_about_an_interval(self):
        payload = national()
        attrs = payload["data"]["structure"]["attributes"]["observation"]
        for a in attrs:
            if a["id"] in ("LOWER_BOUND", "UPPER_BOUND"):
                a["id"] = "SOMETHING_ELSE"
        assert all(lo is None and hi is None for _, _, _, lo, hi in igme._parse(payload))


class TestSubnational:
    def test_every_modelled_province_carries_bounds_and_the_direct_survey_inputs_do_not_leak_in(self):
        rows = igme_subnational._parse(subnational())
        assert rows and all(r["low"] is not None and r["high"] is not None for r in rows)
        assert all(r["low"] < r["value"] < r["high"] for r in rows)

    def test_a_known_provinces_figure_and_range_at_each_level_it_is_published(self):
        rows = {
            r["level"]: r
            for r in igme_subnational._parse(subnational())
            if r["area_name"] == "Bulawayo" and r["period"].startswith("2021")
        }
        province, district = rows[1], rows[2]  # Bulawayo is both a province and a district
        assert (province["value"], province["low"], province["high"]) == pytest.approx((17.1, 4.08, 48.05), abs=0.05)
        assert (district["value"], district["low"], district["high"]) == pytest.approx((19.55, 5.5, 55.49), abs=0.05)

    def test_the_range_is_wide_enough_to_matter(self):
        """Why it is worth keeping: a province's range spans more than a factor of 5."""
        rows = igme_subnational._parse(subnational())
        assert sorted(r["high"] / r["low"] for r in rows)[len(rows) // 2] > 5
