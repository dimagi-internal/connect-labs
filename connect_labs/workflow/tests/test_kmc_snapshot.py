"""Parity tests for the server-side KMC snapshot.

The risk this file exists for: a snapshot that is subtly different from the live view
is worse than no snapshot, because it is a funder-facing dashboard whose colours
disagree with the one it was captured from. `cEntry` in the render is ported branch
for branch in kmc_snapshot.entry(); each branch is pinned here.

("buildFrozen" is the render's older in-template name for its snapshot builder. The
framework's word is snapshot, and this file uses it except where naming the JS
function is the clearest way to say which code is being matched.)
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
        """The render's builder stores `rows: []` — correct there, because a LIVE
        dashboard still holds the case rows. A saved run does not, so copying that
        shape dead-ends the drill at the worker.

        What `rows` HOLDS changed (positions into `cases`, not the records — storing
        both put the payload over the 5 MB cap), but the property under test did not:
        the worker still reaches its own cases. Resolved the way the render resolves
        them, so this stays a test of the drill and not of the encoding.
        """
        snap = self._build()
        flw = snap["byFLW"][0]
        assert len(flw["rows"]) == 2
        resolved = [snap["cases"][i] for i in flw["rows"]]
        assert {c["entity_id"] for c in resolved} == {"case-a", "case-b"}

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
        ghi = next(x for x in snap["byLLO"] if x["llo"] == "GHI")
        assert {o["opp"] for o in ghi["opps"]} == {10017, 10020}
        assert ghi["ind"]["C16"]["band"] == "green"

    def test_an_opportunity_that_never_asks_reads_notinapp(self):
        snap = self._build()
        blank = next(o for o in snap["byOpp"] if o["opp"] == 10020)
        assert blank["ind"]["C16"]["band"] in ("notinapp", "unrecorded")
        assert blank["ind"]["C16"]["value"] is None


class TestDeclaredSchemaMatchesThePayload:
    """`snapshot_schema` is the contract `workflow_get` publishes to callers.

    It is not documentation: an agent reads it to know what a saved run will contain
    before deciding whether to save one. Extending the payload and leaving the
    manifest behind is how it stops being true — which is exactly what happened when
    the case drill was added, and is why this test exists rather than a note.
    """

    def _built_keys(self) -> set[str]:
        cases = ks.case_rows(
            {"children": {"rows": [{"entity_id": "c1", "username": "flw_001", "opportunity_id": 10017}]}},
            LLO_MAP,
        )
        snap = ks.build(
            rows=[{"scope": "programme", "n_cases": 1}],
            measures=[C16],
            llo_map=LLO_MAP,
            credible_sets={},
            cases=cases,
        )
        return set(snap)

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


class TestHookScoping:
    """A snapshot hook builds its own data accessors, and they must carry the run's
    scope.

    On the web path `request` supplies it. On the MCP path there is no request — only
    a token — and a DAO built from a token alone is UNSCOPED, so `get_definition`
    cannot see the very workflow the hook was called for. Measured live: the first
    real execution of this hook failed with `RuntimeError: workflow 5456 could not be
    read` while the same workflow read fine through `workflow_get`.

    This is the third instance of the same defect today (registry-binding validation,
    then this), which is why the scope is assembled ONCE in the hook rather than at
    each accessor.
    """

    def test_the_mcp_path_supplies_program_id_in_context(self):
        """A program-owned run has no opportunity to scope by, so program_id has to
        travel or the hook cannot scope at all."""
        import inspect

        from connect_labs.mcp.tools import workflow_snapshots

        src = inspect.getsource(workflow_snapshots.workflow_save_snapshot)
        assert "program_id=program_id" in src, "build_snapshot_for_contract must receive program_id"
        assert "access_token=wda.access_token" in src

    def test_the_hook_scopes_every_accessor_it_builds(self):
        import inspect

        from connect_labs.workflow.templates import kmc_programme_metrics

        src = inspect.getsource(kmc_programme_metrics.build_snapshot)
        assert 'scope = {"opportunity_id": opportunity_id, "program_id": program_id}' in src
        # every accessor the hook constructs takes the scope
        for dao in ("WorkflowDataAccess(", "PipelineDataAccess(", "SemanticRegistryDataAccess("):
            idx = src.find(dao)
            assert idx != -1, f"{dao} not found"
            assert "**scope" in src[idx : idx + 200], f"{dao} is built without the run's scope"


class TestByFLWMatchesWhatTheRenderReads:
    """The render's own snapshot builder IS the contract for a snapshot's shape.

    Three fields were missing from the server-side byFLW and each broke something
    silently on a saved run, because nothing compared the two builders:

      * `key`   — the render's selection identity AND React key (`f.key === selFLW`).
                  Undefined on every row, so selecting one worker matched all of them.
      * `flw`   — what the worker table renders and what the audit payload sends.
                  The server emitted `username`, so every worker rendered blank.
      * `reds` / `yellows` — the badge counts. by_llo computed them; byFLW did not.

    So this does not hand-list the fields: it reads them out of the render's
    `buildSnapshot()` and asserts the Python builder emits the same set. A field added
    to one side and not the other fails here instead of on a funder's screen.
    """

    RENDER = "kmc_programme_metrics_render.js"

    def _render_src(self) -> str:
        from pathlib import Path

        from connect_labs.workflow.templates import kmc_programme_metrics as tpl

        return (Path(tpl.__file__).parent / self.RENDER).read_text()

    def _keys_declared_by_render(self, group: str) -> set[str]:
        """Field names in the render's `<group>: <group>.map(function (x) { return {...} })`."""
        import re

        src = self._render_src()
        start = src.index(f"      {group}: {group}.map(function (")
        # First `return {` after that, then walk to its matching brace.
        i = src.index("return {", start) + len("return {")
        depth, j = 1, i
        while depth:
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
            j += 1
        body = src[i : j - 1]
        # Top-level `name:` only — skip anything nested inside a sub-object/array.
        keys, depth = set(), 0
        for line in body.splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*):", line)
            if m and depth == 0:
                keys.add(m.group(1))
            depth += line.count("{") + line.count("[") - line.count("}") - line.count("]")
        return keys

    def _snap(self):
        cases = ks.case_rows(
            {
                "children": {
                    "rows": [
                        {"entity_id": "c1", "username": "flw_001", "opportunity_id": 10017, "total_visits": 3},
                        {"entity_id": "c2", "username": "flw_001", "opportunity_id": 10017, "total_visits": 5},
                        {"entity_id": "c3", "username": "flw_009", "opportunity_id": 10020, "total_visits": 2},
                    ]
                }
            },
            LLO_MAP,
        )
        rows = [
            {"scope": "programme", "n_cases": 3},
            {
                "scope": "flw",
                "opportunity_id": 10017,
                "username": "flw_001",
                "n_cases": 2,
                "c16": 10.0,
                "c16_denominator": 30,
                "anyrec_days_discharge_to_reg": 1,
            },
            {
                "scope": "flw",
                "opportunity_id": 10020,
                "username": "flw_009",
                "n_cases": 1,
                "c16": 90.0,
                "c16_denominator": 30,
                "anyrec_days_discharge_to_reg": 1,
            },
        ]
        return ks.build(rows=rows, measures=[C16], llo_map=LLO_MAP, credible_sets={}, cases=cases)

    def test_byflw_emits_every_field_the_render_declares(self):
        declared = self._keys_declared_by_render("byFLW")
        assert declared, "could not parse byFLW out of the render — fix the parser, not the assert"
        emitted = set(self._snap()["byFLW"][0])
        missing = declared - emitted
        assert not missing, f"render reads byFLW[].{sorted(missing)}; the server-side builder omits them"

    def test_byopp_and_byllo_match_too(self):
        snap = ks.build(
            rows=[
                {"scope": "programme", "n_cases": 1},
                {"scope": "opportunity", "opportunity_id": 10017, "n_cases": 1},
                {"scope": "llo", "llo": "GHI", "n_cases": 1},
            ],
            measures=[C16],
            llo_map=LLO_MAP,
            credible_sets={},
            cases=[],
        )
        for group, key in (("byOpp", "byOpp"), ("byLLO", "byLLO")):
            declared = self._keys_declared_by_render(group)
            missing = declared - set(snap[key][0])
            assert not missing, f"render reads {group}[].{sorted(missing)}; the builder omits them"

    def test_key_format_is_the_renders_own(self):
        # render: var k = r.opp + FLW_SEP + (r.flw || '(unassigned)');  FLW_SEP = '::'
        entries = {f["key"]: f for f in self._snap()["byFLW"]}
        assert "10017::flw_001" in entries
        assert entries["10017::flw_001"]["flw"] == "flw_001"

    def test_unassigned_worker_gets_the_renders_placeholder(self):
        snap = ks.build(
            rows=[
                {"scope": "programme", "n_cases": 1},
                {"scope": "flw", "opportunity_id": 10017, "username": None, "n_cases": 1},
            ],
            measures=[C16],
            llo_map=LLO_MAP,
            credible_sets={},
            cases=[],
        )
        assert snap["byFLW"][0]["key"] == "10017::(unassigned)"

    def test_badges_are_counted_not_dropped(self):
        # c16 = 10.0 against bands [50, 30] on `higher` is red; 90.0 is green.
        by_key = {f["key"]: f for f in self._snap()["byFLW"]}
        assert by_key["10017::flw_001"]["reds"] == 1
        assert by_key["10020::flw_009"]["reds"] == 0


