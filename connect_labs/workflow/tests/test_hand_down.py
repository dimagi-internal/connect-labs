"""Handing a saved programme run down to each opportunity's own report.

The slice is read by the people who run ONE opportunity, who may not see any
other. So the first thing pinned here is what a slice must NOT carry.
"""

import json
from types import SimpleNamespace

import pytest

from connect_labs.benchmarks.models import BenchmarkCohort
from connect_labs.semantic import snapshot as semantic_snapshot
from connect_labs.workflow import hand_down as hd
from connect_labs.workflow.snapshot_builders import wrap_for_runner

MINE, OTHER = 501, 502


def _cell(v, band="green", n=40):
    return {"value": v, "band": band, "n": n}


def payload():
    """A programme run over two opportunities, in the builder's own shape."""
    cases = [
        {"opportunity_id": OTHER, "username": "zed", "entity_id": "c-other-1", "llo": "Other LLO"},
        {"opportunity_id": MINE, "username": "amy", "entity_id": "c-mine-1", "llo": "My LLO"},
        {"opportunity_id": OTHER, "username": "zed", "entity_id": "c-other-2", "llo": "Other LLO"},
        {"opportunity_id": MINE, "username": "amy", "entity_id": "c-mine-2", "llo": "My LLO"},
        {"opportunity_id": MINE, "username": "bea", "entity_id": "c-mine-3", "llo": "My LLO"},
    ]
    mine_ind = {"pct_healthy_growth": _cell(0.7), "mortality": _cell(0.05, "yellow")}
    other_ind = {"pct_healthy_growth": _cell(0.4, "red"), "mortality": _cell(0.09, "red")}
    by_opp = [
        {"opp": OTHER, "llo": "Other LLO", "ind": other_ind, "n": 2, "rows": []},
        {"opp": MINE, "llo": "My LLO", "ind": mine_ind, "n": 3, "rows": []},
    ]
    return {
        "schema": 3,
        "cMeasures": [{"indicator": "pct_healthy_growth", "unit": "%"}],
        "programInd": {"pct_healthy_growth": _cell(0.55)},
        "byOpp": by_opp,
        "byLLO": [
            {"llo": "Other LLO", "ind": other_ind, "opps": [by_opp[0]], "rows": [], "reds": 2, "yellows": 0},
            {"llo": "My LLO", "ind": mine_ind, "opps": [by_opp[1]], "rows": [], "reds": 0, "yellows": 1},
        ],
        "byFLW": [
            {"key": f"{OTHER}::zed", "opp": OTHER, "username": "zed", "ind": other_ind, "rows": [0, 2]},
            {"key": f"{MINE}::amy", "opp": MINE, "username": "amy", "ind": mine_ind, "rows": [1, 3]},
            {"key": f"{MINE}::bea", "opp": MINE, "username": "bea", "ind": mine_ind, "rows": [4]},
        ],
        "cases": cases,
        "weekly": {
            "all": [{"week": "2026-09-07", "visits": 30, "registered": 5}],
            "llo:Other LLO": [{"week": "2026-09-07", "visits": 20, "registered": 3}],
            "llo:My LLO": [{"week": "2026-09-07", "visits": 10, "registered": 2}],
            f"opp:{OTHER}": [{"week": "2026-09-07", "visits": 20, "registered": 3}],
            f"opp:{MINE}": [{"week": "2026-09-07", "visits": 10, "registered": 2}],
        },
        "monthly": [{"month": "2026-09", "ind": {"pct_healthy_growth": _cell(0.55)}}],
        "monthlyByScope": {
            "all": [{"month": "2026-09", "ind": {"pct_healthy_growth": _cell(0.55)}}],
            f"opp:{OTHER}": [{"month": "2026-09", "ind": other_ind}],
            f"opp:{MINE}": [{"month": "2026-09", "ind": mine_ind}],
        },
        "series": {},
        "credibility": {"mortality": {"Other LLO": False, "My LLO": True}},
        "pooledOverCredible": {"mortality": {"ind": _cell(0.05), "llos": ["My LLO"], "of": 2}},
        "deployment": {
            "llo_map": {str(OTHER): "Other LLO", str(MINE): "My LLO"},
            "app_asks": {str(OTHER): {"x": True}, str(MINE): {"x": False}},
            "asks_as": {},
        },
        "cohortEdges": {"caseload": [2.0, 3.0]},
        "meta": {"as_of": "2026-09-13", "cases": 5, "opportunities": 2, "llos": 2},
    }


