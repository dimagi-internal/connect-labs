"""A live preview of a report that fills its own cache warms it on a miss.

The opportunity report streams no pipelines, so nothing else on its page would
ever fill the cache its preview reads; without this an in-progress run answers
"load the workflow's pipeline data first" to a reader who has no other page to
open.
"""

import json
from types import SimpleNamespace

from django.test import RequestFactory

from connect_labs.workflow import views
from connect_labs.workflow.snapshot_runtime import SnapshotBuildError


class _DAO:
    def __init__(self, *a, **k):
        pass

    def get_run(self, run_id, program_hint=None):
        return SimpleNamespace(id=run_id, definition_id=5, opportunity_id=10042, is_completed=False, snapshot=None)

    def get_definition(self, definition_id):
        return SimpleNamespace(
            id=definition_id, opportunity_id=10042, opportunity_ids=[], template_type="kmc_opp_report", data={}
        )

    def close(self):
        pass


def _call(monkeypatch, *, warm_on_read):
    attempts, warmed = [], []

    def build(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise SnapshotBuildError("cache_miss", "no cached data")
        return {"payload": {"state": {}}, "opportunity_id": 10042, "opportunity_ids": [10042]}

    monkeypatch.setattr(views, "WorkflowDataAccess", _DAO)
    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.build_snapshot_for_run", build)
    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.cache_state", lambda ids: {})
    monkeypatch.setattr(views, "_warm_cache_on_read", lambda d: warm_on_read)
    monkeypatch.setattr(views, "_warm_visit_cache", lambda *a, **k: warmed.append(a[2]))
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_authenticated=True)
    response = views.preview_snapshot_api.__wrapped__.__wrapped__(request, run_id=1)
    return response, attempts, warmed


def test_a_warm_on_read_report_warms_and_builds_again(monkeypatch):
    response, attempts, warmed = _call(monkeypatch, warm_on_read=True)
    assert response.status_code == 200, response.content
    assert json.loads(response.content)["source"] == "preview"
    assert len(attempts) == 2
    assert warmed == [[10042]], "the run's own opportunity was not warmed"


def test_any_other_report_still_says_the_cache_is_missing(monkeypatch):
    response, attempts, warmed = _call(monkeypatch, warm_on_read=False)
    assert response.status_code == 409
    assert json.loads(response.content)["code"] == "cache_miss"
    assert len(attempts) == 1 and not warmed
