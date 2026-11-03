"""A cold cache is filled off the request, and the reader is told to wait.

The opportunity report streams no pipelines, so nothing else on its page would ever
fill the cache its preview reads. It used to fill it inside this request: on 2026-11-03
two viewers of one report held a web task at 100% for most of 17 minutes (#2810). Now the
request queues the fill and answers 202 at once, and cache_warm.py's protocol does the rest.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.workflow import views
from connect_labs.workflow.snapshot_runtime import SnapshotBuildError
from connect_labs.workflow.visit_cache import visit_warm_slot

DEFINITION = 5


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class _DAO:
    opportunity_id = 10042
    program_id = None

    def __init__(self, *a, **k):
        pass

    def get_run(self, run_id, program_hint=None):
        return SimpleNamespace(
            id=run_id, definition_id=DEFINITION, opportunity_id=10042, is_completed=False, snapshot=None
        )

    def get_definition(self, definition_id):
        return SimpleNamespace(
            id=definition_id, opportunity_id=10042, opportunity_ids=[], template_type="kmc_opp_report", data={}
        )

    def close(self):
        pass


BUILT = {"payload": {"state": {}}, "opportunity_id": 10042, "opportunity_ids": [10042]}


def _miss(*a, **k):
    raise SnapshotBuildError("cache_miss", "no cached data")


def _call(monkeypatch, *, build=_miss, warm_on_read=True, cache_report=None, headers=None):
    """One poll. Returns the response, how many builds ran, and the queued task."""
    builds = []

    def counted(*a, **k):
        builds.append(1)
        return build(*a, **k)

    monkeypatch.setattr(views, "WorkflowDataAccess", _DAO)
    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.build_snapshot_for_run", counted)
    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.cache_state", lambda ids: cache_report or {})
    monkeypatch.setattr(views, "_warm_cache_on_read", lambda d: warm_on_read)
    request = RequestFactory().get("/", headers=headers or {"x-labs-sandbox-for": "5"})
    request.user = SimpleNamespace(is_authenticated=True, pk=7)
    request.session = {"labs_oauth": {"access_token": "tok"}}
    with patch("connect_labs.workflow.tasks.warm_visit_cache") as task:
        response = views.preview_snapshot_api.__wrapped__.__wrapped__(request, run_id=1)
    return response, len(builds), task


def test_a_cold_report_queues_the_fill_and_answers_at_once(monkeypatch):
    response, builds, task = _call(monkeypatch)
    assert response.status_code == 202
    body = json.loads(response.content)
    assert body["status"] == "warming" and body["retry_after_ms"] > 0
    assert builds == 1
    assert task.delay.call_count == 1
    kwargs = task.delay.call_args.kwargs
    assert kwargs["definition_id"] == DEFINITION and kwargs["owner_opportunity_id"] == 10042
    assert kwargs["scope"] == {"opportunity_id": 10042} and kwargs["user_id"] == 7
    assert task.delay.call_args.args == ("tok",)


def test_a_poll_during_the_fill_builds_nothing_and_queues_nothing(monkeypatch):
    """The poll is every few seconds, and a second viewer joins the first's fill."""
    _call(monkeypatch)
    visit_warm_slot(DEFINITION).report("Loading visit data: downloading visits.")
    response, builds, task = _call(monkeypatch)
    assert response.status_code == 202
    assert builds == 0 and task.delay.call_count == 0
    assert json.loads(response.content)["message"] == "Loading visit data: downloading visits."


def test_a_filled_cache_answers_with_the_figures(monkeypatch):
    response, builds, task = _call(monkeypatch, build=lambda *a, **k: BUILT)
    assert response.status_code == 200
    assert json.loads(response.content)["source"] == "preview"
    assert builds == 1 and task.delay.call_count == 0


def test_a_fill_that_found_nothing_stops_the_polling_and_says_why(monkeypatch):
    visit_warm_slot(DEFINITION).finish("Connect export timed out")
    response, _builds, task = _call(monkeypatch)
    assert response.status_code == 502
    assert "Connect export timed out" in json.loads(response.content)["error"]
    assert task.delay.call_count == 0


def test_the_readers_retry_after_a_failure_really_tries_again(monkeypatch):
    visit_warm_slot(DEFINITION).finish("Connect export timed out")
    _call(monkeypatch)
    response, _builds, task = _call(monkeypatch)
    assert response.status_code == 202 and task.delay.call_count == 1


def test_a_builder_that_builds_over_a_cold_cache_waits_for_the_fill(monkeypatch):
    """Without this a report that reads no pipelines answers every figure zero."""
    response, _builds, task = _call(
        monkeypatch, build=lambda *a, **k: BUILT, cache_report={"cold_cache": True, "partial_cache": False}
    )
    assert response.status_code == 202 and task.delay.call_count == 1


def test_an_opportunity_with_no_visits_does_not_poll_forever(monkeypatch):
    """Cold is what it will always be: a fill that finished is the end of the wait."""
    visit_warm_slot(DEFINITION).finish(None)
    response, _builds, task = _call(
        monkeypatch, build=lambda *a, **k: BUILT, cache_report={"cold_cache": True, "partial_cache": False}
    )
    assert response.status_code == 200 and task.delay.call_count == 0


def test_a_partial_cache_answers_now_and_fills_behind_it(monkeypatch):
    response, _builds, task = _call(
        monkeypatch, build=lambda *a, **k: BUILT, cache_report={"cold_cache": False, "partial_cache": True}
    )
    assert response.status_code == 200 and task.delay.call_count == 1


def test_any_other_report_still_says_the_cache_is_missing(monkeypatch):
    response, builds, task = _call(monkeypatch, warm_on_read=False)
    assert response.status_code == 409
    assert json.loads(response.content)["code"] == "cache_miss"
    assert builds == 1 and task.delay.call_count == 0


def test_a_reader_that_cannot_poll_is_told_to_reload(monkeypatch):
    """LABS_WORKFLOW_SANDBOX=off: the render reads `error`, and it must say something true."""
    response, _builds, _task = _call(monkeypatch, headers={"accept": "application/json"})
    assert response.status_code == 202
    assert "Reload" in json.loads(response.content)["error"]