class TestCasesAreStoredOnce:
    """`byFLW[].rows` holds POSITIONS into `cases`, not copies of the records.

    Storing the records in both places stored every case twice: measured on the live
    9,011-case cohort, 3.07 MB each way — 6.14 MB of a 7.0 MB payload against a 5 MB
    cap, so `workflow_save_snapshot` refused the run outright. The old code's comment
    claimed "json dedupes on write"; it does not.
    """

    def _cases_and_snap(self, children):
        cases = ks.case_rows({"children": {"rows": children}}, LLO_MAP)
        rows = [{"scope": "programme", "n_cases": len(children)}]
        seen = []
        for c in children:
            k = (c["opportunity_id"], c["username"])
            if k not in seen:
                seen.append(k)
        for oid, user in seen:
            rows.append({"scope": "flw", "opportunity_id": oid, "username": user, "n_cases": 1})
        return cases, ks.build(rows=rows, measures=[C16], llo_map=LLO_MAP, credible_sets={}, cases=cases)

    def test_rows_are_integer_positions_that_resolve(self):
        children = [
            {"entity_id": "c1", "username": "flw_001", "opportunity_id": 10017},
            {"entity_id": "c2", "username": "flw_001", "opportunity_id": 10017},
            {"entity_id": "c3", "username": "flw_009", "opportunity_id": 10020},
        ]
        cases, snap = self._cases_and_snap(children)
        by_key = {f["key"]: f for f in snap["byFLW"]}
        rows = by_key["10017::flw_001"]["rows"]
        assert all(isinstance(i, int) for i in rows), rows
        assert [snap["cases"][i]["entity_id"] for i in rows] == ["c1", "c2"]
        assert by_key["10020::flw_009"]["rows"] == [2]

    def test_payload_does_not_carry_a_second_copy_of_the_cases(self):
        import json

        children = [
            {
                "entity_id": f"c{i}",
                "username": "flw_001",
                "opportunity_id": 10017,
                "dob": "2026-05-21",
                "gender": "Male",
                "last_kmc_status": "flw_program_concluded",
            }
            for i in range(200)
        ]
        cases, snap = self._cases_and_snap(children)
        cases_bytes = len(json.dumps(snap["cases"], separators=(",", ":")))
        flw_bytes = len(json.dumps(snap["byFLW"], separators=(",", ":")))
        # The index must be a small fraction of the records it points at, not a peer.
        assert (
            flw_bytes < cases_bytes / 4
        ), f"byFLW is {flw_bytes} B against {cases_bytes} B of cases — the records look duplicated"

    def test_entity_ids_reused_across_opportunities_do_not_collide(self):
        # 751 of 8,173 entity ids in the live synthetic cohort appear under more than
        # one opportunity, which is why the reference is a position and not an id.
        children = [
            {"entity_id": "shared", "username": "flw_001", "opportunity_id": 10017},
            {"entity_id": "shared", "username": "flw_009", "opportunity_id": 10020},
        ]
        cases, snap = self._cases_and_snap(children)
        assert len(snap["cases"]) == 2, "an id-keyed index would have collapsed these to one"
        by_key = {f["key"]: f for f in snap["byFLW"]}
        assert by_key["10017::flw_001"]["rows"] == [0]
        assert by_key["10020::flw_009"]["rows"] == [1]


