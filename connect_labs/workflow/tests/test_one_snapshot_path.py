"""There is ONE way to build a run's snapshot, and both surfaces use it.

`views.complete_run_api` and the MCP tool `workflow_save_snapshot` each carried
their own copy of the build step, the second annotated "mirrors the canonical
run-completion endpoint". Mirrors drift, and these did: the MCP path gained
`program_id` / `access_token` in its builder context and the web path did not;
the web path gained `run_id` and the MCP path did not. And the dashboard render
carried a THIRD implementation -- in JavaScript, for live runs -- which is where
every saved-run defect came from.

These tests pin the consolidation structurally. They are about repo code, which
is not dynamic, so they are fair to pin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from connect_labs.workflow.snapshot_runtime import SnapshotBuildError, build_snapshot_for_run

REPO = Path(__file__).resolve().parents[2]


class _Definition:
    def __init__(self, data):
        self.data = data
        self.template_type = data.get("config", {}).get("templateType")
        self.opportunity_id = 10
        self.opportunity_ids = []
        self.pipeline_sources = [{"alias": "a", "pipeline_id": 1}]
        self.name = "t"
        self.registry_source = None


class _Run:
    id = 7
    opportunity_id = 10
    period_start = None
    period_end = None
    is_completed = False
    snapshot = None

    def __init__(self, definition_id=1, state=None):
        self.data = {"definition_id": definition_id, "state": state or {"x": 1}}


class _DAO:
    access_token = "tok"
    program_id = None

    def __init__(self, definition, pipelines=None, miss=None):
        self._definition = definition
        self._pipelines = pipelines or {"a": {"rows": [{"k": 1}]}}
        self._miss = miss
        self.calls = []

    def get_definition(self, definition_id):
        return self._definition

    def get_cached_pipeline_data(
        self, definition_id, opportunity_id, aliases=None, period_start=None, period_end=None
    ):
        self.calls.append(("cached", aliases))
        if self._miss:
            raise self._miss
        return self._pipelines

    def get_workers(self, oid):
        return [{"username": "w1"}]


@pytest.fixture
def declarative_definition():
    return _Definition({"snapshot_inputs": {"pipelines": ["a"], "state_keys": ["x"], "workers": True}})


class TestBuildSnapshotForRun:
    def test_builds_the_declarative_payload(self, declarative_definition):
        built = build_snapshot_for_run(_DAO(declarative_definition), _Run())
        payload = built["payload"]
        assert payload["state"] == {"x": 1}
        assert payload["pipelines"] == {"a": {"rows": [{"k": 1}]}}
        assert payload["workers"][0]["username"] == "w1"
        assert built["opportunity_id"] == 10
        assert built["opportunity_ids"] == [10]

    def test_reads_only_the_aliases_the_contract_captures(self, declarative_definition):
        dao = _DAO(declarative_definition)
        build_snapshot_for_run(dao, _Run())
        assert dao.calls == [("cached", ["a"])]

    def test_a_cache_miss_is_a_typed_refusal_naming_the_pipeline(self, declarative_definition):
        from connect_labs.workflow.data_access import PipelineCacheMiss

        dao = _DAO(declarative_definition, miss=PipelineCacheMiss("a", 10, "Alpha"))
        with pytest.raises(SnapshotBuildError) as exc:
            build_snapshot_for_run(dao, _Run())
        assert exc.value.code == "cache_miss"
        assert "Alpha" in exc.value.message

    def test_a_definition_with_no_contract_is_a_typed_refusal(self):
        definition = _Definition({"config": {}})
        with pytest.raises(SnapshotBuildError) as exc:
            build_snapshot_for_run(_DAO(definition), _Run())
        assert exc.value.code == "no_contract"
        assert exc.value.contract is not None

    def test_a_missing_definition_is_a_typed_refusal(self):
        class _Gone(_DAO):
            def get_definition(self, definition_id):
                return None

        with pytest.raises(SnapshotBuildError) as exc:
            build_snapshot_for_run(_Gone(None), _Run())
        assert exc.value.code == "definition_not_found"


class TestBothSurfacesUseIt:
    """Neither caller may build by hand again."""

    VIEWS = REPO / "workflow" / "views.py"
    MCP = REPO / "mcp" / "tools" / "workflow_snapshots.py"

    def test_neither_caller_invokes_the_contract_builder_directly(self):
        for path in (self.VIEWS, self.MCP):
            src = path.read_text()
            assert "build_snapshot_for_contract(" not in src, f"{path.name} builds a snapshot by hand again"
            assert "build_snapshot_for_run" in src, f"{path.name} does not use the shared builder"

    def test_the_preview_route_exists(self):
        from connect_labs.workflow import urls

        routes = {str(getattr(p.pattern, "_route", "")) for p in urls.urlpatterns}
        assert "api/run/<int:run_id>/snapshot/preview/" in routes

    def test_the_render_computes_no_indicator_itself(self):
        """The third copy. A live run must render the fetched payload, not a
        JavaScript re-implementation of the grading."""
        src = (REPO / "workflow" / "templates" / "kmc_programme_metrics_render.js").read_text()
        for gone in ("function cEntry(", "function cPooled(", "function monthlyFor(", "function buildSnapshot("):
            assert gone not in src, f"the render still carries {gone.strip('(')} -- a second grader"
        assert "/snapshot/preview/" in src, "the render does not fetch the preview payload"


class TestTheRunsPeriodReachesTheBuilder:
    """A builder that evaluates AS OF a date reads `period_end` from its context.
    Without it, a weekly run saved on Tuesday computed "as of Tuesday" and called
    it last week's figures -- with no error anywhere, because the default is today."""

    def test_period_travels_in_the_builder_context(self, declarative_definition, monkeypatch):
        import datetime as dt

        import connect_labs.workflow.templates as templates

        seen = {}

        def fake_build(contract, **kw):
            seen.update(kw)
            return {"state": {}}

        monkeypatch.setattr(templates, "build_snapshot_for_contract", fake_build)
        run = _Run()
        run.period_start = dt.date(2026, 8, 31)
        run.period_end = dt.date(2026, 9, 6)
        build_snapshot_for_run(_DAO(declarative_definition), run)
        assert seen["period_end"] == dt.date(2026, 9, 6)
        assert seen["period_start"] == dt.date(2026, 8, 31)


