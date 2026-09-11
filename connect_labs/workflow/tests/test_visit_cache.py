"""`ensure_visit_cache`: make a workflow's visit data present, explicitly, per opportunity.

The indicators read the RAW visit cache; the pipelines answer from a COMPUTED cache
that outlives it. Warming by running the pipelines could therefore succeed while
the raw rows the indicators need had expired and been deleted -- measured
2026-09-11, 9 of 12 KMC opportunities had no raw rows while every pipeline looked
healthy, and a report read 1,681 cases for a cohort of 8,823. These tests pin the
explicit operation that replaces that guesswork.
"""

from __future__ import annotations

import pytest

from connect_labs.workflow import visit_cache as vc


class _Definition:
    def __init__(self, opps=(523, 524, 874), pipelines=(19776, 19777)):
        self.opportunity_ids = list(opps)
        self.pipeline_sources = [{"pipeline_id": p, "alias": a} for p, a in zip(pipelines, ("children", "visits"))]
        self.data = {}


class _DAO:
    access_token = "tok"

    def __init__(self, definition):
        self._definition = definition

    def get_definition(self, definition_id):
        return self._definition


class _Slots:
    """A fake cache world: per (opp, pipeline) raw count before, and what a fetch does."""

    def __init__(self, raw_before=None, fail=(), from_cache=True):
        self.raw = dict(raw_before or {})
        self.fail = set(fail)
        self.from_cache = from_cache
        self.calls: list[tuple] = []

    def factory(self, access_token, owner_scope, sources=None):
        self.owner_scope = owner_scope
        self.sources = sources
        slots = self

        class _Mgr:
            def __init__(self, opp, pid):
                self.key = (opp, pid)

            def get_raw_visit_count(self):
                return slots.raw.get(self.key, 0)

            def extend_raw_cache_ttl(self, minutes):
                slots.calls.append(("hold_raw", self.key, minutes))

            def hold_computed_caches(self, minutes):
                slots.calls.append(("hold_computed", self.key, minutes))
                return 3

        def fetch(opp, pid):
            slots.calls.append(("fetch", (opp, pid)))
            if (opp, pid) in slots.fail:
                raise RuntimeError("export timed out")
            slots.raw[(opp, pid)] = slots.raw.get((opp, pid)) or 100  # a miss is rebuilt
            return slots.raw[(opp, pid)]

        def run(opp, pid):
            slots.calls.append(("run", (opp, pid)))
            return {"rows": [1, 2], "metadata": {"from_cache": slots.from_cache}}

        return _Mgr, fetch, run


def _ensure(slots, **kw):
    kw.setdefault("opportunity_id", 523)
    return vc.ensure_visit_cache(_DAO(_Definition()), 1, slot_factory=slots.factory, **kw)


class TestEnsure:
    def test_every_opportunity_and_every_pipeline_slot_is_handled(self):
        slots = _Slots()
        report = _ensure(slots)
        assert [e["opportunity_id"] for e in report["opportunities"]] == [523, 524, 874]
        for e in report["opportunities"]:
            assert [s["pipeline_id"] for s in e["slots"]] == [19776, 19777]
        assert report["done"] is True and report["failed"] == []

    def test_a_missing_raw_slot_is_fetched_and_reported_as_refreshed(self):
        # The case that bit: raw gone, pipeline still answering from its computed cache.
        slots = _Slots(raw_before={})
        report = _ensure(slots)
        assert all(s["raw"] == "refreshed" for e in report["opportunities"] for s in e["slots"])

    def test_a_valid_raw_slot_is_held_not_refetched(self):
        # The fetch still goes through the backend -- which answers from cache when
        # valid -- so a fresh slot costs a read, and is reported as held.
        slots = _Slots(raw_before={(o, p): 100 for o in (523, 524, 874) for p in (19776, 19777)})
        report = _ensure(slots)
        assert all(s["raw"] == "held" for e in report["opportunities"] for s in e["slots"])

    def test_both_layers_are_held_for_the_requested_window(self):
        slots = _Slots()
        _ensure(slots, hold_minutes=120)
        holds = [c for c in slots.calls if c[0].startswith("hold")]
        assert {c[2] for c in holds} == {120}
        assert len([c for c in holds if c[0] == "hold_raw"]) == 6
        assert len([c for c in holds if c[0] == "hold_computed"]) == 6

    def test_the_raw_fetch_happens_before_the_pipeline_runs(self):
        # A pipeline recomputed from a missing raw cache would rebuild it inside the
        # recompute instead -- the fetch has to lead so its outcome is observable.
        slots = _Slots()
        _ensure(slots)
        first = slots.calls[:3]
        assert [c[0] for c in first] == ["fetch", "hold_raw", "run"]

    def test_one_failing_opportunity_does_not_cost_the_rest(self):
        slots = _Slots(fail={(524, 19776)})
        report = _ensure(slots)
        by = {e["opportunity_id"]: e for e in report["opportunities"]}
        assert by[524]["ok"] is False and "export timed out" in by[524]["error"]
        assert by[523]["ok"] and by[874]["ok"]
        assert report["failed"] == [524]

    def test_pipelines_read_in_the_owner_scope(self):
        slots = _Slots()
        _ensure(slots, opportunity_id=523)
        assert slots.owner_scope == {"opportunity_id": 523}
        slots = _Slots()
        vc.ensure_visit_cache(_DAO(_Definition()), 1, program_id=46, slot_factory=slots.factory)
        assert slots.owner_scope == {"program_id": 46}


