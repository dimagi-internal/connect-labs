"""Grading and payload tests for the framework's semantic-snapshot builder.

The risk this file exists for is unchanged: a snapshot that is subtly different from
the live view is worse than no snapshot, because it is a funder-facing dashboard
whose colours disagree with the one it was captured from.

What changed is where the defence lives. These tests were written against
`workflow/templates/kmc_snapshot.py`, a hand-port of the KMC render's `cEntry()`
into a per-template file, and they pinned each ported branch because a hand-port is
kept in step by review. That file is gone: both sides now read the SAME registry
through `measure_catalog`, so there is one copy of every threshold. The branch tests
are ported verbatim in substance (each still pins a branch that was got wrong at
least once) and the ones below them prove the generalisation actually happened —
that nothing here knows what KMC is.
"""

from __future__ import annotations

import pytest

from connect_labs.semantic import snapshot as snap

# A measure as `measure_catalog` now serves it: the gate inputs and the coverage
# floor are registry DATA, where they used to be `gates.IND_INPUTS` and a
# `_C16_MIN_COVERAGE = 0.45` literal guarded by `if ind_id == "C16"`.
C16 = {
    "id": "c16",
    "indicator": "C16",
    "unit": "%",
    "direction": "higher",
    "bands": [50, 30],
    "min_denominator": 25,
    "inputs": ["days_discharge_to_reg"],
    "min_input_coverage": 0.45,
    "coverage_denominator": "c02",
}
C14 = {
    "id": "c14",
    "indicator": "C14",
    "unit": "%",
    "direction": "mid2",
    "bands": [[2, 12], [1, 20]],
    "inputs": [],
}
LLO_MAP = {10016: "EHA", 10017: "GHI", 10020: "GHI"}
# No availability facts declared: every gate fails open to "asks", which is what a
# registry that says nothing about app coverage should do.
DEPLOY: dict = {"llo_map": LLO_MAP, "settings": {}, "app_asks": {}, "asks_as": {}}

SPEC = {
    "builder": "semantic_snapshot",
    "series": "C",
    "scopes": ["programme", "llo", "opportunity", "flw"],
    "case_index": {
        "pipeline": "children",
        "fields": [
            "entity_id",
            "username",
            "opportunity_id",
            "reg_date",
            "birth_weight_g",
            "last_weight_g",
            "total_visits",
        ],
    },
    "visits_pipeline": "visits",
    "credibility": {"C14": "mortality_recording_credible"},
    "min_denominator_default": 25,
}


def grade(measure, row, credibility=None, deployment=None):
    return snap.grade(
        measure,
        row,
        llo_map=LLO_MAP,
        deployment=deployment or DEPLOY,
        credibility=credibility or {},
        min_denominator_default=25,
    )


class TestBandOf:
    def test_higher_direction(self):
        assert snap.band_of("higher", [50, 30], 60) == "green"
        assert snap.band_of("higher", [50, 30], 40) == "yellow"
        assert snap.band_of("higher", [50, 30], 10) == "red"

    def test_lower_direction(self):
        assert snap.band_of("lower", [15, 30], 10) == "green"
        assert snap.band_of("lower", [15, 30], 20) == "yellow"
        assert snap.band_of("lower", [15, 30], 40) == "red"

    def test_two_sided_is_red_beyond_either_end(self):
        """The single outcome a two-sided mortality band exists to prevent: under
        ~2% means deaths are not recorded, not that babies survived."""
        assert snap.band_of("mid2", [[2, 12], [1, 20]], 6) == "green"
        assert snap.band_of("mid2", [[2, 12], [1, 20]], 15) == "yellow"
        assert snap.band_of("mid2", [[2, 12], [1, 20]], 0.5) == "red"
        assert snap.band_of("mid2", [[2, 12], [1, 20]], 30) == "red"

    def test_one_dimensional_bands_on_mid2_are_unbanded_not_green(self):
        assert snap.band_of("mid2", [2, 12], 6) == "unbanded"

    def test_no_bands_and_no_value(self):
        assert snap.band_of("higher", None, 5) == "unbanded"
        assert snap.band_of("higher", [50, 30], None) == "nodata"
        assert snap.band_of("higher", [50, 30], "abc") == "nodata"


