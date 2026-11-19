"""One live preview build per viewer and run, and its answer kept briefly (#2810).

A report page asks for its preview from several render fetchers at once. Each used to
run the whole build: on 2026-11-19 six side-by-side builds of one run held labs p95 at
20-31s for twenty minutes (`labs-jj-alb-latency-high`). These pin the COST: a request
that arrives while a build is running builds nothing, and a poll after it reads the
answer instead of building again.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.workflow import views

BUILT = {"payload": {"figures": [1, 2, 3]}, "opportunity_id": 10042, "opportunity_ids": [10042]}
WHOLE = {"cold_cache": False, "partial_cache": False}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _dao(state):
    class _DAO:
        opportunity_id = 10042
        program_id = None

        def __init__(self, *a, **k):
            pass

        def get_run(self, run_id, program_hint=None):
            return SimpleNamespace(
                id=run_id,
                definition_id=5,
                opportunity_id=10042,
                is_completed=False,
                snapshot=None,
                data={"definition_id": 5, "state": dict(state)},
            )

        def get_definition(self, definition_id):
            return SimpleNamespace(id=definition_id, opportunity_id=10042, opportunity_ids=[], data={})

        def close(self):
            pass

    return _DAO


class Harness:
    def __init__(self, monkeypatch, cache_report=WHOLE):
        self.monkeypatch = monkeypatch
        self.builds = 0
        self.state = {}
        self.during_build = None
        self.cache_report = cache_report
        monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.build_snapshot_for_run", self._build)
        monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.cache_state", lambda ids: self.cache_report)
        monkeypatch.setattr(views, "_warm_cache_on_read", lambda d: False)

    def _build(self, *a, **k):
        self.builds += 1
        if self.during_build is not None:
            hook, self.during_build = self.during_build, None
            hook()
        return BUILT

    def get(self, query="", user=7):
        self.monkeypatch.setattr(views, "WorkflowDataAccess", _dao(self.state))
        request = RequestFactory().get("/" + query, headers={"x-labs-sandbox-for": "5"})
        request.user = SimpleNamespace(is_authenticated=True, pk=user)
        request.session = {"labs_oauth": {"access_token": "tok"}}
        with patch("connect_labs.workflow.tasks.warm_visit_cache"):
            return views.preview_snapshot_api.__wrapped__.__wrapped__(request, run_id=1)


def test_a_request_during_a_build_builds_nothing_and_waits(monkeypatch):
    h = Harness(monkeypatch)
    seen = []
    h.during_build = lambda: seen.append(h.get())
    first = h.get()
    assert first.status_code == 200
    (second,) = seen
    assert second.status_code == 202, "a second fetcher must not start its own build"
    assert json.loads(second.content)["status"] == "warming"
    assert h.builds == 1


def test_the_poll_after_a_build_reads_its_answer(monkeypatch):
    h = Harness(monkeypatch)
    first = h.get()
    again = h.get()
    assert again.status_code == 200
    assert json.loads(again.content) == json.loads(first.content)
    assert h.builds == 1


def test_the_build_slot_is_free_once_the_build_ends(monkeypatch):
    h = Harness(monkeypatch)
    h.get()
    assert h.get("?refresh=1").status_code == 200
    assert h.builds == 2


def test_a_state_edit_is_a_new_answer(monkeypatch):
    h = Harness(monkeypatch)
    h.get()
    h.state = {"reviewed": True}
    assert h.get().status_code == 200
    assert h.builds == 2


def test_another_viewer_does_not_read_my_answer(monkeypatch):
    h = Harness(monkeypatch)
    h.get(user=7)
    h.get(user=8)
    assert h.builds == 2


def test_a_partial_answer_is_not_kept(monkeypatch):
    h = Harness(monkeypatch, cache_report={"cold_cache": False, "partial_cache": True})
    h.get()
    h.get()
    assert h.builds == 2


def test_a_failed_build_frees_the_slot(monkeypatch):
    h = Harness(monkeypatch)

    def boom(*a, **k):
        h.builds += 1
        raise RuntimeError("db went away")

    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.build_snapshot_for_run", boom)
    assert h.get().status_code == 500
    assert h.get().status_code == 500
    assert h.builds == 2, "a failed build must not leave later readers polling a dead claim"


def test_an_unreachable_cache_builds_as_before(monkeypatch):
    """django-redis runs with IGNORE_EXCEPTIONS: a down Redis claims nothing and holds nothing."""
    h = Harness(monkeypatch)
    monkeypatch.setattr(views.WarmSlot, "claim", lambda self, **k: None)
    monkeypatch.setattr(views.WarmSlot, "state", lambda self: None)
    assert h.get().status_code == 200
    assert h.builds == 1