class TestAsOfIsADateOrNothing:
    """`as_of` is spliced into SQL as a DATE literal, so only a date may pass."""

    def test_accepts_date_datetime_and_iso_strings(self):
        import datetime as dt

        from connect_labs.workflow.snapshot_builders import as_of_iso

        assert as_of_iso(dt.date(2026, 9, 6)) == "2026-09-06"
        assert as_of_iso(dt.datetime(2026, 9, 6, 23, 59)) == "2026-09-06"
        assert as_of_iso("2026-09-06T00:00:00+00:00") == "2026-09-06"

    def test_nothing_means_today(self):
        from connect_labs.workflow.snapshot_builders import as_of_iso

        assert as_of_iso(None) is None
        assert as_of_iso("") is None

    def test_anything_else_is_refused_not_passed_through(self):
        from connect_labs.workflow.snapshot_builders import as_of_iso

        assert as_of_iso("'; DROP TABLE x; --") is None
        assert as_of_iso("Sept 6") is None


class TestRunHistoryIsAProjection:
    """The history API returns many runs' snapshots at once; a trend needs a few
    hundred bytes of each. The caller names the paths, missing ones come back as
    None so every run has the same shape."""

    def test_dotted_paths_are_picked_out(self):
        from connect_labs.workflow.snapshot_runtime import project_state

        state = {"snapshot": {"programInd": {"C09": {"value": 0.5}}, "meta": {"as_of": "2026-09-06"}}}
        got = project_state(state, ["snapshot.programInd", "snapshot.meta.as_of", "snapshot.cases"])
        assert got == {
            "snapshot.programInd": {"C09": {"value": 0.5}},
            "snapshot.meta.as_of": "2026-09-06",
            "snapshot.cases": None,
        }

    def test_a_missing_or_non_dict_state_is_all_none(self):
        from connect_labs.workflow.snapshot_runtime import project_state

        assert project_state(None, ["a.b"]) == {"a.b": None}
        assert project_state({"a": 3}, ["a.b"]) == {"a.b": None}

    def test_the_history_route_exists(self):
        from connect_labs.workflow import urls

        routes = {str(getattr(p.pattern, "_route", "")) for p in urls.urlpatterns}
        assert "api/<int:definition_id>/runs/history/" in routes

    def test_the_render_draws_its_trend_from_saved_runs_not_cohort_lines(self):
        src = (REPO / "workflow" / "templates" / "kmc_programme_metrics_render.js").read_text()
        assert "/runs/history/" in src, "the trend does not read the saved-run history"
        assert "P.weekly" in src, "the activity chart does not read the builder's weekly series"


class TestTheCaseIndexIsAsOfToo:
    """The evaluation is cut at as_of; the pipeline cache behind the case index and
    the visit rows is all-time. A run for a past week must not carry today's counts
    in its banner or babies registered after its date in its drill."""

    def test_rows_after_the_date_are_dropped_and_undated_rows_kept(self):
        from connect_labs.workflow.snapshot_builders import cut_as_of

        rows = [
            {"reg_date": "2026-07-01"},
            {"reg_date": "2026-07-06"},
            {"first_visit_date": "2026-07-05T10:00:00"},
            {"weights": []},
        ]
        got = cut_as_of(rows, ("reg_date", "first_visit_date"), "2026-07-05")
        assert got == [{"reg_date": "2026-07-01"}, {"first_visit_date": "2026-07-05T10:00:00"}, {"weights": []}]

    def test_no_as_of_means_no_cut(self):
        from connect_labs.workflow.snapshot_builders import cut_as_of

        rows = [{"reg_date": "2999-01-01"}]
        assert cut_as_of(rows, ("reg_date",), None) == rows
