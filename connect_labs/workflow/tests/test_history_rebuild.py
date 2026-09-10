"""Rebuilding a workflow's periodic history: the series it WOULD have had.

The trend on a periodic report draws one point per saved run, so a report that
has only ever been run once has no line. `history_rebuild` produces the runs
that would exist if the report had been running on its cadence all along, each
computed AS OF its own period end.

The tests that matter here are the ones about what it refuses and what it
must not destroy, because both failures are silent:

  * A builder that ignores `period_end` yields the SAME snapshot for every
    period. That renders as a flat line over real dates -- data, apparently,
    and wrong. Refused up front rather than discovered by reading the chart.
  * A rebuild replaces its own previous output. If it replaced everything it
    found, the report a human made and named by hand would vanish into a
    machine series, with no way back.
  * The new run is built and completed BEFORE the old one is deleted, so a
    build that fails leaves the existing history intact.
"""

from __future__ import annotations

from datetime import date

import pytest

from connect_labs.workflow import history_rebuild as hr

# ---------------------------------------------------------------------------
# Period generation -- a pure function, so it is pinned exactly.
# ---------------------------------------------------------------------------


class TestPeriods:
    def test_weekly_periods_are_monday_to_sunday(self):
        # 2026-09-01 is a Tuesday; 2026-09-30 a Wednesday.
        got = list(hr.periods("weekly", date(2026, 9, 1), date(2026, 9, 30)))
        assert got == [
            (date(2026, 8, 31), date(2026, 9, 6)),
            (date(2026, 9, 7), date(2026, 9, 13)),
            (date(2026, 9, 14), date(2026, 9, 20)),
            (date(2026, 9, 21), date(2026, 9, 27)),
        ]
        for start, end in got:
            assert start.weekday() == 0, "period starts Monday"
            assert end.weekday() == 6, "period ends Sunday"

    def test_weekly_excludes_a_week_whose_sunday_has_not_arrived(self):
        # End mid-week: the week in progress is not a completed period.
        got = list(hr.periods("weekly", date(2026, 9, 7), date(2026, 9, 16)))
        assert got == [(date(2026, 9, 7), date(2026, 9, 13))]

    def test_weekly_start_inside_a_week_still_yields_that_whole_week(self):
        # The as-of cut handles a period that begins before the data does, so
        # the range is aligned to real week boundaries rather than truncated.
        got = list(hr.periods("weekly", date(2026, 9, 2), date(2026, 9, 6)))
        assert got == [(date(2026, 8, 31), date(2026, 9, 6))]

    def test_daily_periods_are_single_days(self):
        got = list(hr.periods("daily", date(2026, 9, 1), date(2026, 9, 3)))
        assert got == [
            (date(2026, 9, 1), date(2026, 9, 1)),
            (date(2026, 9, 2), date(2026, 9, 2)),
            (date(2026, 9, 3), date(2026, 9, 3)),
        ]

    def test_empty_when_the_range_holds_no_complete_period(self):
        assert list(hr.periods("weekly", date(2026, 9, 7), date(2026, 9, 9))) == []

    def test_unknown_cadence_is_refused_by_name(self):
        with pytest.raises(hr.HistoryRebuildError) as e:
            list(hr.periods("fortnightly", date(2026, 9, 1), date(2026, 9, 30)))
        assert e.value.code == "bad_cadence"
        assert "fortnightly" in e.value.message


class TestEarliestDate:
    def test_takes_the_minimum_across_every_named_field(self):
        rows = [
            {"reg_date": "2026-04-10", "first_visit_date": "2026-04-02"},
            {"reg_date": "2026-03-15"},
            {"first_visit_date": "2026-05-01"},
        ]
        assert hr.earliest_date(rows, ("reg_date", "first_visit_date")) == date(2026, 3, 15)

    def test_ignores_blanks_and_unparseable_values(self):
        rows = [{"reg_date": ""}, {"reg_date": None}, {"reg_date": "not-a-date"}, {"reg_date": "2026-06-01"}]
        assert hr.earliest_date(rows, ("reg_date",)) == date(2026, 6, 1)

    def test_none_when_nothing_is_dated(self):
        assert hr.earliest_date([{"reg_date": ""}, {}], ("reg_date",)) is None
        assert hr.earliest_date([], ("reg_date",)) is None

    def test_accepts_a_datetime_string(self):
        assert hr.earliest_date([{"reg_date": "2026-06-01T09:30:00Z"}], ("reg_date",)) == date(2026, 6, 1)


