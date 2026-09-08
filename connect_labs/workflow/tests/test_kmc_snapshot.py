"""Parity tests for the server-side KMC snapshot.

The risk this file exists for: a snapshot that is subtly different from the live view
is worse than no snapshot, because it is a funder-facing dashboard whose colours
disagree with the one it was captured from. `cEntry` in the render is ported branch
for branch in kmc_snapshot.entry(); each branch is pinned here.
"""

from __future__ import annotations

from connect_labs.workflow.templates import kmc_snapshot as ks

C16 = {"id": "c16", "indicator": "C16", "unit": "%", "direction": "higher", "bands": [50, 30], "min_denominator": 25}
C14 = {"id": "c14", "indicator": "C14", "unit": "%", "direction": "mid2", "bands": [[2, 12], [1, 20]]}
LLO_MAP = {10016: "EHA", 10017: "GHI", 10020: "GHI"}


class TestBandOf:
    def test_higher_direction(self):
        assert ks.band_of("higher", [50, 30], 60) == "green"
        assert ks.band_of("higher", [50, 30], 40) == "yellow"
        assert ks.band_of("higher", [50, 30], 10) == "red"

    def test_lower_direction(self):
        assert ks.band_of("lower", [15, 30], 10) == "green"
        assert ks.band_of("lower", [15, 30], 20) == "yellow"
        assert ks.band_of("lower", [15, 30], 40) == "red"

    def test_two_sided_is_red_beyond_either_end(self):
        """The single outcome a two-sided mortality band exists to prevent: under
        ~2% means deaths are not recorded, not that babies survived."""
        assert ks.band_of("mid2", [[2, 12], [1, 20]], 6) == "green"
        assert ks.band_of("mid2", [[2, 12], [1, 20]], 15) == "yellow"
        assert ks.band_of("mid2", [[2, 12], [1, 20]], 0.5) == "red"
        assert ks.band_of("mid2", [[2, 12], [1, 20]], 30) == "red"

    def test_one_dimensional_bands_on_mid2_are_unbanded_not_green(self):
        assert ks.band_of("mid2", [2, 12], 6) == "unbanded"

    def test_no_bands_and_no_value(self):
        assert ks.band_of("higher", None, 5) == "unbanded"
        assert ks.band_of("higher", [50, 30], None) == "nodata"
        assert ks.band_of("higher", [50, 30], "abc") == "nodata"


class TestEntry:
    def _row(self, **kw):
        row = {"scope": "llo", "llo": "GHI", "anyrec_days_discharge_to_reg": 1}
        row.update(kw)
        return row

    def test_no_row_is_nodata(self):
        assert ks.entry(C16, None, LLO_MAP, {})["band"] == "nodata"

    def test_value_is_scaled_for_percent_units(self):
        e = ks.entry(C16, self._row(c16=94.5, c16_denominator=382, c02=810), LLO_MAP, {})
        assert e["band"] == "green"
        assert abs(e["value"] - 0.945) < 1e-9
        assert e["n"] == 382

    def test_below_min_denominator_reads_insufficient_not_a_number(self):
        e = ks.entry(C16, self._row(c16=94.5, c16_denominator=10, c02=100), LLO_MAP, {})
        assert e["band"] == "insufficient"
        assert e["value"] is None

    def test_not_credible_marks_the_figure_it_does_not_erase_it(self):
        """Blanking hides the under-recording — pooling every LLO reads lower than
        the credible recorders alone."""
        row = {"scope": "llo", "llo": "GHI", "c14": 2.4, "c14_denominator": 373}
        e = ks.entry(C14, row, LLO_MAP, {"C14": {"BERI": True}})
        assert e["band"] == "notcredible"
        assert e["value"] is not None

    def test_c16_thin_coverage_is_footnoted(self):
        """Neal's spec item 8: <45% of started cases carrying a discharge date is a
        self-selected minority, and 96.5% off 20% coverage is the failure case."""
        e = ks.entry(C16, self._row(c16=96.5, c16_denominator=954, c02=4767), LLO_MAP, {})
        assert e["thinDenominator"] is True
        assert e["coverage"] < 0.45

    def test_c16_full_coverage_is_not_footnoted(self):
        e = ks.entry(C16, self._row(c16=97.6, c16_denominator=342, c02=611), LLO_MAP, {})
        assert "thinDenominator" not in e