class TestSyntheticDisclaimerSurvivesTheSnapshot:
    """The render shows "this run is built on synthetic clones" from
    `snapshot.meta.synthetic`. A live run computes it; a saved run can only know what
    was captured, so omitting the flag published a synthetic cohort with the
    disclaimer silently absent."""

    def _build(self, **kw):
        return ks.build(
            rows=[{"scope": "programme", "n_cases": 1}],
            measures=[C16],
            llo_map=LLO_MAP,
            credible_sets={},
            cases=[],
            meta={"cases": 1},
            **kw,
        )

    def test_flag_is_carried(self):
        assert self._build(synthetic=True)["meta"]["synthetic"] is True
        assert self._build(synthetic=False)["meta"]["synthetic"] is False

    def test_absent_when_not_supplied_rather_than_guessed_false(self):
        # A builder that guessed False would state "this is real programme data".
        assert "synthetic" not in self._build()["meta"]

    def test_render_reads_it_from_meta(self):
        from pathlib import Path

        from connect_labs.workflow.templates import kmc_programme_metrics as tpl

        src = (Path(tpl.__file__).parent / "kmc_programme_metrics_render.js").read_text()
        assert (
            "snapshot.meta && snapshot.meta.synthetic" in src
        ), "the render no longer reads meta.synthetic — this contract moved"