class TestGrade:
    def _row(self, **kw):
        row = {"scope": "llo", "llo": "GHI", "anyrec_days_discharge_to_reg": 1}
        row.update(kw)
        return row

    def test_no_row_is_nodata(self):
        assert grade(C16, None)["band"] == "nodata"

    def test_value_is_scaled_for_percent_units(self):
        e = grade(C16, self._row(c16=94.5, c16_denominator=382, c02=810))
        assert e["band"] == "green"
        assert abs(e["value"] - 0.945) < 1e-9
        assert e["n"] == 382

    def test_below_min_denominator_reads_insufficient_not_a_number(self):
        e = grade(C16, self._row(c16=94.5, c16_denominator=10, c02=100))
        assert e["band"] == "insufficient"
        assert e["value"] is None

    def test_min_denominator_falls_back_to_the_spec_default(self):
        """A measure that declares none must still be gated, or a 1-case scope
        publishes a confident 100%."""
        loose = {**C16, "min_denominator": None}
        assert grade(loose, self._row(c16=100.0, c16_denominator=3, c02=3))["band"] == "insufficient"

    def test_not_credible_marks_the_figure_it_does_not_erase_it(self):
        """Blanking hides the under-recording — pooling every LLO reads lower than
        the credible recorders alone."""
        row = {"scope": "llo", "llo": "GHI", "c14": 2.4, "c14_denominator": 373}
        e = grade(C14, row, credibility={"C14": {"BERI": True}})
        assert e["band"] == "notcredible"
        assert e["value"] is not None

    def test_thin_coverage_is_footnoted(self):
        """Neal's spec item 8: <45% of started cases carrying a discharge date is a
        self-selected minority, and 96.5% off 20% coverage is the failure case."""
        e = grade(C16, self._row(c16=96.5, c16_denominator=954, c02=4767))
        assert e["thinDenominator"] is True
        assert e["coverage"] < 0.45

    def test_full_coverage_is_not_footnoted(self):
        e = grade(C16, self._row(c16=97.6, c16_denominator=342, c02=611))
        assert "thinDenominator" not in e

    def test_the_coverage_rule_is_registry_data_not_an_indicator_id(self):
        """This was `if ind_id == "C16"` against a 0.45 literal, so a second
        indicator needing the same footnote meant editing framework code."""
        other = {**C16, "id": "c17", "indicator": "C17", "min_input_coverage": 0.9}
        row = self._row(c17=80.0, c17_denominator=100, c02=200)
        e = grade(other, row)
        assert e["thinDenominator"] is True
        # And an indicator that declares no floor is never footnoted, whatever its id.
        plain = {k: v for k, v in other.items() if k != "min_input_coverage"}
        assert "thinDenominator" not in grade(plain, row)

    def test_a_measure_declaring_no_coverage_denominator_is_not_footnoted(self):
        """Both halves of the rule are required — a floor with nothing to divide by
        must not silently footnote everything."""
        floor_only = {k: v for k, v in C16.items() if k != "coverage_denominator"}
        e = grade(floor_only, self._row(c16=96.5, c16_denominator=954, c02=4767))
        assert "thinDenominator" not in e


