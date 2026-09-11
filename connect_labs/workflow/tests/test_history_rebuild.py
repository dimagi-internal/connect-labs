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

    @property
    def status(self):
        return self.data["status"]

    @property
    def completed_at(self):
        return self.data.get("completed_at")


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
    _stub_ensure(monkeypatch)


ENSURED: list = []


def _stub_ensure(monkeypatch, failed=()):
    """The cache step, recorded rather than run: it talks to the real backend."""
    ENSURED.clear()

    def fake(dao, definition_id, **kw):
        ENSURED.append(kw)
        return {"failed": list(failed), "hold_until": "2026-09-11T03:00:00+00:00"}

    monkeypatch.setattr(hr, "ensure_visit_cache", fake)


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


# ---------------------------------------------------------------------------
# Rolling. A year of weekly history is ~70 full evaluations at ~12s each, so one
# call that walks the whole range is a single request held open for fifteen
# minutes -- longer than any client or load balancer will wait for it. A client
# idle timeout already killed an 11-opportunity synthetic clone whose work had
# fully succeeded (connect-labs#1220). So a call does a BOUNDED batch and hands
# back a cursor; the caller rolls forward until `done`.
# ---------------------------------------------------------------------------


class TestRolling:
    def _rebuild(self, dao, **kw):
        kw.setdefault("cadence", "weekly")
        kw.setdefault("opportunity_id", 10)
        return hr.rebuild_history(dao, 1, **kw)

    def test_a_limit_builds_only_the_first_batch_and_returns_a_cursor(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = self._rebuild(dao, start=date(2026, 8, 3), end=date(2026, 9, 6), limit=2)

        assert report["periods"] == 5, "the whole range is still reported"
        assert report["batch"] == 2
        assert [r["period_end"] for r in report["runs"]] == ["2026-08-09", "2026-08-16"]
        assert report["done"] is False
        assert report["next_start"] == "2026-08-17", "the Monday the next batch begins on"
        assert report["end"] == "2026-09-06", "echoed so every batch walks the same window"
        assert len([c for c in dao.calls if c[0] == "create"]) == 2

    def test_rolling_to_the_end_covers_every_period_exactly_once(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        seen, start, calls = [], date(2026, 8, 3), 0
        while True:
            report = self._rebuild(dao, start=start, end=date(2026, 9, 6), limit=2)
            seen += [r["period_end"] for r in report["runs"]]
            calls += 1
            if report["done"]:
                assert report["next_start"] is None
                break
            start = date.fromisoformat(report["next_start"])

        assert seen == ["2026-08-09", "2026-08-16", "2026-08-23", "2026-08-30", "2026-09-06"]
        assert calls == 3
        assert len(dao.list_runs(1)) == 5, "no period built twice across batch boundaries"

    def test_the_last_partial_batch_is_done(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = self._rebuild(dao, start=date(2026, 8, 31), end=date(2026, 9, 6), limit=5)

        assert report["batch"] == 1
        assert report["done"] is True
        assert report["next_start"] is None

    def test_no_limit_still_walks_the_whole_range(self, monkeypatch):
        # The in-process caller (a test, a shell) may still want one pass.
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = self._rebuild(dao, start=date(2026, 8, 3), end=date(2026, 9, 6))

        assert report["batch"] == 5
        assert report["done"] is True

    def test_the_cap_is_checked_against_the_whole_range_not_the_batch(self, monkeypatch):
        # Otherwise a mistyped start sails through one small batch at a time and
        # the cap never fires.
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        with pytest.raises(hr.HistoryRebuildError) as e:
            self._rebuild(dao, cadence="daily", start=date(2020, 1, 1), end=date(2026, 9, 6), limit=3)
        assert e.value.code == "too_many_periods"

    def test_a_dry_run_reports_the_whole_plan_regardless_of_limit(self, monkeypatch):
        # The point of a dry run is to see the size of the walk before paying for it.
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        report = self._rebuild(dao, start=date(2026, 8, 3), end=date(2026, 9, 6), limit=2, dry_run=True)

        assert len(report["runs"]) == 5
        assert report["done"] is True
        assert dao.calls == []

    def test_progress_is_reported_once_per_period(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)
        ticks = []

        self._rebuild(
            dao,
            start=date(2026, 8, 3),
            end=date(2026, 9, 6),
            limit=3,
            progress=lambda done, total=None, message=None: ticks.append((done, total)),
        )

        assert ticks == [(1, 3), (2, 3), (3, 3)]

    def test_a_progress_callback_that_raises_cannot_fail_the_rebuild(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        def broken(*a, **kw):
            raise RuntimeError("client went away")

        report = self._rebuild(dao, start=date(2026, 8, 31), end=date(2026, 9, 6), progress=broken)
        assert report["created"] == 1

    def test_a_non_positive_limit_is_refused(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)

        with pytest.raises(hr.HistoryRebuildError) as e:
            self._rebuild(dao, start=date(2026, 8, 31), end=date(2026, 9, 6), limit=0)
        assert e.value.code == "bad_limit"


# ---------------------------------------------------------------------------
# Preview as of a date. The check that has to come BEFORE writing any history:
# grade the registry at one date exactly as a rebuilt snapshot would -- same
# builder, same binding -- and persist nothing, so the figures can be compared
# against the reference before a single run is written.
# ---------------------------------------------------------------------------


def _payload(as_of):
    cell = lambda v, n: {"id": "x", "value": v, "n": n, "band": "green"}  # noqa: E731
    return {
        "state": {
            "snapshot": {
                "meta": {"as_of": as_of, "cases": 8823},
                "programInd": {"C01": cell(8776, 8776)},
                "byLLO": [{"llo": "PIPN", "ind": {"C01": cell(5389, 5389)}, "n": 5399, "opps": [1, 2]}],
                "byOpp": [{"opp": 524, "ind": {}}],
                "byFLW": [{"key": "a"}] * 50,
                "cases": [{"entity_id": i} for i in range(500)],
                "monthly": [1, 2, 3],
                "series": {
                    "N": {
                        "measures": [{"indicator": "N10"}],
                        "programme": {"N10": cell(65.0, 4924)},
                        "byLLO": [{"llo": "PIPN", "ind": {"N10": cell(72.0, 3238)}, "n": 5399}],
                        "byOpp": [{"opp": 524}],
                        "byFLW": [{"key": "a"}] * 50,
                    }
                },
            }
        }
    }


def _stub_preview_build(monkeypatch, seen):
    def fake(dao, run, **kw):
        seen["run"] = run
        seen["kw"] = kw
        return {
            "payload": _payload(run.period_end),
            "contract": {"source": "definition", "snapshot_inputs": {"builder": "semantic_snapshot"}},
            "definition": dao.get_definition(1),
            "opportunity_id": 10,
            "opportunity_ids": [10, 11],
        }

    monkeypatch.setattr(hr, "build_snapshot_for_run", fake)
    monkeypatch.setattr(hr, "cache_state", lambda ids: {"cold_cache": False, "partial_cache": False})
    _stub_ensure(monkeypatch)


class TestPreviewAsOf:
    def test_it_builds_as_of_the_requested_date_and_writes_nothing(self, monkeypatch):
        dao = _DAO(_Definition())
        seen = {}
        _stub_preview_build(monkeypatch, seen)

        out = hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)

        assert seen["run"].period_end == "2026-09-10", "the builder's as-of is the requested date"
        assert seen["run"].data["definition_id"] == 1
        assert dao.calls == [], "a preview creates, completes and deletes nothing"
        assert out["as_of"] == "2026-09-10"
        assert out["meta"]["as_of"] == "2026-09-10"

    def test_it_returns_the_graded_cells_a_comparison_needs(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_preview_build(monkeypatch, {})

        out = hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)

        assert out["programme"]["C01"]["value"] == 8776
        assert out["byLLO"][0]["llo"] == "PIPN"
        n = out["series"]["N"]
        assert n["programme"]["N10"]["value"] == 65.0
        assert n["byLLO"][0]["ind"]["N10"] == {"id": "x", "value": 72.0, "n": 3238, "band": "green"}
        assert out["cache"] == {"cold_cache": False, "partial_cache": False}

    def test_it_drops_the_bulk_a_comparison_does_not_need(self, monkeypatch):
        # A KMC snapshot is megabytes, almost all of it the case index and the
        # per-worker cells. None of that is needed to check a figure.
        dao = _DAO(_Definition())
        _stub_preview_build(monkeypatch, {})

        out = hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)

        for heavy in ("cases", "byFLW", "monthly", "byOpp"):
            assert heavy not in out
        assert "byFLW" not in out["series"]["N"] and "byOpp" not in out["series"]["N"]

    def test_opportunity_cells_are_available_on_request(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_preview_build(monkeypatch, {})

        out = hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10, include_opportunities=True)

        assert out["byOpp"] == [{"opp": 524, "ind": {}}]
        assert out["series"]["N"]["byOpp"] == [{"opp": 524}]

    def test_an_ineligible_workflow_is_refused(self, monkeypatch):
        # A preview of a builder that ignores the date would show today's figures
        # labelled as the requested date -- the same lie a flat rebuilt series tells.
        dao = _DAO(_Definition(builder="copy_rows"))
        _stub_preview_build(monkeypatch, {})

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)
        assert e.value.code == "not_periodic"

    def test_a_cold_cache_is_refused_by_name(self, monkeypatch):
        from connect_labs.workflow.snapshot_runtime import SnapshotBuildError

        dao = _DAO(_Definition())

        def cold(dao, run, **kw):
            raise SnapshotBuildError("cache_miss", "no cached data for pipeline 'children'")

        monkeypatch.setattr(hr, "build_snapshot_for_run", cold)
        _stub_ensure(monkeypatch)  # ensure succeeded; the miss comes at build time

        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)
        assert e.value.code == "cache_miss"


