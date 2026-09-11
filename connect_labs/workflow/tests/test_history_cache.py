"""A trend must not re-read every saved snapshot on every open.

`run_history_api` answers "one point per saved run" by fetching EVERY run of the
definition -- each carrying its whole snapshot -- and keeping a few hundred bytes
of each. A KMC snapshot is ~4.5 MB, so a report with 21 runs decoded ~95 MB to
read 21 numbers, per open, per viewer: 6-10 seconds of CPU on a 1-vCPU web task
(measured 2026-09-11), which everything else on that task then queued behind.

The projection changes only when a run is completed or deleted, so it is cached
and those writes invalidate it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory, override_settings

from connect_labs.workflow import history_cache

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@pytest.fixture(autouse=True)
def _clear_cache():
    with override_settings(CACHES=LOCMEM):
        cache.clear()
        yield
        cache.clear()


def _run(run_id, period_end, state, completed=True):
    run = MagicMock()
    run.id = run_id
    run.name = f"run {run_id}"
    run.opportunity_id = 523
    run.period_start = period_end
    run.period_end = period_end
    run.completed_at = f"{period_end}T00:00:00Z"
    run.is_completed = completed
    run.data = {"snapshot": {"state": state}}
    return run


def _request(**params):
    request = RequestFactory().get("/labs/workflow/api/19778/runs/history/", params)
    request.user = MagicMock(is_authenticated=True)
    request.labs_context = {"opportunity_id": params.get("opportunity_id")}
    return request


def _call(dao, **params):
    from connect_labs.workflow.views import run_history_api

    with patch("connect_labs.workflow.views.WorkflowDataAccess", return_value=dao):
        response = run_history_api(_request(**params), 19778)
    import json

    return json.loads(response.content)


def _dao(runs):
    dao = MagicMock()
    dao.list_runs.return_value = runs
    return dao


RUNS = [_run(1, "2026-09-06", {"snapshot": {"meta": {"as_of": "2026-09-06"}}})]


class TestTheProjectionIsServedFromCache:
    def test_the_second_open_reads_no_runs_at_all(self):
        dao = _dao(RUNS)
        first = _call(dao, keys="snapshot.meta", opportunity_id=523)
        second = _call(dao, keys="snapshot.meta", opportunity_id=523)
        assert dao.list_runs.call_count == 1, "the second open re-read every snapshot"
        assert second["runs"] == first["runs"] and second.get("cached") is True

    def test_different_requested_paths_are_different_answers(self):
        dao = _dao(RUNS)
        _call(dao, keys="snapshot.meta", opportunity_id=523)
        _call(dao, keys="snapshot.programInd", opportunity_id=523)
        assert dao.list_runs.call_count == 2

    def test_a_different_scope_is_a_different_answer(self):
        dao = _dao(RUNS)
        _call(dao, keys="snapshot.meta", opportunity_id=523)
        _call(dao, keys="snapshot.meta", opportunity_id=10042)
        assert dao.list_runs.call_count == 2

    def test_only_completed_runs_are_points(self):
        dao = _dao(RUNS + [_run(2, "2026-09-13", {}, completed=False)])
        assert [r["id"] for r in _call(dao, keys="snapshot.meta", opportunity_id=523)["runs"]] == [1]


class TestAWriteInvalidatesIt:
    def test_completing_a_run_makes_the_next_open_recompute(self):
        dao = _dao(RUNS)
        _call(dao, keys="snapshot.meta", opportunity_id=523)
        history_cache.invalidate(19778)
        _call(dao, keys="snapshot.meta", opportunity_id=523)
        assert dao.list_runs.call_count == 2

    def test_invalidating_one_definition_leaves_another_alone(self):
        dao = _dao(RUNS)
        _call(dao, keys="snapshot.meta", opportunity_id=523)
        history_cache.invalidate(5456)
        _call(dao, keys="snapshot.meta", opportunity_id=523)
        assert dao.list_runs.call_count == 1

    def test_invalidate_bumps_even_with_no_counter_yet(self):
        before = history_cache.version(19778)
        history_cache.invalidate(19778)
        assert history_cache.version(19778) > before

    def test_the_key_carries_the_version(self):
        key_before = history_cache.entry_key(19778, "opp523", ["a"])
        history_cache.invalidate(19778)
        assert history_cache.entry_key(19778, "opp523", ["a"]) != key_before


class TestTheWritePathsCallIt:
    def test_complete_run_invalidates_its_definition(self):
        from connect_labs.workflow.data_access import WorkflowDataAccess

        wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
        wda.labs_api = MagicMock()
        run = MagicMock(id=5, is_completed=False, data={"definition_id": 19778})
        run.definition_id = 19778
        wda.get_run = MagicMock(return_value=run)
        wda.labs_api.update_record.return_value = MagicMock(
            id=5, experiment="workflow", type="workflow_run", data={}, opportunity_id=523
        )
        with patch("connect_labs.workflow.history_cache.invalidate") as inv:
            wda.complete_run(5, {"state": {}})
        inv.assert_called_once_with(19778)

    def test_delete_run_invalidates_the_definition_it_belonged_to(self):
        from connect_labs.workflow.data_access import WorkflowDataAccess

        wda = WorkflowDataAccess.__new__(WorkflowDataAccess)
        wda.labs_api = MagicMock()
        run = MagicMock(id=5, opportunity_id=523)
        run.definition_id = 19778
        wda.get_run = MagicMock(return_value=run)
        wda.get_definition = MagicMock(return_value=MagicMock(opportunity_ids=[523]))
        wda._definition_opportunity_ids = MagicMock(return_value=[523])
        wda._scoped_audit_session_ids = MagicMock(return_value=[])
        with patch("connect_labs.workflow.history_cache.invalidate") as inv:
            wda.delete_run(5)
        inv.assert_called_once_with(19778)