class TestASliceCarriesOnlyItsOwnOpportunity:
    def test_nothing_of_the_other_opportunity_survives(self):
        text = json.dumps(hd.slice_for_opportunity(payload(), MINE))
        for leak in (str(OTHER), "Other LLO", "zed", "c-other-1", "c-other-2", "0.09", "0.4,"):
            assert leak not in text, f"{leak!r} reached another opportunity's report"

    def test_its_own_figures_become_the_page_scope(self):
        out = hd.slice_for_opportunity(payload(), MINE)
        assert out["programInd"] == payload()["byOpp"][1]["ind"]
        assert [r["opp"] for r in out["byOpp"]] == [MINE]
        assert out["weekly"]["all"] == [{"week": "2026-09-07", "visits": 10, "registered": 2}]
        assert out["monthly"] == out["monthlyByScope"]["all"] == payload()["monthlyByScope"][f"opp:{MINE}"]
        assert out["meta"]["cases"] == 3 and out["meta"]["visits"] == 10 and out["meta"]["opportunities"] == 1

    def test_workers_still_point_at_their_own_cases(self):
        """`byFLW[].rows` are positions into the case index; the index shrank, so
        every position had to move with it."""
        out = hd.slice_for_opportunity(payload(), MINE)
        cases = out["cases"]
        by_worker = {f["username"]: [cases[i]["entity_id"] for i in f["rows"]] for f in out["byFLW"]}
        assert by_worker == {"amy": ["c-mine-1", "c-mine-2"], "bea": ["c-mine-3"]}

    def test_the_programme_pool_is_dropped(self):
        """Pooled over the programme's credible recorders: a programme figure, and
        it names the organisations that recorded credibly."""
        assert hd.slice_for_opportunity(payload(), MINE)["pooledOverCredible"] == {}

    def test_it_says_where_it_came_from(self):
        src = {"workflow_id": 7, "run_id": 9, "as_of": "2026-09-13"}
        assert hd.slice_for_opportunity(payload(), MINE, source=src)["meta"]["handed_down_from"] == src

    def test_an_opportunity_not_in_the_run_is_refused(self):
        with pytest.raises(hd.HandDownError):
            hd.slice_for_opportunity(payload(), 999)

    def test_a_run_from_before_the_unified_indicator_set_is_refused(self):
        legacy = payload()
        legacy["programInd"] = {"C14": _cell(0.05)}
        with pytest.raises(hd.HandDownError):
            hd.slice_for_opportunity(legacy, MINE)

    def test_every_key_the_real_builder_emits_is_either_sliced_or_known_safe(self):
        """A key the builder adds later is carried over whole unless it is listed.
        This fails the day that happens, so someone decides whether it carries
        another opportunity's data before it reaches a network manager."""
        real = semantic_snapshot.build(spec={}, rows=[], measures=[], deployment={}, as_of="2026-09-13")
        known_safe = {"schema", "cMeasures", "generated_at", "cohortEdges", "nSeries"}
        assert set(real) <= hd._REPLACED | known_safe, set(real) - hd._REPLACED - known_safe