def test_a_preview_states_the_registry_it_graded_with(monkeypatch):
    # A comparison against a reference means nothing without knowing whether it ran
    # on a bound record or on the on-disk copy.
    dao = _DAO(_Definition())
    _stub_preview_build(monkeypatch, {})
    out = hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)
    assert out["registry"]["source"] == "disk"


# ---------------------------------------------------------------------------
# The visit cache is ensured before anything is built from it.
# ---------------------------------------------------------------------------


class TestTheCacheIsEnsuredFirst:
    def test_a_batch_ensures_the_whole_cohort_before_building(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)
        report = hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 6), opportunity_id=10
        )
        assert len(ENSURED) == 1
        assert ENSURED[0]["opportunity_id"] == 10 and ENSURED[0]["hold_minutes"] >= 60
        assert report["visit_cache"] == "2026-09-11T03:00:00+00:00", "the report says how long the data is held"

    def test_an_opportunity_that_cannot_be_cached_stops_the_batch_and_writes_nothing(self, monkeypatch):
        # A partial cohort would publish plausible, understated figures -- the failure
        # this step exists to end. So it stops, naming the opportunities.
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)
        _stub_ensure(monkeypatch, failed=[524, 874])
        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.rebuild_history(
                dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 6), opportunity_id=10
            )
        assert e.value.code == "cache_incomplete"
        assert "524" in e.value.message and "874" in e.value.message
        assert dao.calls == []

    def test_a_dry_run_does_not_download_anything(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_build(monkeypatch)
        hr.rebuild_history(
            dao, 1, cadence="weekly", start=date(2026, 8, 31), end=date(2026, 9, 6), opportunity_id=10, dry_run=True
        )
        assert ENSURED == []

    def test_a_preview_ensures_the_cache_too(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_preview_build(monkeypatch, {})
        hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)
        assert len(ENSURED) == 1

    def test_a_preview_over_a_partial_cohort_is_refused(self, monkeypatch):
        dao = _DAO(_Definition())
        _stub_preview_build(monkeypatch, {})
        _stub_ensure(monkeypatch, failed=[1487])
        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.preview_as_of(dao, 1, as_of=date(2026, 9, 10), opportunity_id=10)
        assert e.value.code == "cache_incomplete"


class TestExportAndPrune:
    """Choosing a window is the caller's decision: back it up, then keep only the window.

    A rebuild only adds or replaces periods, so a shorter window after a longer one
    left the old block on the chart -- and the trend spaces points evenly, not by
    date, so the old block and the new one read as one continuous line.
    """

    def _history(self):
        mine = lambda i, end: _Run(i, end, end, state={"generated_by": hr.GENERATED_BY}, completed=True)  # noqa: E731
        return [
            mine(1, "2025-05-25"),
            mine(2, "2025-06-01"),
            _Run(3, "2025-06-01", "2025-06-01", state={}, completed=True),  # saved by hand
            mine(4, "2026-04-26"),
            mine(5, "2026-09-06"),
            _Run(6, "2025-10-12", "2025-10-12", state={"generated_by": hr.GENERATED_BY}),  # cut off mid-build
        ]

    def test_prune_deletes_only_stamped_runs_outside_the_window(self):
        dao = _DAO(_Definition(), runs=self._history())
        report = hr.prune_history(dao, 1, keep_from=date(2026, 4, 20), dry_run=False)
        assert sorted(r.id for r in dao._runs) == [3, 4, 5], "the hand-saved run and the window survive"
        assert report["pruned"] == 3 and report["kept"] == 2

    def test_an_unfinished_stamped_run_outside_the_window_is_pruned(self):
        # A rebuild cut off mid-request leaves one behind; it must not be immortal.
        dao = _DAO(_Definition(), runs=self._history())
        hr.prune_history(dao, 1, keep_from=date(2026, 4, 20), dry_run=False)
        assert 6 not in [r.id for r in dao._runs]

    def test_prune_is_a_dry_run_by_default(self):
        dao = _DAO(_Definition(), runs=self._history())
        report = hr.prune_history(dao, 1, keep_from=date(2026, 4, 20))
        assert len(dao._runs) == 6 and not [c for c in dao.calls if c[0] == "delete"]
        assert [r["run_id"] for r in report["runs"]["would_prune"]] == [1, 2, 6]

    def test_prune_refuses_to_run_without_a_window(self):
        with pytest.raises(hr.HistoryRebuildError) as e:
            hr.prune_history(_DAO(_Definition(), runs=self._history()), 1, dry_run=False)
        assert e.value.code == "no_window"

    def test_keep_to_bounds_the_other_end(self):
        dao = _DAO(_Definition(), runs=self._history())
        hr.prune_history(dao, 1, keep_from=date(2025, 5, 1), keep_to=date(2026, 5, 1), dry_run=False)
        assert sorted(r.id for r in dao._runs) == [1, 2, 3, 4, 6]

    def test_export_pages_in_period_order_with_the_full_record(self):
        dao = _DAO(_Definition(), runs=self._history())
        first = hr.history_runs(dao, 1, include_snapshot=True, limit=2)
        assert [r["run_id"] for r in first["runs"]] == [1, 2]
        assert first["runs"][0]["data"]["state"]["generated_by"] == hr.GENERATED_BY
        assert first["done"] is False and first["next_start_at"] == 2
        rest = hr.history_runs(dao, 1, include_snapshot=True, start_at=first["next_start_at"])
        assert [r["run_id"] for r in rest["runs"]] == [6, 4, 5] and rest["done"] is True

    def test_export_lists_hand_saved_runs_only_when_asked(self):
        dao = _DAO(_Definition(), runs=self._history())
        assert 3 not in [r["run_id"] for r in hr.history_runs(dao, 1)["runs"]]
        assert 3 in [r["run_id"] for r in hr.history_runs(dao, 1, generated_only=False)["runs"]]


class TestPruneNamedRuns:
    """A stale hand-made report sits on a trend until someone removes it; the rebuild
    and the window prune never touch runs a person saved. Naming ids is the way."""

    def _history(self):
        return [
            _Run(1, "2026-09-06", "2026-09-06", state={"generated_by": hr.GENERATED_BY}, completed=True),
            _Run(2, "2026-09-09", "2026-09-09", state={}, completed=True),  # hand-made, stale
            _Run(3, "2026-09-10", "2026-09-10", state={}, completed=True),  # hand-made, stale
            _Run(4, "2026-09-11", "2026-09-11", state={}, completed=True),  # hand-made, today's
        ]

    def test_exactly_the_named_runs_go_hand_made_included(self):
        dao = _DAO(_Definition(), runs=self._history())
        report = hr.prune_history(dao, 1, run_ids=[2, 3], dry_run=False)
        assert sorted(r.id for r in dao._runs) == [1, 4]
        assert report["pruned"] == 2 and report["missing"] == []

    def test_an_id_that_is_not_this_workflows_run_is_reported_not_deleted(self):
        dao = _DAO(_Definition(), runs=self._history())
        report = hr.prune_history(dao, 1, run_ids=[3, 999], dry_run=False)
        assert report["missing"] == [999] and sorted(r.id for r in dao._runs) == [1, 2, 4]

    def test_named_pruning_is_a_dry_run_by_default(self):
        dao = _DAO(_Definition(), runs=self._history())
        report = hr.prune_history(dao, 1, run_ids=[2])
        assert len(dao._runs) == 4 and [r["run_id"] for r in report["runs"]["would_prune"]] == [2]

    def test_an_empty_list_is_refused(self):
        with pytest.raises(hr.HistoryRebuildError):
            hr.prune_history(_DAO(_Definition(), runs=self._history()), 1, run_ids=[], dry_run=False)