# ---------------------------------------------------------------------------
# Fakes. Same style as test_one_snapshot_path.py: hand-rolled, no fixtures.
# ---------------------------------------------------------------------------


class _Definition:
    opportunity_id = 10
    opportunity_ids = [10]
    template_type = None
    name = "KMC Programme Metrics"
    registry_source = None
    pipeline_sources = [{"alias": "children", "pipeline_id": 1}]

    def __init__(self, builder="semantic_snapshot", manifest=True):
        """`manifest=False` drops the instance-owned snapshot_inputs, so the
        contract resolves through the template registry instead."""
        self.data = {"name": self.name}
        if manifest:
            self.data["snapshot_inputs"] = {"builder": builder, "case_index": {"pipeline": "children"}}


class _Run:
    def __init__(self, run_id, period_start, period_end, state=None, completed=False):
        self.id = run_id
        self.opportunity_id = 10
        self.data = {
            "definition_id": 1,
            "period_start": period_start,
            "period_end": period_end,
            "state": state or {},
            "status": "completed" if completed else "in_progress",
        }

    @property
    def period_start(self):
        return self.data["period_start"]

    @property
    def period_end(self):
        return self.data["period_end"]

    @property
    def state(self):
        return self.data.get("state", {})

    @property
    def is_completed(self):
        return self.data["status"] == "completed"


class _DAO:
    """Records every mutation in order, so tests can assert on the SEQUENCE."""

    access_token = "tok"
    program_id = None

    def __init__(self, definition, runs=None, rows=None):
        self._definition = definition
        self._runs = list(runs or [])
        self._rows = rows if rows is not None else [{"reg_date": "2026-08-17"}]
        self._next_id = 100
        self.calls: list[tuple] = []

    def get_definition(self, definition_id):
        return self._definition

    def list_runs(self, definition_id=None):
        return list(self._runs)

    def get_cached_pipeline_data(self, definition_id, opportunity_id, aliases=None, **kw):
        return {"children": {"rows": self._rows}}

    def create_run(
        self, *, definition_id, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None
    ):
        self._next_id += 1
        run = _Run(self._next_id, period_start, period_end, state=dict(initial_state or {}))
        self._runs.append(run)
        self.calls.append(("create", run.id, period_end))
        return run

    def complete_run(self, run_id, snapshot, run=None):
        target = next((r for r in self._runs if r.id == run_id), None)
        if target is not None:
            target.data["status"] = "completed"
            target.data["snapshot"] = snapshot
        self.calls.append(("complete", run_id))
        return target

    def delete_run(self, run_id, delete_linked=True):
        self._runs = [r for r in self._runs if r.id != run_id]
        self.calls.append(("delete", run_id))
        return {"run": 1}

    def close(self):
        pass


def _stub_build(monkeypatch, fail_on=None, error=None):
    """Make build_snapshot_for_run return a payload, or raise for one period."""
    from connect_labs.workflow import snapshot_runtime

    def fake(dao, run, **kw):
        if fail_on is not None and run.period_end == fail_on:
            raise error or snapshot_runtime.SnapshotBuildError("non_dict", "boom")
        return {
            "payload": {"state": {"snapshot": {"meta": {"as_of": run.period_end}}}},
            "contract": {"source": "definition"},
            "definition": dao.get_definition(1),
            "opportunity_id": 10,
            "opportunity_ids": [10],
        }

    monkeypatch.setattr(hr, "build_snapshot_for_run", fake)


# ---------------------------------------------------------------------------
# Eligibility -- the refusals that stop a plausible-looking wrong chart.
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_a_periodic_builder_is_eligible(self):
        ok, reason = hr.eligibility(_Definition(builder="semantic_snapshot"))
        assert ok is True
        assert reason is None

    def test_a_builder_that_ignores_period_end_is_refused(self):
        # The whole point: rebuilding this would write N identical snapshots
        # and draw a flat line over real dates.
        ok, reason = hr.eligibility(_Definition(builder="copy_rows"))
        assert ok is False
        assert "copy_rows" in reason
        assert "period_end" in reason

    def test_a_python_hook_contract_is_refused_because_it_cannot_be_inspected(self):
        # `program_audit_creator` builds its snapshot in Python. Whether that
        # code honours period_end is invisible from here, and guessing wrong
        # writes a flat series -- so it is refused, with the way out named.
        d = _Definition(manifest=False)
        d.template_type = "program_audit_creator"
        ok, reason = hr.eligibility(d)
        assert ok is False
        assert "Python hook" in reason
        assert "snapshot_inputs" in reason

    def test_a_definition_inherits_eligibility_from_its_template(self):
        # No instance manifest, but the name recovers a template whose OWN
        # manifest declares a periodic builder. That workflow is rebuildable:
        # the contract resolves exactly the way completion resolves it, so
        # eligibility cannot disagree with what a completion would actually do.
        d = _Definition(manifest=False)
        ok, reason = hr.eligibility(d)
        assert ok is True

    def test_a_definition_with_no_contract_at_all_is_refused(self):
        d = _Definition(manifest=False)
        d.data = {"name": "nothing recognisable"}
        ok, reason = hr.eligibility(d)
        assert ok is False
        assert "no usable snapshot contract" in reason


