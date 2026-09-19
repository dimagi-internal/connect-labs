"""A cohort that follows a report republishes itself when the report saves a run.

Before this, `auto_publish_on_completion` was a column nothing read: the prod KMC
peers froze at whichever run someone last published by hand."""

from types import SimpleNamespace

import pytest

from connect_labs.benchmarks import auto_publish, tasks
from connect_labs.benchmarks.models import BenchmarkCohort

pytestmark = pytest.mark.django_db


def _cohort(name, *, source=19778, auto=True):
    return BenchmarkCohort.objects.create(
        name=name, organization_id="dimagi-kmc", source_workflow_id=source, auto_publish_on_completion=auto
    )


def _run(run_id=21114, workflow_id=19778, period_end="2026-09-20", completed_at="2026-09-18T13:42:00"):
    return SimpleNamespace(
        id=run_id, definition_id=workflow_id, is_completed=True, period_end=period_end, completed_at=completed_at
    )


def test_only_cohorts_that_follow_the_workflow_and_opted_in_republish(monkeypatch):
    follows = _cohort("follows")
    _cohort("opted out", auto=False)
    _cohort("another report", source=5456)
    published = []
    monkeypatch.setattr(
        auto_publish,
        "publish_run",
        lambda cohort, wda, workflow_id, run, **kw: published.append((cohort.pk, workflow_id, run.id))
        or SimpleNamespace(pk=1),
    )

    report = auto_publish.publish_for_completed_run(object(), _run())

    assert published == [(follows.pk, 19778, 21114)]
    assert report == [{"cohort_id": follows.pk, "publication_id": 1, "error": None}]


def test_one_failing_cohort_does_not_cost_the_others(monkeypatch):
    bad, good = _cohort("bad"), _cohort("good")

    def publish(cohort, *a, **kw):
        if cohort.pk == bad.pk:
            raise RuntimeError("history read timed out")
        return SimpleNamespace(pk=7)

    monkeypatch.setattr(auto_publish, "publish_run", publish)
    report = {r["cohort_id"]: r for r in auto_publish.publish_for_completed_run(object(), _run())}

    assert report[good.pk]["publication_id"] == 7
    assert "timed out" in report[bad.pk]["error"]


def test_after_a_rebuild_it_publishes_from_the_newest_run():
    _cohort("follows")
    runs = [
        _run(1, period_end="2026-09-06", completed_at="2026-09-12T00:00"),
        _run(2, period_end="2026-09-20", completed_at="2026-09-18T13:42"),
        _run(3, period_end="2026-09-13", completed_at="2026-09-18T15:32"),
        SimpleNamespace(id=4, definition_id=19778, is_completed=False, period_end="2026-09-27", completed_at=None),
    ]
    wda = SimpleNamespace(list_runs=lambda definition_id: runs)
    assert auto_publish.latest_completed_run(wda, 19778).id == 2, "newest PERIOD wins, not newest completion"


def test_a_save_queues_nothing_when_no_cohort_follows(monkeypatch):
    delayed = []
    monkeypatch.setattr(tasks.auto_publish_after_save, "delay", lambda *a, **kw: delayed.append((a, kw)))
    _cohort("another report", source=5456)

    assert tasks.queue_auto_publish(SimpleNamespace(access_token="t"), workflow_id=19778, run_id=1) is False
    assert delayed == []


def test_a_save_queues_the_republish_with_the_users_token_and_scope(monkeypatch):
    delayed = []
    monkeypatch.setattr(tasks.auto_publish_after_save, "delay", lambda *a, **kw: delayed.append((a, kw)))
    _cohort("follows")

    wda = SimpleNamespace(access_token="tok", opportunity_id=523, program_id=None)
    assert tasks.queue_auto_publish(wda, workflow_id=19778, run_id=21114) is True
    assert delayed == [(("tok",), {"run_id": 21114, "workflow_id": 19778, "opportunity_id": 523, "program_id": None})]


def test_queueing_never_raises_into_the_save(monkeypatch):
    _cohort("follows")

    def boom(*a, **kw):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.auto_publish_after_save, "delay", boom)
    assert tasks.queue_auto_publish(SimpleNamespace(access_token="t"), workflow_id=19778, run_id=1) is False