class TestGatesReadTheRegistryNotTheRepo:
    """`app_asks` / `asks_as` were static dicts in `semantic/gates.py`.

    So a workflow bound to a registry RECORD read its bands from the record and its
    availability gates from the repo, and nothing could notice them disagreeing.
    They are deployment facts now, which is why these tests pass them IN.
    """

    def _row(self, opp, **kw):
        row = {"scope": "opportunity", "opportunity_id": opp, "anyrec_days_discharge_to_reg": 1}
        row.update(kw)
        return row

    def test_an_app_that_does_not_ask_reads_notinapp(self):
        deploy = {**DEPLOY, "app_asks": {"10020": {"days_discharge_to_reg": False}}}
        e = grade(C16, self._row(10020, c16=94.5, c16_denominator=382, c02=400), deployment=deploy)
        assert e["band"] == "notinapp"
        assert e["value"] is None

    def test_an_app_that_asks_is_graded_normally(self):
        deploy = {**DEPLOY, "app_asks": {"10017": {"days_discharge_to_reg": True}}}
        e = grade(C16, self._row(10017, c16=94.5, c16_denominator=382, c02=400), deployment=deploy)
        assert e["band"] == "green"

    def test_asks_and_recorded_nothing_is_a_different_fact_from_never_asking(self):
        deploy = {**DEPLOY, "app_asks": {"10017": {"days_discharge_to_reg": True}}}
        row = self._row(10017, c16=None, c16_denominator=0, c02=400)
        row["anyrec_days_discharge_to_reg"] = 0
        assert grade(C16, row, deployment=deploy)["band"] == "unrecorded"

    def test_yaml_integer_keys_still_match(self):
        """YAML reads `10020:` as an int while the gate keys on str, so a map moved
        out of Python would match nothing and every gate would fail open."""
        from connect_labs.semantic.runtime import normalise_deployment_facts

        facts = normalise_deployment_facts({"app_asks": {10020: {"days_discharge_to_reg": False}}})
        assert set(facts["app_asks"]) == {"10020"}
        e = grade(C16, self._row(10020, c16=94.5, c16_denominator=382, c02=400), deployment={**DEPLOY, **facts})
        assert e["band"] == "notinapp"

    def test_alias_indirection_survives_the_move(self):
        """`referred` is stored as `referral_visits`; without `asks_as` the gate
        looks up a column that is not there and fails open."""
        measure = {**C16, "id": "c19", "indicator": "C19", "inputs": ["referred"]}
        deploy = {
            **DEPLOY,
            "app_asks": {"10020": {"referral_visits": False}},
            "asks_as": {"referred": "referral_visits"},
        }
        row = self._row(10020, c19=50.0, c19_denominator=100, c02=100)
        row["anyrec_referred"] = 1
        assert grade(measure, row, deployment=deploy)["band"] == "notinapp"

    def test_unknown_opportunity_fails_open(self):
        deploy = {**DEPLOY, "app_asks": {"10020": {"days_discharge_to_reg": False}}}
        e = grade(C16, self._row(99999, c16=94.5, c16_denominator=382, c02=400), deployment=deploy)
        assert e["band"] == "green"

    def test_any_opportunity_in_scope_asking_is_enough(self):
        """An LLO spanning a asking and a non-asking app still has the number."""
        deploy = {
            **DEPLOY,
            "app_asks": {"10017": {"days_discharge_to_reg": True}, "10020": {"days_discharge_to_reg": False}},
        }
        row = {"scope": "llo", "llo": "GHI", "anyrec_days_discharge_to_reg": 1, "c16": 94.5, "c16_denominator": 382}
        assert grade(C16, row, deployment=deploy)["band"] == "green"


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
                        "weights": [1700, 2000, 2400],
                    },
                    {
                        "entity_id": "case-b",
                        "username": "flw_001",
                        "opportunity_id": 10017,
                        "reg_date": "2026-05-09",
                        "birth_weight_g": 1500,
                        "last_weight_g": 2100,
                        "total_visits": 3,
                        "weights": [1500, 1800, 2100],
                    },
                ]
            },
            "visits": {"rows": [{}, {}, {}]},
        }

    def _build(self, **kw):
        cases = snap.case_rows(self._pipelines(), SPEC, LLO_MAP)
        args = {
            "spec": SPEC,
            "rows": self._rows(),
            "measures": [C16],
            "deployment": DEPLOY,
            "cases": cases,
            "meta": {"cases": len(cases)},
        }
        args.update(kw)
        return snap.build(**args)

    def test_payload_shape(self):
        payload = self._build()
        for key in (
            "schema",
            "generated_at",
            "cMeasures",
            "credibility",
            "deployment",
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
            assert key in payload, f"the builder must emit {key}"

    def test_flw_reaches_its_cases_so_the_drill_survives_saving(self):
        """A LIVE dashboard still holds the case rows and reads only `.length` off
        the snapshot; a saved run does not, so carrying no cases dead-ends the drill
        at the worker. Resolved the way the render resolves them, so this stays a
        test of the drill rather than of the encoding."""
        payload = self._build()
        flw = payload["byFLW"][0]
        assert len(flw["rows"]) == 2
        resolved = [payload["cases"][i] for i in flw["rows"]]
        assert {c["entity_id"] for c in resolved} == {"case-a", "case-b"}

    def test_cases_carry_their_llo_for_the_handoff(self):
        assert all(c["llo"] == "GHI" for c in self._build()["cases"])

    def test_weight_series_is_not_carried(self):
        """The 5 MB cap is real; the longitudinal workflow fetches the series live
        for the one case a user opened. The spec's field list is what excludes it."""
        assert all("weights" not in c for c in self._build()["cases"])

    def test_llo_nests_its_opportunities_and_counts_bands(self):
        payload = self._build()
        ghi = next(x for x in payload["byLLO"] if x["llo"] == "GHI")
        assert {o["opp"] for o in ghi["opps"]} == {10017, 10020}
        assert ghi["ind"]["C16"]["band"] == "green"

    def test_an_opportunity_that_recorded_nothing_reads_a_reason_not_a_zero(self):
        payload = self._build()
        blank = next(o for o in payload["byOpp"] if o["opp"] == 10020)
        assert blank["ind"]["C16"]["band"] in ("notinapp", "unrecorded")
        assert blank["ind"]["C16"]["value"] is None

    def test_scopes_absent_from_the_evaluation_do_not_break_the_payload(self):
        """The spec may name month scopes a given run has no rows for."""
        payload = self._build()
        assert payload["monthly"] == []
        assert payload["monthlyByScope"]["all"] == []


class TestByFLWMatchesWhatTheRenderReads:
    """Three fields were missing from the server-side byFLW and each broke something
    silently on a saved run: `key` (the render's selection identity AND React key,
    `f.key === selFLW`), `flw` (what the worker table displays and what an audit
    payload sends — the server emitted only `username`), and `reds`/`yellows` (the
    badge counts, which by_llo computed and byFLW did not)."""

    def _payload(self):
        rows = [
            {"scope": "programme", "n_cases": 3},
            {
                "scope": "flw",
                "opportunity_id": 10017,
                "username": "flw_001",
                "n_cases": 2,
                "c16": 10.0,
                "c16_denominator": 30,
                "c02": 30,
                "anyrec_days_discharge_to_reg": 1,
            },
            {
                "scope": "flw",
                "opportunity_id": 10020,
                "username": "flw_009",
                "n_cases": 1,
                "c16": 90.0,
                "c16_denominator": 30,
                "c02": 30,
                "anyrec_days_discharge_to_reg": 1,
            },
        ]
        return snap.build(spec=SPEC, rows=rows, measures=[C16], deployment=DEPLOY, cases=[])

    def test_every_field_the_render_reads_is_present(self):
        entry = self._payload()["byFLW"][0]
        for field in ("key", "opp", "flw", "llo", "rows", "ind", "reds", "yellows", "n"):
            assert field in entry, f"the render reads byFLW[].{field}"

    def test_key_format_is_the_renders_own(self):
        # render: var k = r.opp + FLW_SEP + (r.flw || '(unassigned)');  FLW_SEP = '::'
        by_key = {f["key"]: f for f in self._payload()["byFLW"]}
        assert "10017::flw_001" in by_key
        assert by_key["10017::flw_001"]["flw"] == "flw_001"

    def test_unassigned_worker_gets_the_renders_placeholder(self):
        payload = snap.build(
            spec=SPEC,
            rows=[{"scope": "programme", "n_cases": 1}, {"scope": "flw", "opportunity_id": 10017, "username": None}],
            measures=[C16],
            deployment=DEPLOY,
            cases=[],
        )
        assert payload["byFLW"][0]["key"] == "10017::(unassigned)"

    def test_badges_are_counted_not_dropped(self):
        # c16 = 10.0 against bands [50, 30] on `higher` is red; 90.0 is green.
        by_key = {f["key"]: f for f in self._payload()["byFLW"]}
        assert by_key["10017::flw_001"]["reds"] == 1
        assert by_key["10020::flw_009"]["reds"] == 0


class TestCasesAreStoredOnce:
    """`byFLW[].rows` holds POSITIONS into `cases`, not copies of the records.

    Storing them in both places stored every case twice: measured on the live
    9,011-case cohort, 3.07 MB each way — 6.14 MB of a 7.0 MB payload against a 5 MB
    cap, so `workflow_save_snapshot` refused the run outright.
    """

    def _build(self, children):
        cases = snap.case_rows({"children": {"rows": children}}, SPEC, LLO_MAP)
        rows: list[dict] = [{"scope": "programme", "n_cases": len(children)}]
        seen: list[tuple] = []
        for c in children:
            key = (c["opportunity_id"], c["username"])
            if key not in seen:
                seen.append(key)
        for oid, user in seen:
            rows.append({"scope": "flw", "opportunity_id": oid, "username": user, "n_cases": 1})
        return snap.build(spec=SPEC, rows=rows, measures=[C16], deployment=DEPLOY, cases=cases)

    def test_rows_are_integer_positions_that_resolve(self):
        payload = self._build(
            [
                {"entity_id": "c1", "username": "flw_001", "opportunity_id": 10017},
                {"entity_id": "c2", "username": "flw_001", "opportunity_id": 10017},
                {"entity_id": "c3", "username": "flw_009", "opportunity_id": 10020},
            ]
        )
        by_key = {f["key"]: f for f in payload["byFLW"]}
        rows = by_key["10017::flw_001"]["rows"]
        assert all(isinstance(i, int) for i in rows), rows
        assert [payload["cases"][i]["entity_id"] for i in rows] == ["c1", "c2"]
        assert by_key["10020::flw_009"]["rows"] == [2]

    def test_payload_does_not_carry_a_second_copy_of_the_cases(self):
        import json

        payload = self._build(
            [
                {
                    "entity_id": f"c{i}",
                    "username": "flw_001",
                    "opportunity_id": 10017,
                    "reg_date": "2026-05-21",
                    "birth_weight_g": 1700,
                    "last_weight_g": 2400,
                    "total_visits": 5,
                }
                for i in range(200)
            ]
        )
        cases_bytes = len(json.dumps(payload["cases"], separators=(",", ":")))
        flw_bytes = len(json.dumps(payload["byFLW"], separators=(",", ":")))
        assert (
            flw_bytes < cases_bytes / 4
        ), f"byFLW is {flw_bytes} B against {cases_bytes} B of cases — the records look duplicated"

    def test_entity_ids_reused_across_opportunities_do_not_collide(self):
        # 751 of 8,173 entity ids in the live synthetic cohort appear under more than
        # one opportunity, which is why the reference is a position and not an id.
        payload = self._build(
            [
                {"entity_id": "shared", "username": "flw_001", "opportunity_id": 10017},
                {"entity_id": "shared", "username": "flw_009", "opportunity_id": 10020},
            ]
        )
        assert len(payload["cases"]) == 2, "an id-keyed index would have collapsed these to one"
        by_key = {f["key"]: f for f in payload["byFLW"]}
        assert by_key["10017::flw_001"]["rows"] == [0]
        assert by_key["10020::flw_009"]["rows"] == [1]


class TestCredibilityTravelsAsData:
    def test_spec_maps_indicators_onto_registry_settings(self):
        resolved = snap.resolve_credibility(
            {"credibility": {"C14": "mortality_recording_credible"}},
            {"mortality_recording_credible": {"PIPN": True, "GHI": False}},
        )
        assert resolved == {"C14": {"PIPN": True, "GHI": False}}

    def test_a_missing_settings_table_resolves_empty_rather_than_raising(self):
        assert snap.resolve_credibility({"credibility": {"C14": "nope"}}, {}) == {"C14": {}}

    def test_payload_carries_every_named_table_not_just_mortality(self):
        """The payload key was `mortalityCredible`, which could only ever carry C14 —
        so C18's and C22's tables were graded with and then not published."""
        spec = {**SPEC, "credibility": {"C14": "m", "C18": "c", "C22": "c"}}
        payload = snap.build(
            spec=spec,
            rows=[{"scope": "programme", "n_cases": 1}],
            measures=[C16],
            deployment={**DEPLOY, "settings": {"m": {"PIPN": True}, "c": {"GHI": False}}},
            cases=[],
        )
        assert set(payload["credibility"]) == {"C14", "C18", "C22"}
        assert payload["credibility"]["C18"] == {"GHI": False}


class TestDeclaredSchemaMatchesThePayload:
    """`snapshot_schema` is the contract `workflow_get` publishes to callers.

    It is not documentation: an agent reads it to know what a saved run will contain
    before deciding whether to save one. Extending the payload and leaving the
    manifest behind is how it stops being true.
    """

    def _built_keys(self) -> set[str]:
        from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS

        cases = snap.case_rows(
            {"children": {"rows": [{"entity_id": "c1", "username": "flw_001", "opportunity_id": 10017}]}},
            SNAPSHOT_INPUTS,
            LLO_MAP,
        )
        payload = snap.build(
            spec=SNAPSHOT_INPUTS,
            rows=[{"scope": "programme", "n_cases": 1}],
            measures=[C16],
            deployment=DEPLOY,
            cases=cases,
        )
        return set(payload)

    def _declared_keys(self) -> set[str]:
        from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_SCHEMA

        prefix = "state.snapshot."
        return {k[len(prefix) :] for k in SNAPSHOT_SCHEMA["keys"] if k.startswith(prefix)}

    def test_every_built_key_is_declared(self):
        undeclared = self._built_keys() - self._declared_keys()
        assert not undeclared, f"snapshot emits {sorted(undeclared)} that snapshot_schema does not declare"

    def test_every_declared_key_is_built(self):
        missing = self._declared_keys() - self._built_keys()
        assert not missing, f"snapshot_schema promises {sorted(missing)} that the snapshot does not contain"


class TestTheTemplateShipsNoSnapshotCode:
    """The point of the whole change: the KMC snapshot is DECLARED, not coded."""

    def test_the_template_declares_a_builder(self):
        from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS

        assert SNAPSHOT_INPUTS["builder"] == "semantic_snapshot"

    def test_the_template_module_has_no_build_snapshot_hook(self):
        """A hook needs a DEPLOY to change; the loader auto-registers any
        module-level `build_snapshot`, so its absence is what keeps this declarative."""
        from connect_labs.workflow.templates import kmc_programme_metrics as tpl

        assert not hasattr(tpl, "build_snapshot")

    def test_the_hand_port_is_gone(self):
        with pytest.raises(ImportError):
            from connect_labs.workflow.templates import kmc_snapshot  # noqa: F401

    def test_no_indicator_id_is_hardcoded_in_the_builder(self):
        """`if ind_id == "C16"` and a 0.45 literal were programme rules living in
        framework code. Nothing here should name an indicator."""
        import re
        from pathlib import Path

        src = Path(snap.__file__).read_text()
        # Strip comments and docstrings: the prose explains what moved and names ids.
        code = re.sub(r'"""(?:.|\n)*?"""', "", src)
        code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
        assert not re.findall(r"\bC\d{2}\b", code), re.findall(r"\bC\d{2}\b", code)


class TestPooledOverCredibleRecorders:
    """The headline figure for a credibility-gated indicator, and why the builder
    must compute it rather than the render.

    The programme row pools EVERY LLO, so on mortality it reads lower than reality
    — non-recorders contribute denominator without deaths. The card therefore shows
    the credible-recorder pool. A saved run cannot rebuild that from its graded
    cells: a row banded `insufficient` still contributes its numerator and
    denominator to the pool while storing `value: null`, so pooling from stored
    values silently drops exactly those rows and moves the number.

    The shape matters as much as the value. The render's memo returns
    `{ind, llos, of}` and its consumers read `.llos.length`; the predecessor hook
    stored the raw credibility table there instead, which has no `llos` — a
    TypeError on the rendered page, and invisible to every test that stopped at the
    stored payload.
    """

    C14 = {
        "id": "c14",
        "indicator": "C14",
        "unit": "%",
        "direction": "mid2",
        "bands": [[2, 12], [1, 20]],
        "inputs": [],
        "min_denominator": 25,
    }
    SPEC = {**SPEC, "credibility": {"C14": "mortality_recording_credible"}}

    def _rows(self):
        return [
            {"scope": "programme", "n_cases": 4},
            # PIPN and EHA record deaths credibly; GHI does not.
            {"scope": "llo", "llo": "PIPN", "n_cases": 2, "c14": 5.0, "c14_numerator": 5, "c14_denominator": 100},
            {"scope": "llo", "llo": "EHA", "n_cases": 1, "c14": 3.0, "c14_numerator": 3, "c14_denominator": 100},
            {"scope": "llo", "llo": "GHI", "n_cases": 1, "c14": 0.0, "c14_numerator": 0, "c14_denominator": 400},
        ]

    def _build(self, settings):
        return snap.build(
            spec=self.SPEC,
            rows=self._rows(),
            measures=[self.C14],
            deployment={**DEPLOY, "settings": settings},
            cases=[],
        )

    def test_it_pools_only_the_credible_recorders(self):
        payload = self._build({"mortality_recording_credible": {"PIPN": True, "EHA": True, "GHI": False}})
        block = payload["pooledOverCredible"]["C14"]
        assert sorted(block["llos"]) == ["EHA", "PIPN"]
        assert block["of"] == 3
        # (5 + 3) / (100 + 100) = 4.0%, not (5+3+0)/(100+100+400) = 1.33%
        assert abs(block["ind"]["value"] - 0.04) < 1e-9
        assert block["ind"]["n"] == 200

    def test_the_shape_is_the_one_the_render_reads(self):
        payload = self._build({"mortality_recording_credible": {"PIPN": True}})
        block = payload["pooledOverCredible"]["C14"]
        assert set(block) == {"ind", "llos", "of"}, "the render's memo returns {ind, llos, of}"
        assert isinstance(block["llos"], list), "consumers read .llos.length"

    def test_it_sums_rather_than_averaging_the_rates(self):
        """A mean would weight a 100-case LLO like a 10,000-case one."""
        rows = [
            {"scope": "programme", "n_cases": 2},
            {"scope": "llo", "llo": "A", "c14": 10.0, "c14_numerator": 10, "c14_denominator": 100},
            {"scope": "llo", "llo": "B", "c14": 1.0, "c14_numerator": 100, "c14_denominator": 10000},
        ]
        payload = snap.build(
            spec=self.SPEC,
            rows=rows,
            measures=[self.C14],
            deployment={**DEPLOY, "settings": {"mortality_recording_credible": {"A": True, "B": True}}},
            cases=[],
        )
        pooled = payload["pooledOverCredible"]["C14"]["ind"]["value"]
        assert abs(pooled - 110 / 10100) < 1e-9, "pooled must be sum/sum, not the mean of 10% and 1%"

    def test_a_row_below_its_min_denominator_still_contributes(self):
        """The reason this cannot be rebuilt from graded cells: such a row stores no
        value of its own, but its numerator and denominator belong in the pool."""
        rows = [
            {"scope": "programme", "n_cases": 2},
            {"scope": "llo", "llo": "A", "c14": 5.0, "c14_numerator": 5, "c14_denominator": 100},
            {"scope": "llo", "llo": "B", "c14": 50.0, "c14_numerator": 5, "c14_denominator": 10},
        ]
        settings = {"mortality_recording_credible": {"A": True, "B": True}}
        payload = snap.build(
            spec=self.SPEC, rows=rows, measures=[self.C14], deployment={**DEPLOY, "settings": settings}, cases=[]
        )
        by_llo = {x["llo"]: x for x in payload["byLLO"]}
        assert by_llo["B"]["ind"]["C14"]["band"] == "insufficient"
        assert by_llo["B"]["ind"]["C14"]["value"] is None, "so it cannot be pooled from stored values"
        assert payload["pooledOverCredible"]["C14"]["ind"]["n"] == 110, "yet it belongs in the denominator"

    def test_an_absent_table_pools_everything_rather_than_withholding_it(self):
        """Matches the render's `credible === null` branch: no basis to gate is not
        a reason to blank every number."""
        payload = self._build({})
        assert sorted(payload["pooledOverCredible"]["C14"]["llos"]) == ["EHA", "GHI", "PIPN"]

    def test_indicators_the_spec_does_not_gate_get_no_block(self):
        payload = snap.build(
            spec={**SPEC, "credibility": {}},
            rows=self._rows(),
            measures=[self.C14],
            deployment=DEPLOY,
            cases=[],
        )
        assert payload["pooledOverCredible"] == {}


class TestMonthlyTrendPoints:
    """Each trend point carries what the render's trend tab reads.

    The tab draws "babies started" (a cohort-month measure, from the semantic rows)
    against "visits" (activity that HAPPENED that month, from the visit rows), plus
    the credible-recorder pool for gated indicators. The first two are different
    groupings on purpose, and the second is only derivable in the builder. A point
    that carried only `{month, ind, n}` drew every bar as NaN and every line as
    "no month has enough data to score" on a run whose 17 months were all present.
    """

    C14 = {
        "id": "c14",
        "indicator": "C14",
        "unit": "%",
        "direction": "mid2",
        "bands": [[2, 12], [1, 20]],
        "inputs": [],
        "min_denominator": 25,
    }
    SPEC = {**SPEC, "credibility": {"C14": "mortality_recording_credible"}}
    LLO_MAP = {10017: "GHI", 10016: "EHA"}
    DEPLOY = {
        "llo_map": LLO_MAP,
        "settings": {"mortality_recording_credible": {"EHA": True, "GHI": False}},
        "app_asks": {},
        "asks_as": {},
    }

    def _rows(self):
        return [
            {"scope": "programme", "n_cases": 5},
            {
                "scope": "month",
                "cohort_month": "2026-01-01",
                "n_cases": 3,
                "c14": 2.0,
                "c14_numerator": 2,
                "c14_denominator": 100,
            },
            {"scope": "month", "cohort_month": "2026-02-01", "n_cases": 2},
            {
                "scope": "llo_month",
                "llo": "EHA",
                "cohort_month": "2026-01-01",
                "n_cases": 2,
                "c14": 5.0,
                "c14_numerator": 5,
                "c14_denominator": 100,
            },
            {
                "scope": "llo_month",
                "llo": "GHI",
                "cohort_month": "2026-01-01",
                "n_cases": 1,
                "c14": 0.0,
                "c14_numerator": 0,
                "c14_denominator": 400,
            },
            {
                "scope": "opportunity_month",
                "opportunity_id": 10016,
                "cohort_month": "2026-01-01",
                "n_cases": 2,
                "c14": 5.0,
                "c14_numerator": 5,
                "c14_denominator": 100,
            },
        ]

    def _visits(self):
        return [
            {"visit_date": "2026-01-03T00:00:00", "opportunity_id": 10016},
            {"visit_date": "2026-01-09T00:00:00", "opportunity_id": 10017},
            {"visit_date": "2026-02-11T00:00:00", "opportunity_id": 10016},
            # A month with visits but no cohort row must still appear.
            {"visit_date": "2026-03-01T00:00:00", "opportunity_id": 10016},
        ]

    def _build(self):
        return snap.build(
            spec=self.SPEC,
            rows=self._rows(),
            measures=[self.C14],
            deployment=self.DEPLOY,
            cases=[],
            visit_rows=self._visits(),
        )

    def test_visits_are_counted_by_the_month_they_happened(self):
        by_month = {m["month"]: m for m in self._build()["monthly"]}
        assert by_month["2026-01"]["visits"] == 2
        assert by_month["2026-02"]["visits"] == 1

    def test_a_visit_only_month_still_appears(self):
        by_month = {m["month"]: m for m in self._build()["monthly"]}
        assert "2026-03" in by_month
        assert by_month["2026-03"]["visits"] == 1
        assert by_month["2026-03"]["n"] == 0

    def test_visits_follow_the_drill(self):
        scoped = self._build()["monthlyByScope"]
        assert {m["month"]: m["visits"] for m in scoped["llo:EHA"]} == {"2026-01": 1, "2026-02": 1, "2026-03": 1}
        assert {m["month"]: m["visits"] for m in scoped["opp:10016"]} == {"2026-01": 1, "2026-02": 1, "2026-03": 1}

    def test_the_pool_spans_only_credible_recorders_undrilled(self):
        jan = {m["month"]: m for m in self._build()["monthly"]}["2026-01"]
        pooled = jan["pooled"]["C14"]
        # EHA is credible (5/100); GHI is not, and its 0/400 must NOT dilute the pool.
        assert pooled["n"] == 100
        assert abs(pooled["value"] - 0.05) < 1e-9

    def test_a_drilled_pool_is_the_scopes_own_row_only_if_credible(self):
        scoped = self._build()["monthlyByScope"]
        eha = {m["month"]: m for m in scoped["llo:EHA"]}["2026-01"]
        ghi = {m["month"]: m for m in scoped["llo:GHI"]}["2026-01"]
        assert eha["pooled"]["C14"]["n"] == 100
        assert ghi["pooled"]["C14"] is None, "a non-credible LLO's drill must not show a mortality figure"

    def test_points_still_carry_the_graded_indicators(self):
        jan = {m["month"]: m for m in self._build()["monthly"]}["2026-01"]
        assert "C14" in jan["ind"]
        assert jan["n"] == 3