# ---------------------------------------------------------------------------
# The rebuild itself.
# ---------------------------------------------------------------------------


class TestRebuild:
    def test_writes_one_completed_run_per_period_stamped_as_generated(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 20), opportunity_id=10
        )

        assert report["created"] == 3
        assert report["replaced"] == 0
        assert report["failed"] == 0
        assert [r["period_end"] for r in report["runs"]] == ["2026-09-06", "2026-09-13", "2026-09-20"]
        assert all(r["action"] == "created" for r in report["runs"])

        written = dao.list_runs(1)
        assert len(written) == 3
        for run in written:
            assert run.is_completed
            assert run.state["generated_by"] == hr.GENERATED_BY
            # The as-of the builder saw is the run's own period end, which is
            # the entire reason a rebuilt point reports its week and not today.
            assert run.data["snapshot"]["state"]["snapshot"]["meta"]["as_of"] == run.period_end

    def test_replacing_deletes_only_its_own_previous_output(self, monkeypatch):
        mine = _Run(1, "2026-08-31", "2026-09-06", state={"generated_by": hr.GENERATED_BY}, completed=True)
        yours = _Run(2, "2026-08-31", "2026-09-06", state={}, completed=True)
        dao = _DAO(_Definition(), runs=[mine, yours])
        _stub_build(monkeypatch)

        report = hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 6), opportunity_id=10
        )

        assert report["replaced"] == 1
        surviving = {r.id for r in dao.list_runs(1)}
        assert 2 in surviving, "a hand-made run must survive a rebuild"
        assert 1 not in surviving, "the previous generated run is replaced, not duplicated"

    def test_the_new_run_is_completed_before_the_old_one_is_deleted(self, monkeypatch):
        old = _Run(1, "2026-08-31", "2026-09-06", state={"generated_by": hr.GENERATED_BY}, completed=True)
        dao = _DAO(_Definition(), runs=[old])
        _stub_build(monkeypatch)

        hr.rebuild_history(dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 6), opportunity_id=10)

        kinds = [c[0] for c in dao.calls]
        assert kinds == [
            "create",
            "complete",
            "delete",
        ], "delete-then-build would destroy real history on a build failure"

    def test_a_failed_period_keeps_the_old_run_and_cleans_up_the_new_one(self, monkeypatch):
        old = _Run(1, "2026-08-31", "2026-09-06", state={"generated_by": hr.GENERATED_BY}, completed=True)
        dao = _DAO(_Definition(), runs=[old])
        _stub_build(monkeypatch, fail_on="2026-09-06")

        report = hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 6), opportunity_id=10
        )

        assert report["failed"] == 1
        assert report["runs"][0]["action"] == "failed"
        assert "boom" in report["runs"][0]["error"]
        surviving = {r.id for r in dao.list_runs(1)}
        assert surviving == {1}, "the old run survives and the half-built one is removed"

    def test_one_bad_period_does_not_abandon_the_rest(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch, fail_on="2026-09-13")

        report = hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 20), opportunity_id=10
        )

        assert report["created"] == 2
        assert report["failed"] == 1
        assert [r["action"] for r in report["runs"]] == ["created", "failed", "created"]

    def test_a_cold_cache_aborts_immediately_rather_than_failing_every_period(self, monkeypatch):
        from connect_labs.workflow.snapshot_runtime import SnapshotBuildError

        dao = _DAO(_Definition())
        _stub_build(
            monkeypatch,
            fail_on="2026-09-06",
            error=SnapshotBuildError("cache_miss", "no cached data for pipeline 'children'"),
        )

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(
                dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 20), opportunity_id=10
            )
        assert e.value.code == "cache_miss"
        # It stopped at the first period rather than reporting the same
        # environmental failure 52 times.
        assert len([c for c in dao.calls if c[0] == "create"]) == 1

    def test_replace_false_skips_a_period_that_already_has_a_run(self, monkeypatch):
        existing = _Run(1, "2026-08-31", "2026-09-06", state={"generated_by": hr.GENERATED_BY}, completed=True)
        dao = _DAO(_Definition(), runs=[existing])
        _stub_build(monkeypatch)

        report = hr.rebuild_history(
            dao,
            1,
            cadence="weekly",
            start=date(2026, 8, 31),
            end=date(2026, 9, 13),
            opportunity_id=10,
            replace=False,
        )

        assert report["skipped"] == 1
        assert report["created"] == 1
        assert [r["action"] for r in report["runs"]] == ["skipped", "created"]

    def test_dry_run_writes_nothing(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = hr.rebuild_history(
            dao,
            1,
            cadence="weekly",
            start=date(2026, 8, 31),
            end=date(2026, 9, 20),
            opportunity_id=10,
            dry_run=True,
        )

        assert report["dry_run"] is True
        assert report["periods"] == 3
        assert dao.calls == []

    def test_start_is_derived_from_the_data_when_not_given(self, monkeypatch):
        dao = _DAO(_Definition(), rows=[{"reg_date": "2026-08-20"}, {"reg_date": "2026-09-01"}])
        _stub_build(monkeypatch)

        report = hr.rebuild_history(dao, 1, cadence="weekly", end=date(2026, 9, 6), opportunity_id=10)

        # Earliest activity 2026-08-20 (a Thursday) -> its week ends 2026-08-23.
        assert report["start"] == "2026-08-20"
        assert [r["period_end"] for r in report["runs"]] == ["2026-08-23", "2026-08-30", "2026-09-06"]

    def test_undated_data_asks_for_an_explicit_start_rather_than_guessing(self, monkeypatch):
        dao = _DAO(_Definition(), rows=[{"reg_date": ""}])
        _stub_build(monkeypatch)

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(dao, 1, cadence="weekly", end=date(2026, 9, 6), opportunity_id=10)
        assert e.value.code == "no_start"

    def test_a_cold_cache_while_deriving_the_start_says_so_instead_of_no_start(self, monkeypatch):
        # A cold cache and genuinely undated rows both leave the derivation
        # with nothing to read, but the remedies are opposite: load the
        # pipeline data, versus pass an explicit start. Reporting `no_start`
        # for a cold cache sends someone to supply a date that cannot help --
        # the rebuild then dies at its first period on the cache it never had.
        # Observed live on definition 5456.
        from connect_labs.workflow.data_access import PipelineCacheMiss

        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        def cold(*a, **kw):
            raise PipelineCacheMiss("children", 10, "KMC Case Properties (SQL)")

        dao.get_cached_pipeline_data = cold

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(dao, 1, cadence="weekly", end=date(2026, 9, 6), opportunity_id=10)
        assert e.value.code == "cache_miss"
        assert "children" in e.value.message, "names the pipeline to load"
        assert dao.calls == [], "nothing is written when the start cannot be derived"

    def test_any_other_read_failure_still_degrades_to_asking_for_a_start(self, monkeypatch):
        # Deriving a convenience default must not be able to fail a call the
        # caller could have made themselves by passing `start`.
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        def boom(*a, **kw):
            raise RuntimeError("some unrelated read problem")

        dao.get_cached_pipeline_data = boom

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(dao, 1, cadence="weekly", end=date(2026, 9, 6), opportunity_id=10)
        assert e.value.code == "no_start"

    def test_an_ineligible_definition_is_refused_before_anything_is_written(self, monkeypatch):
        dao = _DAO(_Definition(builder="copy_rows"))
        _stub_build(monkeypatch)

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(
                dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 20), opportunity_id=10
            )
        assert e.value.code == "not_periodic"
        assert dao.calls == []

    def test_a_range_beyond_the_cap_is_refused_with_the_count(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(
                dao, 1, cadence="daily", start=date(2020, 1, 1), end=date(2026, 9, 6), opportunity_id=10
            )
        assert e.value.code == "too_many_periods"

    def test_an_empty_range_is_refused_rather_than_reported_as_success(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(
                dao, 1, cadence="weekly", start=date(2026, 9, 7), end=date(2026, 9, 9), opportunity_id=10
            )
        assert e.value.code == "empty_range"

    def test_end_defaults_to_the_last_completed_period(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), opportunity_id=10, today=date(2026, 9, 16)
        )

        # 2026-09-16 is a Wednesday; the last COMPLETE week ended Sunday the 13th.
        assert report["runs"][-1]["period_end"] == "2026-09-13"
