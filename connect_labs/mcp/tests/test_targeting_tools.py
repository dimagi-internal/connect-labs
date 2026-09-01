"""Targeting MCP tools — the behaviours a chat session depends on.

These tools exist so an investigation can happen in a conversation, and a model
summarising their output cannot see the caveats a human reads off the page. So
the tests here are mostly about what the tools REFUSE to let a caller assume:
which way a threshold reads, that a percent threshold has no second reading,
that a method which cannot answer is not silently substituted for, and that a
total built on partial data is labelled a floor.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import targeting

pytestmark = pytest.mark.django_db


def _nigeria():
    country = make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
    region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
    other = make_boundary("NGA", 1, "Lagos", "NGA-1-2", x=4)
    return country, region, other


class TestIndicators:
    def test_it_says_which_way_each_family_reads(self):
        listed = {i["indicator"]: i for i in targeting.targeting_indicators(None)["indicators"]}

        assert listed["u5mr"]["family"] == "burden"
        assert listed["u5mr"]["selects"] == "above the threshold"
        assert listed["improved_sanitation"]["family"] == "coverage"
        assert listed["improved_sanitation"]["selects"] == "below the threshold"

    def test_only_a_per_1000_rate_carries_a_percent_reading(self):
        """A percent threshold rendered as a tenth of itself is the bug this prevents."""
        listed = {i["indicator"]: i for i in targeting.targeting_indicators(None)["indicators"]}

        assert listed["u5mr"]["percent_equivalent_of_default"] == 8.0
        assert listed["improved_sanitation"]["percent_equivalent_of_default"] is None

    def test_an_unknown_indicator_is_refused_with_a_way_forward(self):
        with pytest.raises(MCPToolError) as err:
            targeting.targeting_indicators(None, indicator="not_a_measure")
        assert "not_a_measure" in str(err.value)


class TestSelect:
    def test_it_returns_totals_and_the_rows_behind_them(self):
        _, region, _ = _nigeria()
        # The survey source explicitly: this asks for "Survey as measured",
        # and an indicator may only be answered from a source it names.
        set_value(region, "u5mr", 150, source=Source.DHS)
        set_value(region, "births", 1000)

        got = targeting.targeting_select(None, indicator="u5mr", threshold=80, method="subnational_survey")

        assert got["counts"]["units"] == 1
        assert got["totals"]["births"] == 1000
        assert got["rows"][0]["area"] == "Kano"
        assert got["rows"][0]["value"] == 150.0

    def test_coverage_reports_the_shortfall_that_makes_a_total_a_floor(self):
        _, region, other = _nigeria()
        set_value(region, "u5mr", 150, source=Source.DHS)
        set_value(other, "u5mr", 150, source=Source.DHS)
        set_value(region, "births", 1000)  # only one of the two selected units

        got = targeting.targeting_select(None, indicator="u5mr", threshold=80, method="subnational_survey")

        assert got["coverage"]["births"] == {"with_value": 1, "of": 2}

    def test_an_explicit_method_that_cannot_answer_is_honoured_not_substituted(self):
        """Silently swapping the method would hide the one thing worth learning."""
        _, region, _ = _nigeria()
        set_value(region, "improved_sanitation", 20, source=Source.DHS)

        got = targeting.targeting_select(
            None, indicator="improved_sanitation", threshold=50, method="subnational_igme"
        )

        assert got["method"] == "subnational_igme"
        assert got["counts"]["units"] == 0
        assert "Nigeria" in got["countries_unsupported"]

    def test_the_default_method_is_one_that_can_answer_this_indicator(self):
        _, region, _ = _nigeria()
        set_value(region, "improved_sanitation", 20, source=Source.DHS)

        got = targeting.targeting_select(None, indicator="improved_sanitation", threshold=50)

        assert got["method"] != "subnational_igme"
        assert got["counts"]["units"] == 1

    def test_rows_are_capped_so_a_chat_gets_a_summary_not_a_dump(self):
        _, region, _ = _nigeria()
        set_value(region, "u5mr", 150, source=Source.DHS)

        got = targeting.targeting_select(None, indicator="u5mr", threshold=80, method="subnational_survey", limit=9999)

        assert got["rows_returned"] <= targeting.MAX_ROW_LIMIT
        assert got["rows_total"] == got["counts"]["areas"]


class TestMethodology:
    def test_it_returns_the_same_text_the_download_ships(self):
        from connect_labs.labs.indicators import export
        from connect_labs.labs.indicators.resolve import select_above

        _, region, _ = _nigeria()
        set_value(region, "u5mr", 150, source=Source.DHS)

        got = targeting.targeting_methodology(None, indicator="u5mr", threshold=80, method="subnational_survey")
        expected = export.to_methodology(select_above(indicator="u5mr", threshold=80.0, method="subnational_survey"))

        # Line 2 is a timestamp; everything after it must match exactly.
        assert got["markdown"].splitlines()[3:] == expected.splitlines()[3:]


class TestScenario:
    def test_it_prices_the_selection_and_flags_a_floor(self):
        _, region, other = _nigeria()
        set_value(region, "u5mr", 150, source=Source.DHS)
        set_value(other, "u5mr", 150, source=Source.DHS)
        set_value(region, "births", 1000)

        got = targeting.targeting_scenario(
            None, indicator="u5mr", threshold=80, basis="birth", unit_cost=60, method="subnational_survey"
        )

        assert got["units"] == 1000
        assert got["absorbable_usd"] == 60000
        assert got["is_floor"] is True
        assert "floor" in got["caveat"]

    def test_a_basis_with_no_case_count_is_declined_rather_than_approximated(self):
        _, region, _ = _nigeria()
        set_value(region, "stunting", 40, source=Source.DHS)

        with pytest.raises(MCPToolError) as err:
            targeting.targeting_scenario(None, indicator="stunting", basis="case", unit_cost=10)

        assert "no count" in str(err.value)


class TestAdminLevels:
    def test_it_reports_what_is_loaded_per_source(self):
        _nigeria()

        got = targeting.targeting_admin_levels(None, iso_codes=["NGA"])

        assert got["loaded"]["NGA"]["geoboundaries"]["ADM1"] == 2
        # The shared-table warning must travel with the answer, not live in a doc.
        assert "double-counts" in got["note"]

    def test_a_country_with_nothing_loaded_is_reported_as_empty_not_missing(self):
        got = targeting.targeting_admin_levels(None, iso_codes=["ZZZ"])

        assert got["loaded"] == {"ZZZ": {}}
