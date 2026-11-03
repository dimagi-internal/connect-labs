"""One background fill of a cold cache: the slot, and the visit-cache task that runs in it."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from connect_labs.workflow import cache_warm, visit_cache
from connect_labs.workflow.cache_warm import WarmSlot
from connect_labs.workflow.visit_cache import visit_warm_slot, warm_visit_cache_in_background


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestSlot:
    def test_only_the_first_reader_claims_it(self):
        slot = WarmSlot("k")
        assert slot.claim()
        assert slot.claim() is None
        assert WarmSlot("other").claim()

    def test_an_old_fill_cannot_release_a_newer_ones_claim(self):
        """A task queued so long its claim lapsed, then a second reader queued another."""
        late = WarmSlot("k")
        late.claim()
        cache.delete("k")
        newer = WarmSlot("k")
        newer_token = newer.claim()
        assert late.superseded() is True
        late.report("stale stage")
        late.finish("stale failure")
        assert newer.state()["token"] == newer_token and newer.state()["message"] is None
        assert newer.outcome() is None

    def test_a_task_whose_claim_lapsed_with_no_successor_still_runs(self):
        late = WarmSlot("k")
        late.claim()
        cache.delete("k")
        assert late.superseded() is False
        with late.running():
            assert late.state()["running"] is True
        assert late.state() is None

    def test_a_claim_that_could_not_be_queued_does_not_stand(self):
        slot = WarmSlot("k")

        def broker_down(token):
            raise ConnectionError("no broker")

        with pytest.raises(ConnectionError):
            slot.queue_once(broker_down)
        assert slot.state() is None
        assert "no broker" in slot.take_outcome()["error"]

    def test_queue_once_queues_once_and_hands_over_the_token(self):
        tokens = []
        WarmSlot("k").queue_once(tokens.append)
        WarmSlot("k").queue_once(tokens.append)
        assert len(tokens) == 1 and WarmSlot("k").state()["token"] == tokens[0]

    def test_a_claim_from_before_this_format_reads_as_a_fill_under_way(self):
        cache.set("k", "warming", 60)
        assert WarmSlot("k").state() == {"message": None, "started_at": None}

    def test_running_releases_the_claim_and_leaves_the_outcome(self):
        slot = WarmSlot("k")
        slot.claim()
        with slot.running():
            assert slot.state()["running"] is True
        assert slot.state() is None
        assert slot.outcome() == {"error": None}
        assert slot.claim()

    def test_a_reported_failure_is_left_for_the_reader(self):
        slot = WarmSlot("k")
        with slot.running() as outcome:
            outcome["error"] = "no export"
        assert slot.take_outcome() == {"error": "no export"}

    def test_an_exception_is_reported_and_still_raised(self):
        slot = WarmSlot("k")
        with pytest.raises(ValueError):
            with slot.running():
                raise ValueError("boom")
        assert slot.outcome() == {"error": "boom"} and slot.state() is None

    def test_a_started_claim_lapses_without_a_heartbeat(self):
        """A task killed by a deploy must not strand its readers: the claim is short
        once started, and only the heartbeat keeps it."""
        slot = WarmSlot("k")
        with patch.object(cache, "set", wraps=cache.set) as written:
            with slot.running():
                assert written.call_args_list[0].args[2] == cache_warm.RUNNING_SECONDS
        assert cache_warm.RUNNING_SECONDS < cache_warm.QUEUED_SECONDS

    def test_the_heartbeat_keeps_a_long_walk_claimed(self, monkeypatch):
        monkeypatch.setattr(cache_warm, "HEARTBEAT_SECONDS", 0.01)
        slot = WarmSlot("k")
        import time

        # On the module, not the proxy: django's cache is per-thread, and this is a thread.
        spy = MagicMock(wraps=cache)
        monkeypatch.setattr(cache_warm, "cache", spy)
        with slot.running():
            slot.report("downloading")
            time.sleep(0.1)
            assert slot.state()["message"] == "downloading", "a beat must not undo a reported stage"
        assert spy.touch.call_count > 2, "the walk has nothing to tick from; the thread must"
        assert slot.state() is None, "a beat after the release must not take the claim back"

    def test_taking_a_failure_spends_it(self):
        slot = WarmSlot("k")
        slot.finish("no export")
        assert slot.take_outcome() == {"error": "no export"}
        assert slot.take_outcome() is None


class TestVisitWarm:
    def _run(self, *, report=None, raises=None, only=None, definition=None):
        wda = MagicMock()
        wda.get_definition.return_value = definition or SimpleNamespace(opportunity_ids=[523, 524], data={})
        ensure = MagicMock(return_value=report or {"failed": [], "opportunities": []}, side_effect=raises)
        with (
            patch("connect_labs.workflow.data_access.WorkflowDataAccess", return_value=wda),
            patch.object(visit_cache, "ensure_visit_cache", ensure),
        ):
            out = warm_visit_cache_in_background(
                "tok", definition_id=5, owner_opportunity_id=523, scope={"opportunity_id": 523}, only=only
            )
        wda.close.assert_called_once()
        return out, ensure

    def test_it_fills_the_workflows_cache_and_says_it_finished(self):
        _out, ensure = self._run()
        assert ensure.call_args.args[1] == 5
        assert ensure.call_args.kwargs["opportunity_id"] == 523
        assert "start_at" not in ensure.call_args.kwargs
        assert visit_warm_slot(5).outcome() == {"error": None}
        assert visit_warm_slot(5).state() is None

    def test_one_opportunity_is_a_window_of_one(self):
        _out, ensure = self._run(only=524)
        assert ensure.call_args.kwargs["start_at"] == 1 and ensure.call_args.kwargs["limit"] == 1
        assert visit_warm_slot(5, 524).outcome() == {"error": None}
        assert visit_warm_slot(5).outcome() is None, "one worker's fill is not the whole report's"

    def test_an_opportunity_removed_while_queued_downloads_nothing(self):
        """No window would mean every opportunity the workflow spans."""
        out, ensure = self._run(only=999)
        assert ensure.call_count == 0
        assert "999" in out["error"]
        assert "999" in visit_warm_slot(5, 999).outcome()["error"]

    def test_a_task_a_newer_fill_replaced_does_nothing(self):
        slot = visit_warm_slot(5)
        slot.claim()
        ensure = MagicMock()
        with patch.object(visit_cache, "ensure_visit_cache", ensure):
            out = warm_visit_cache_in_background(
                "tok", definition_id=5, owner_opportunity_id=523, scope={"opportunity_id": 523}, claim="older"
            )
        assert out == {"superseded": True} and ensure.call_count == 0
        assert slot.state() is not None

    def test_a_failed_opportunity_is_named_for_the_reader(self):
        report = {"failed": [523], "opportunities": [{"opportunity_id": 523, "error": "pipeline 9: no export"}]}
        out, _ensure = self._run(report=report)
        assert out == {"failed": [523]}
        assert visit_warm_slot(5).outcome() == {"error": "pipeline 9: no export"}

    def test_a_crash_reaches_the_reader_and_never_the_worker(self):
        out, _ensure = self._run(raises=RuntimeError("Connect export timed out"))
        assert out == {"error": "Connect export timed out"}
        assert visit_warm_slot(5).outcome() == {"error": "Connect export timed out"}
        assert visit_warm_slot(5).state() is None

    def test_a_reader_polling_mid_fill_sees_the_current_stage(self):
        seen = []

        def walk(*args, on_stage, **kwargs):
            on_stage("Loading visit data: downloading visits.")
            seen.append(visit_warm_slot(5).state()["message"])
            return {"failed": [], "opportunities": []}

        wda = MagicMock()
        with (
            patch("connect_labs.workflow.data_access.WorkflowDataAccess", return_value=wda),
            patch.object(visit_cache, "ensure_visit_cache", walk),
        ):
            warm_visit_cache_in_background(
                "tok", definition_id=5, owner_opportunity_id=523, scope={"opportunity_id": 523}
            )
        assert seen == ["Loading visit data: downloading visits."]