@pytest.mark.django_db
class TestWhichReportReceivesASlice:
    def _definition(self, config=None, follows=True):
        data = {"config": config or {}}
        if follows:
            data["render_source"] = {"template": "kmc_opp_report"}
        return SimpleNamespace(id=1, data=data, template_type="kmc_opp_report")

    def test_a_report_naming_its_source_takes_it(self):
        assert hd.names_source(self._definition({"source_workflow_id": 19778}), 19778, MINE)
        assert not hd.names_source(self._definition({"source_workflow_id": 5456}), 19778, MINE)

    def test_an_older_report_follows_its_benchmark_cohort(self):
        cohort = BenchmarkCohort.objects.create(name="KMC", organization_id="x", source_workflow_id=19778)
        cohort.members.create(opportunity_id=MINE)
        assert hd.names_source(self._definition(), 19778, MINE)
        assert not hd.names_source(self._definition(), 5456, MINE)
        assert not hd.names_source(self._definition(), 19778, OTHER)

    def test_a_fork_with_its_own_render_does_not_follow_the_cohort(self):
        """A workflow built from the template and then given its own page -- on prod,
        a twin/triplet audit in opportunity 1488 -- is not an opportunity report and
        received a slice before this rule existed. It must name a source to get one."""
        cohort = BenchmarkCohort.objects.create(name="KMC", organization_id="x", source_workflow_id=19778)
        cohort.members.create(opportunity_id=MINE)
        assert not hd.names_source(self._definition(follows=False), 19778, MINE)
        assert hd.names_source(self._definition({"source_workflow_id": 19778}, follows=False), 19778, MINE)

    def test_only_a_template_that_asks_for_hand_downs_receives_them(self):
        assert hd.receives_hand_down(SimpleNamespace(template_type="kmc_opp_report"))
        assert not hd.receives_hand_down(SimpleNamespace(template_type="kmc_programme_metrics"))


class FakeRun(SimpleNamespace):
    @property
    def is_completed(self):
        return self.status == "completed"


class FakeWDA:
    """The four calls `write_slice` makes, over an in-memory run list."""

    def __init__(self, runs=None, definitions=None):
        self.runs = list(runs or [])
        self.definitions = definitions or []
        self.deleted = []
        self.closed = False
        self._next = 100

    def list_definitions(self):
        return self.definitions

    def list_runs(self, definition_id=None):
        return [r for r in self.runs if definition_id is None or r.definition_id == definition_id]

    def create_run(self, *, definition_id, opportunity_id, period_start, period_end, initial_state):
        self._next += 1
        run = FakeRun(
            id=self._next,
            definition_id=definition_id,
            period_start=period_start,
            period_end=period_end,
            state=dict(initial_state),
            status="in_progress",
            snapshot=None,
            completed_at=None,
        )
        self.runs.append(run)
        return run

    def complete_run(self, run_id, snapshot, run=None):
        target = next(r for r in self.runs if r.id == run_id)
        target.status, target.snapshot = "completed", snapshot
        return target

    def delete_run(self, run_id):
        self.deleted.append(run_id)
        self.runs = [r for r in self.runs if r.id != run_id]

    def close(self):
        self.closed = True


def _source_run(run_id=9, end="2026-09-13"):
    return FakeRun(
        id=run_id,
        definition_id=19778,
        period_start="2026-09-07",
        period_end=end,
        status="completed",
        snapshot=wrap_for_runner(payload()),
        completed_at="2026-09-14T00:00:00",
        state={},
    )


def _receiver():
    return SimpleNamespace(
        id=50,
        data={"config": {"source_workflow_id": 19778}, "render_source": {"template": "kmc_opp_report"}},
        template_type="kmc_opp_report",
    )


def _write(wda, source):
    return hd.write_slice(
        wda,
        _receiver(),
        source,
        payload(),
        opportunity_id=MINE,
        source_workflow_id=19778,
        state_key="snapshot",
    )


