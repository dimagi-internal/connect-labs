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

    def test_an_unanswerable_empty_is_marked_so_it_is_not_read_as_a_finding(self):
        """Zero rows means two opposite things and the totals cannot tell them apart.

        "0 areas above 50% improved water in Liberia" summarises as good news.
        It is only good news if Liberia could have answered; with no survey
        behind the indicator the question was never asked. A model reading this
        JSON has none of the page's prose to warn it, so the flag has to be in
        the payload.
        """
        _, region, _ = _nigeria()
        # Boundaries and an unrelated measure exist; this indicator does not.
        set_value(region, "u5mr", 90, source=Source.DHS)

        got = targeting.targeting_select(None, indicator="improved_water", threshold=50)

        assert got["counts"]["areas"] == 0
        assert got["countries_supported"] == []
        assert got["empty_because_unanswerable"] is True

    def test_a_genuinely_empty_result_is_not_marked_unanswerable(self):
        _, region, _ = _nigeria()
        # Nigeria CAN answer; no area simply clears the bar.
        set_value(region, "improved_water", 95.0, source=Source.DHS)

        got = targeting.targeting_select(None, indicator="improved_water", threshold=10)

        assert got["counts"]["areas"] == 0
        assert got["countries_supported"], "the country could answer, so it must be listed"
        assert got["empty_because_unanswerable"] is False

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
        # The decline is only useful if it says what to use instead, and it must
        # not offer the two case bases it just refused.
        assert "under_5" in str(err.value)
        assert "case_year" not in str(err.value)

    def test_a_rejected_basis_names_the_legal_values(self):
        """'Unknown basis' with no list is a dead end.

        The caller reaches for a basis after reading a selection, where the counts
        are called pop_u5 / births / pop_total, so guessing wrong is the normal
        case rather than the careless one.
        """
        _, region, _ = _nigeria()
        set_value(region, "u5mr", 150, source=Source.DHS)

        with pytest.raises(MCPToolError) as err:
            targeting.targeting_scenario(None, indicator="u5mr", basis="not_a_basis", unit_cost=10)

        message = str(err.value)
        assert "not_a_basis" in message
        for legal in ("birth", "under_5", "person", "household", "case", "case_year"):
            assert legal in message

    def test_a_count_field_name_is_accepted_as_a_basis(self):
        """pop_u5 is what the rows and targeting_compare_criteria's `count` call it."""
        _, region, other = _nigeria()
        set_value(region, "u5mr", 150, source=Source.DHS)
        set_value(other, "u5mr", 150, source=Source.DHS)
        set_value(region, "pop_u5", 2000)

        by_column = targeting.targeting_scenario(
            None, indicator="u5mr", threshold=80, basis="pop_u5", unit_cost=3, method="subnational_survey"
        )
        by_basis = targeting.targeting_scenario(
            None, indicator="u5mr", threshold=80, basis="under_5", unit_cost=3, method="subnational_survey"
        )

        assert by_column["basis"] == "under_5"
        assert by_column["absorbable_usd"] == by_basis["absorbable_usd"] == 6000


class TestAdminLevelsSelectability:
    """`loaded` reports every source; targeting can only select on geoBoundaries.

    That distinction used to live in the last sentence of a note about
    double-counting, and it is the sentence that decides whether an admin_level
    can be asked for at all. Six countries — Nigeria, DR Congo, Kenya, Côte
    d'Ivoire, Mozambique and CAR — carry an ADM2 under a different source and
    none under geoBoundaries, so `loaded` advertises districts that targeting
    drops the country for.
    """

    def test_it_separates_levels_targeting_can_use_from_levels_it_cannot(self):
        from connect_labs.labs.admin_boundaries.models import AdminBoundary
        from connect_labs.labs.indicators.tests.test_resolve import _square

        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        # An ADM2 that exists, under a source targeting does not select on.
        AdminBoundary.objects.create(
            iso_code="NGA",
            admin_level=2,
            name="Some LGA",
            source=AdminBoundary.Source.GEOPODE,
            boundary_id="NGA-2-geopode-1",
            geometry=_square(4, 0),
        )

        got = targeting.targeting_admin_levels(None, iso_codes=["NGA"])

        assert got["selectable_by_targeting"]["NGA"]["levels"] == [0, 1]
        assert got["selectable_by_targeting"]["NGA"]["levels_only_in_other_sources"] == [2]
        # ...and the raw view still shows it, so nothing is hidden.
        assert got["loaded"]["NGA"]["geopode"]["ADM2"] == 1