class TestBuild:
    def _rows(self):
        return [
            {
                "scope": "programme",
                "n_cases": 9011,
                "c16": 73.9,
                "c16_denominator": 4580,
                "c02": 8000,
                "anyrec_days_discharge_to_reg": 1,
            },
            {
                "scope": "llo",
                "llo": "GHI",
                "n_cases": 835,
                "c16": 94.5,
                "c16_denominator": 382,
                "c02": 810,
                "anyrec_days_discharge_to_reg": 1,
            },
            {
                "scope": "opportunity",
                "opportunity_id": 10017,
                "n_cases": 489,
                "c16": 94.5,
                "c16_denominator": 382,
                "c02": 479,
                "anyrec_days_discharge_to_reg": 1,
            },
            {
                "scope": "opportunity",
                "opportunity_id": 10020,
                "n_cases": 346,
                "c16": None,
                "c16_denominator": 0,
                "c02": 331,
                "anyrec_days_discharge_to_reg": 0,
            },
            {
                "scope": "flw",
                "opportunity_id": 10017,
                "username": "flw_001",
                "n_cases": 40,
                "c16": 90.0,
                "c16_denominator": 30,
                "c02": 38,
                "anyrec_days_discharge_to_reg": 1,
            },
        ]

    def _pipelines(self):
        return {
            "children": {
                "rows": [
                    {
                        "entity_id": "case-a",
                        "username": "flw_001",
                        "opportunity_id": 10017,
                        "reg_date": "2026-05-01",
                        "birth_weight_g": 1700,
                        "last_weight_g": 2400,
                        "total_visits": 5,
                    },
                    {
                        "entity_id": "case-b",
                        "username": "flw_001",
                        "opportunity_id": 10017,
                        "reg_date": "2026-05-09",
                        "birth_weight_g": 1500,
                        "last_weight_g": 2100,
                        "total_visits": 3,
                    },
                ]
            },
            "visits": {"rows": [{}, {}, {}]},
        }

    def _build(self):
        cases = ks.case_rows(self._pipelines(), LLO_MAP)
        return ks.build(
            rows=self._rows(),
            measures=[C16],
            llo_map=LLO_MAP,
            credible_sets={},
            cases=cases,
            meta={"cases": len(cases)},
        )

    def test_shape_matches_buildFrozen(self):
        snap = self._build()
        for key in (
            "schema",
            "generated_at",
            "cMeasures",
            "mortalityCredible",
            "programInd",
            "byLLO",
            "byOpp",
            "byFLW",
            "monthly",
            "monthlyByScope",
            "nSeries",
            "meta",
            "cases",
        ):
            assert key in snap, f"buildFrozen emits {key} and the port must too"

    def test_flw_carries_its_cases_so_the_drill_survives_freezing(self):
        """buildFrozen stores `rows: []`; the LIVE render still holds the case rows,
        a frozen run does not. Copying that shape dead-ends the drill at the FLW."""
        snap = self._build()
        flw = snap["byFLW"][0]
        assert len(flw["rows"]) == 2
        assert {c["entity_id"] for c in flw["rows"]} == {"case-a", "case-b"}

    def test_cases_carry_their_llo_for_the_handoff(self):
        snap = self._build()
        assert all(c["llo"] == "GHI" for c in snap["cases"])

    def test_weight_series_is_not_carried(self):
        """The 5 MB cap is real; the longitudinal workflow fetches the series live
        for the one case a user opened."""
        snap = self._build()
        assert all("weights" not in c for c in snap["cases"])

    def test_llo_nests_its_opportunities_and_counts_bands(self):
        snap = self._build()
        ghi = next(l for l in snap["byLLO"] if l["llo"] == "GHI")
        assert {o["opp"] for o in ghi["opps"]} == {10017, 10020}
        assert ghi["ind"]["C16"]["band"] == "green"

    def test_an_opportunity_that_never_asks_reads_notinapp(self):
        snap = self._build()
        blank = next(o for o in snap["byOpp"] if o["opp"] == 10020)
        assert blank["ind"]["C16"]["band"] in ("notinapp", "unrecorded")
        assert blank["ind"]["C16"]["value"] is None