class TestWritingASlice:
    def test_it_lands_as_a_completed_run_of_the_opportunity_report(self):
        wda = FakeWDA()
        out = _write(wda, _source_run())
        assert out["action"] == "created"
        run = wda.runs[0]
        assert run.definition_id == 50 and run.is_completed and run.period_end == "2026-09-13"
        assert run.state["handed_down_from"] == {"workflow_id": 19778, "run_id": 9, "as_of": "2026-09-13"}
        stored = run.snapshot["state"]["snapshot"]
        assert [r["opp"] for r in stored["byOpp"]] == [MINE]

    def test_the_same_run_twice_writes_nothing(self):
        wda = FakeWDA()
        _write(wda, _source_run())
        assert _write(wda, _source_run())["action"] == "unchanged"
        assert len(wda.runs) == 1

    def test_a_newer_save_of_the_week_replaces_the_older_hand_down(self):
        wda = FakeWDA()
        _write(wda, _source_run(run_id=9))
        first = wda.runs[0].id
        out = _write(wda, _source_run(run_id=10))
        assert out["action"] == "replaced"
        assert wda.deleted == [first]
        assert [r.state["handed_down_from"]["run_id"] for r in wda.runs] == [10]

    def test_a_week_the_report_saved_itself_is_never_touched(self):
        own = FakeRun(
            id=1,
            definition_id=50,
            period_start="2026-09-07",
            period_end="2026-09-13",
            status="completed",
            snapshot={},
            completed_at="x",
            state={},
        )
        wda = FakeWDA(runs=[own])
        assert _write(wda, _source_run())["action"] == "created"
        assert own in wda.runs and not wda.deleted

    def test_a_failed_completion_leaves_no_half_written_run(self):
        wda = FakeWDA()
        wda.complete_run = lambda *a, **k: None
        with pytest.raises(hd.HandDownError):
            _write(wda, _source_run())
        assert wda.runs == []


class TestHandingDownARun:
    def test_every_opportunity_report_that_follows_it_gets_its_own_slice(self):
        mine = FakeWDA(definitions=[_receiver()])
        other = FakeWDA(
            definitions=[
                SimpleNamespace(id=60, data={"config": {"source_workflow_id": 1}}, template_type="kmc_opp_report")
            ]
        )
        by_opp = {MINE: mine, OTHER: other}
        report = hd.hand_down_run(lambda opp: by_opp[opp], 19778, _source_run())
        assert report == [
            {"opportunity_id": MINE, "workflow_id": 50, "action": "created", "run_id": mine.runs[0].id, "error": None}
        ]
        assert other.runs == [], "a report following a different programme received a slice"
        assert mine.closed and other.closed

    def test_a_legacy_run_is_skipped_not_failed(self):
        run = _source_run()
        run.snapshot["state"]["snapshot"]["programInd"] = {"C14": _cell(0.05)}
        report = hd.hand_down_run(lambda opp: FakeWDA(), 19778, run)
        assert report[0]["action"] == "skipped"


def test_a_history_is_handed_down_one_run_per_week_latest_completion_first():
    early = FakeRun(id=1, period_end="2026-09-06", status="completed", completed_at="2026-09-07")
    hand = FakeRun(id=2, period_end="2026-09-13", status="completed", completed_at="2026-09-14")
    rebuilt = FakeRun(id=3, period_end="2026-09-13", status="completed", completed_at="2026-09-20")
    draft = FakeRun(id=4, period_end="2026-09-20", status="in_progress", completed_at=None)
    assert [r.id for r in hd.latest_run_per_period([hand, draft, rebuilt, early])] == [1, 3]


def test_only_a_template_that_hands_down_queues_a_hand_down(monkeypatch):
    from connect_labs.workflow import tasks

    sent = []
    monkeypatch.setattr(tasks.hand_down_task, "delay", lambda *a, **k: sent.append(k))
    wda = SimpleNamespace(access_token="t", opportunity_id=1, program_id=None)
    assert not hd.queue_hand_down(wda, workflow_id=5, template_type="kmc_opp_report")
    assert hd.queue_hand_down(wda, workflow_id=5, template_type="kmc_programme_metrics", run_id=9)
    assert sent == [{"workflow_id": 5, "run_id": 9, "opportunity_id": 1, "program_id": None}]