class TestSchemaMatchesImplementation:
    """The schema is the only surface an MCP caller has.

    Every tool sets ``additionalProperties: False``, so a parameter the function
    accepts but the schema omits is not merely undocumented — it is unreachable,
    and it fails as though it were never built. Both defects this class guards
    shipped that way: ``targeting_select`` implemented ``admin_level``,
    ``target_year`` and ``rollup`` while offering none of them (and two sibling
    tools described their own ``target_year`` as "See targeting_select"), and
    ``targeting_scenario``'s enum omitted the ``case_year`` basis, leaving callers
    able to price only a fortnight of cases where they meant a year.
    """

    def _tool(self, name):
        from connect_labs.mcp import tool_registry

        tool = tool_registry.get_tool(name)
        assert tool is not None, f"{name} is not registered"
        return tool

    @pytest.mark.parametrize(
        "name",
        [
            "targeting_indicators",
            "targeting_select",
            "targeting_methodology",
            "targeting_scenario",
            "targeting_admin_levels",
            "targeting_research",
            "targeting_research_write",
            "targeting_compare_criteria",
        ],
    )
    def test_every_accepted_parameter_is_offered_by_the_schema(self, name):
        import inspect

        tool = self._tool(name)
        offered = set((tool.input_schema.get("properties") or {}).keys())
        accepted = {
            p.name
            for p in inspect.signature(tool.handler).parameters.values()
            if p.kind is inspect.Parameter.KEYWORD_ONLY
        }

        hidden = sorted(accepted - offered)
        assert not hidden, (
            f"{name} accepts {hidden} but does not offer them in its input_schema. "
            "With additionalProperties: False that makes them unreachable over MCP."
        )

    def test_the_basis_enum_offers_every_unit_basis(self):
        from connect_labs.labs.indicators import interventions

        offered = set(self._tool("targeting_scenario").input_schema["properties"]["basis"]["enum"])
        assert offered == {b.value for b in interventions.UnitBasis}


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