class TestRolling:
    def test_a_limit_takes_a_batch_and_returns_a_cursor(self):
        slots = _Slots()
        report = _ensure(slots, limit=2)
        assert [e["opportunity_id"] for e in report["opportunities"]] == [523, 524]
        assert report["done"] is False and report["next_start_at"] == 2

    def test_rolling_covers_every_opportunity_once(self):
        slots, seen, start = _Slots(), [], 0
        while True:
            report = _ensure(slots, limit=2, start_at=start)
            seen += [e["opportunity_id"] for e in report["opportunities"]]
            if report["done"]:
                break
            start = report["next_start_at"]
        assert seen == [523, 524, 874]

    def test_progress_is_reported_once_per_opportunity(self):
        slots, ticks = _Slots(), []
        _ensure(slots, progress=lambda done, total=None, message=None: ticks.append((done, total)))
        assert ticks == [(1, 3), (2, 3), (3, 3)]


class TestRefusals:
    @pytest.mark.parametrize("hold", [0, vc.MAX_HOLD_MINUTES + 1])
    def test_the_hold_is_bounded(self, hold):
        # Expiry is what re-reads a visit's status after review; an unbounded hold
        # would freeze statuses. So the hold covers a job, never longer.
        with pytest.raises(vc.VisitCacheError) as e:
            _ensure(_Slots(), hold_minutes=hold)
        assert e.value.code == "bad_hold"

    def test_a_workflow_that_reads_no_pipelines_is_refused(self):
        dao = _DAO(_Definition(pipelines=()))
        with pytest.raises(vc.VisitCacheError) as e:
            vc.ensure_visit_cache(dao, 1, opportunity_id=523, slot_factory=_Slots().factory)
        assert e.value.code == "no_pipelines"

    def test_exactly_one_owner(self):
        with pytest.raises(vc.VisitCacheError) as e:
            vc.ensure_visit_cache(_DAO(_Definition()), 1, slot_factory=_Slots().factory)
        assert e.value.code == "no_owner"


@pytest.mark.django_db
class TestComputedHold:
    """The computed-cache hold touches only LIVE rows, and only moves expiry forward."""

    def test_holds_live_rows_and_leaves_expired_ones_to_die(self):
        from datetime import timedelta

        from django.utils import timezone

        from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
        from connect_labs.labs.analysis.backends.sql.models import ComputedEntityCache

        now = timezone.now()
        common = {"opportunity_id": 523, "pipeline_id": 19776, "config_hash": "h", "visit_count": 1}
        live = ComputedEntityCache.objects.create(entity_id="a", expires_at=now + timedelta(minutes=5), **common)
        dead = ComputedEntityCache.objects.create(entity_id="b", expires_at=now - timedelta(minutes=5), **common)
        long_lived = ComputedEntityCache.objects.create(entity_id="c", expires_at=now + timedelta(hours=5), **common)

        SQLCacheManager(523, pipeline_id=19776).hold_computed_caches(90)

        live.refresh_from_db(), dead.refresh_from_db(), long_lived.refresh_from_db()
        assert live.expires_at > now + timedelta(minutes=80), "a live row is held"
        assert dead.expires_at < now, "an expired row is not resurrected"
        assert long_lived.expires_at > now + timedelta(hours=4), "a longer-lived row is never shortened"