class TestCompareCriteria:
    """Several defensible screens give different answers, and that is the finding.

    Taken from a hand-built proposal that made the point in prose: one county's
    ORS coverage is fourth lowest so a coverage screen keeps it, its diarrhoea
    prevalence is the lowest by half so a prevalence screen drops it. Three
    defensible screens, three answers for the same place. The tool used to
    return whichever screen was asked for and say nothing about the others.
    """

    def _two_counties(self):
        make_boundary("LBR", 0, "Liberia", "LBR-0", x=0)
        low_ors = make_boundary("LBR", 1, "LowORS", "LBR-1-1", x=2)
        high_dia = make_boundary("LBR", 1, "HighDiarrhoea", "LBR-1-2", x=4)
        # Contested: each county is kept by exactly one of the two screens.
        set_value(low_ors, "ors_coverage", 30.0, source=Source.DHS)
        set_value(low_ors, "diarrhoea_prevalence", 5.0, source=Source.DHS)
        set_value(low_ors, "pop_u5", 100_000, source=Source.WORLDPOP_RASTER)
        set_value(high_dia, "ors_coverage", 90.0, source=Source.DHS)
        set_value(high_dia, "diarrhoea_prevalence", 25.0, source=Source.DHS)
        set_value(high_dia, "pop_u5", 50_000, source=Source.WORLDPOP_RASTER)

    def test_it_reports_which_screen_keeps_each_area(self):
        self._two_counties()
        got = targeting.targeting_compare_criteria(
            None,
            criteria=[
                {"indicator": "ors_coverage", "threshold": 70, "label": "coverage"},
                {"indicator": "diarrhoea_prevalence", "threshold": 15, "label": "prevalence"},
            ],
            iso_codes=["LBR"],
        )
        kept = {r["area"]: r["kept_by"] for r in got["areas"]}
        assert kept["LowORS"] == ["coverage"]
        assert kept["HighDiarrhoea"] == ["prevalence"]

    def test_it_says_how_much_of_the_answer_is_contested(self):
        self._two_counties()
        got = targeting.targeting_compare_criteria(
            None,
            criteria=[
                {"indicator": "ors_coverage", "threshold": 70},
                {"indicator": "diarrhoea_prevalence", "threshold": 15},
            ],
            iso_codes=["LBR"],
        )
        assert got["unanimous"] == 0
        assert got["contested"] == 2
        assert got["contested_share_of_count"] == 100.0

    def test_an_unanswerable_screen_is_not_counted_as_agreement(self):
        """A screen that could not be asked must not silently narrow the comparison.

        It contributes no areas, so every area it might have KEPT looks unanimous
        among the screens that did run. The agreement is then overstated, and the
        caller has no way to see it from the numbers.
        """
        self._two_counties()

        got = targeting.targeting_compare_criteria(
            None,
            criteria=[
                {"indicator": "ors_coverage", "threshold": 70, "label": "coverage"},
                # Liberia has no survey behind this one; the screen cannot be asked.
                {"indicator": "improved_water", "threshold": 50, "label": "water"},
            ],
            iso_codes=["LBR"],
        )

        water = next(s for s in got["screens"] if s["label"] == "water")
        coverage = next(s for s in got["screens"] if s["label"] == "coverage")
        assert water["empty_because_unanswerable"] is True
        assert coverage["empty_because_unanswerable"] is False
        assert got["unanswerable_screens"] == ["water"]
        assert "WARNING" in got["advice"]
        assert "overstated" in got["advice"]

    def test_a_wholly_unanswerable_comparison_says_so_instead_of_reporting_zero(self):
        """The admin_level=2 shape: nothing can be asked, so nothing comes back.

        The old advice line reported this as '0 areas are selected by every screen
        ... holding 0% of the pop u5 in play', which reads as a finding that no
        area qualifies. It is a finding that the question was never asked.
        """
        self._two_counties()

        got = targeting.targeting_compare_criteria(
            None,
            criteria=[
                {"indicator": "improved_water", "threshold": 50},
                {"indicator": "improved_sanitation", "threshold": 50},
            ],
            iso_codes=["LBR"],
        )

        assert got["areas"] == []
        assert got["empty_because_unanswerable"] is True
        assert len(got["unanswerable_screens"]) == 2
        assert "could not be run" in got["advice"]
        assert "NOT a finding" in got["advice"]
        # The old sentence must not be reachable in this state.
        assert "selected by every screen" not in got["advice"]

    def test_a_country_with_no_boundary_at_the_pinned_level_is_named_not_dropped(self):
        """Pinning a level a country lacks used to remove it with no trace.

        Subnational spans levels (1, 2) and there is no ADM0 to fall back to, so
        a country with no ADM2 produced no units and hit a bare ``continue``. It
        was not in countries_unsupported (the method CAN answer it) and not in
        skipped_no_data (that needs units to evaluate), so a level-2 answer
        quietly excluded it and read as "nothing there qualifies".

        Live: Nigeria and Kenya carry ADM2 in geopode but NOT in geoBoundaries,
        which is what targeting selects on -- so admin_level=2 returned zero for
        both, while targeting_admin_levels still advertised 774 and 316 units.
        """
        make_boundary("LBR", 0, "Liberia", "LBR-0", x=0)
        county = make_boundary("LBR", 1, "Bong", "LBR-1-1", x=2)
        district = make_boundary("LBR", 2, "Jorwah", "LBR-2-1", x=3)
        set_value(county, "ors_coverage", 30.0, source=Source.DHS)
        set_value(district, "ors_coverage", 30.0, source=Source.DHS)
        set_value(district, "pop_u5", 1000, source=Source.WORLDPOP_RASTER)
        # Nigeria is answerable at ADM1 and has no ADM2 at all.
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=10)
        state = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=12)
        set_value(state, "ors_coverage", 30.0, source=Source.DHS)
        set_value(state, "pop_u5", 5000, source=Source.WORLDPOP_RASTER)

        pinned = targeting.targeting_select(
            None, indicator="ors_coverage", threshold=50, iso_codes=["LBR", "NGA"], admin_level=2
        )

        assert "Nigeria" in pinned["countries_missing_level"]
        assert "Nigeria" not in pinned["countries_unsupported"], "the method can answer Nigeria"
        assert "Liberia" not in pinned["countries_missing_level"], "Liberia has an ADM2"

    def test_without_a_pinned_level_nothing_is_reported_as_missing(self):
        """The field describes a pinning decision, not a property of the data."""
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=10)
        state = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=12)
        set_value(state, "ors_coverage", 30.0, source=Source.DHS)

        got = targeting.targeting_select(None, indicator="ors_coverage", threshold=50, iso_codes=["NGA"])

        assert got["countries_missing_level"] == []
        assert got["rows_total"] >= 1, "unpinned, Nigeria answers at ADM1"

    def test_a_genuinely_empty_comparison_is_not_marked_unanswerable(self):
        """Answerable screens that simply keep nothing stay a real finding."""
        self._two_counties()

        got = targeting.targeting_compare_criteria(
            None,
            criteria=[
                # Both answerable in Liberia; neither county clears these bars.
                {"indicator": "ors_coverage", "threshold": 1},
                {"indicator": "diarrhoea_prevalence", "threshold": 99},
            ],
            iso_codes=["LBR"],
        )

        assert got["empty_because_unanswerable"] is False
        assert got["unanswerable_screens"] == []

    def test_one_criterion_is_refused_because_there_is_nothing_to_compare(self):
        with pytest.raises(Exception):
            targeting.targeting_compare_criteria(None, criteria=[{"indicator": "ors_coverage"}], iso_codes=["LBR"])

    def test_a_criterion_without_an_indicator_says_which_one(self):
        with pytest.raises(MCPToolError, match=r"criteria\[1\]"):
            targeting.targeting_compare_criteria(
                None,
                criteria=[{"indicator": "ors_coverage"}, {"threshold": 15}],
                iso_codes=["LBR"],
            )


class TestRankingControls:
    """A universal programme has no threshold to fail.

    Every unit qualifies, so the rollup returns one country row — correct for
    "the whole country is above the line", and it collapses exactly the ranking
    the question asked for. Found by running the skill against a real request:
    rank Liberia's counties for a programme that reaches every child.
    """

    def _liberia(self):
        make_boundary("LBR", 0, "Liberia", "LBR-0", x=0)
        for i, (name, cov) in enumerate([("Bong", 41.0), ("Nimba", 51.3), ("Bomi", 75.5)]):
            b = make_boundary("LBR", 1, name, f"LBR-1-{i}", x=2 + i * 2)
            set_value(b, "ors_coverage", cov, source=Source.DHS)
            set_value(b, "pop_u5", 50_000, source=Source.WORLDPOP_RASTER)

    def test_by_default_a_wholly_qualifying_country_is_one_row(self):
        self._liberia()
        got = targeting.targeting_select(
            None, indicator="ors_coverage", threshold=95, iso_codes=["LBR"], method="subnational_survey"
        )
        assert got["counts"]["areas"] == 1
        assert got["rows"][0]["whole_country"] is True

    def test_rollup_false_returns_the_units_themselves(self):
        self._liberia()
        got = targeting.targeting_select(
            None,
            indicator="ors_coverage",
            threshold=95,
            iso_codes=["LBR"],
            method="subnational_survey",
            rollup=False,
        )
        assert got["counts"]["areas"] == 3
        assert {r["area"] for r in got["rows"]} == {"Bong", "Nimba", "Bomi"}

    def test_a_row_says_whether_its_own_estimate_is_thin(self):
        """The total says how many are thin; a reader scanning needs which."""
        self._liberia()
        b = make_boundary("LBR", 1, "Gbarpolu", "LBR-1-9", x=20)
        v = set_value(b, "ors_coverage", 70.3, source=Source.DHS)
        v.extra = {"sample_unweighted": 47}
        v.save(update_fields=["extra"])
        set_value(b, "pop_u5", 20_000, source=Source.WORLDPOP_RASTER)

        got = targeting.targeting_select(
            None,
            indicator="ors_coverage",
            threshold=95,
            iso_codes=["LBR"],
            method="subnational_survey",
            rollup=False,
        )
        rows = {r["area"]: r for r in got["rows"]}
        assert rows["Gbarpolu"]["small_sample"] is True
        assert rows["Gbarpolu"]["sample_unweighted"] == 47
        assert rows["Bong"]["small_sample"] is False


class TestResearchWriteRefusesWhatTheColumnCannotHold:
    """An overlong summary used to surface as a psycopg2 traceback.

    `ResearchNote.summary` is varchar(300). Exceeding it raised
    StringDataRightTruncation, which names neither the field nor the limit — so
    a caller who had just supplied a summary, a body, and ten alternatives had
    no way to tell which string was too long, and the note silently failed to
    save after the checks had already run.
    """

    def test_it_names_the_field_and_the_limit(self):
        from connect_labs.mcp.tools import targeting

        with pytest.raises(MCPToolError) as excinfo:
            targeting.targeting_research_write(
                None,
                topic="a-topic",
                summary="x" * 301,
                body="the reasoning",
            )

        message = str(excinfo.value)
        assert "summary" in message
        assert "301" in message and "300" in message

    def test_a_summary_at_the_limit_is_accepted(self):
        from connect_labs.mcp.tools import targeting

        got = targeting.targeting_research_write(
            None,
            topic="a-topic",
            summary="x" * 300,
            body="the reasoning",
        )
        assert got["saved"] is True
