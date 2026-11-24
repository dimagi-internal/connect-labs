"""A history rebuild gives every week of the followed report a new run id: nothing staff decided may be lost.

And the reviews follow the report when it saves a run (``tasks.sync_after_save``), not only when the
page is next opened.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.apps import apps as django_apps

from connect_labs.audit_reviews import service, tasks
from connect_labs.audit_reviews.models import AuditSource, Decision, Review, SourceRun, TeamEvent
from connect_labs.audit_reviews.tests.test_service import CASES, FakeDao, _pending, kc_state, source_for

pytestmark = pytest.mark.django_db

CASE = CASES["go_live_first_run"]


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))


def _rebuilt(runs, offset=500):
    """The same weeks under new run ids, completed later: what workflow_rebuild_history does."""
    return [{**r, "run_id": r["run_id"] + offset} for r in runs]


class RebuiltDao(FakeDao):
    def list_runs(self, definition_id):
        out = super().list_runs(definition_id)
        for run in out:
            run.completed_at = f"{run.period_end}T23:59:00Z"
        return out


@pytest.fixture
def live():
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(CASE), d)
    return source, d


def _review(source, ent):
    return next(r for r in service.world(source)["reviews"] if r["ent"] == ent)


def test_decisions_and_events_survive_a_rebuild(live):
    source, d = live
    a, b, c = _pending(source, "open")[:3]
    service.record_decisions(source, [{"kind": "open", "entity": a}, {"kind": "open", "entity": b}], user=None, by="S")
    service.record_decisions(source, [{"kind": "dismiss", "entity": c, "note": "not yet"}], user=None, by="S")
    key = _review(source, a)["key"]
    service.record_event(
        source, review_key=key, event_id="e1", type="comment", payload={"text": "hi"}, user=None, by="S"
    )
    service.record_decisions(source, [{"kind": "close", "entity": b, "status": "closed_suspended"}], user=None, by="S")
    before = {r["ent"]: (r["id"], r["status"]) for r in service.world(source)["reviews"]}

    service.sync(source, RebuiltDao(CASE, runs=_rebuilt(CASE["D"]["runs"])), d)
    after = service.world(source)
    assert {r["ent"]: (r["id"], r["status"]) for r in after["reviews"]} == before
    assert not any(w.startswith("1 saved decision") or "no longer has a run" in w for w in after["warnings"])
    assert [e["payload"]["text"] for e in _review(source, a)["events"] if e.get("team")] == ["hi"]
    dismissed = next(p for p in after["latestProposals"] if p["ent"] == c)
    assert dismissed.get("decided") == "dismissed"
    assert after["goLiveIdx"] == len(after["runs"]) - 1, "go-live followed its week to the rebuilt run"


def test_a_decision_whose_week_is_gone_is_kept_and_named(live):
    source, d = live
    ent = _pending(source, "open")[0]
    service.record_decisions(source, [{"kind": "open", "entity": ent}], user=None, by="S")
    service.sync(source, FakeDao(CASE, runs=CASE["D"]["runs"][:-1]), d)  # the report drops that week
    payload = service.world(source)
    assert any("no longer has a run" in w for w in payload["warnings"])
    assert Decision.objects.filter(source=source, entity=ent).exists()


def test_a_close_closes_only_the_review_it_was_made_on(live):
    source, d = live
    ent = _pending(source, "open")[0]
    service.record_decisions(source, [{"kind": "open", "entity": ent}], user=None, by="S")
    first = _review(source, ent)["id"]
    service.record_decisions(
        source, [{"kind": "close", "entity": ent, "status": "closed_suspended"}], user=None, by="S"
    )
    close = Decision.objects.get(source=source, kind="close", entity=ent)
    assert close.closes_review == first
    # Pretend the close was recorded against a later review of the same entity: it must not apply here.
    close.closes_review = first + 50
    close.save()
    assert _review(source, ent)["status"] == "open"


def test_the_migration_fills_the_new_fields_for_what_was_saved_before():
    from importlib import import_module

    migration = import_module("connect_labs.audit_reviews.migrations.0002_week_keys")
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(CASE), d)
    ent = _pending(source, "open")[0]
    service.record_decisions(source, [{"kind": "open", "entity": ent}], user=None, by="S")
    key = _review(source, ent)["key"]
    service.record_event(
        source, review_key=key, event_id="e2", type="comment", payload={"text": "x"}, user=None, by="S"
    )
    service.record_decisions(
        source, [{"kind": "close", "entity": ent, "status": "closed_suspended"}], user=None, by="S"
    )
    number = _review(source, ent)["id"]
    # As rows saved before 0002 were:
    Decision.objects.filter(source=source).update(week="", closes_review=None)
    TeamEvent.objects.filter(source=source).update(review_number=None)
    AuditSource.objects.filter(pk=source.pk).update(go_live_week="")

    migration.tie_to_weeks_and_reviews(django_apps, None)

    week = service.week_of(SourceRun.objects.get(source=source, run_id=last))
    assert set(Decision.objects.filter(source=source).values_list("week", flat=True)) == {week}
    assert Decision.objects.get(source=source, kind="close").closes_review == number
    assert TeamEvent.objects.get(source=source).review_number == number
    assert AuditSource.objects.get(pk=source.pk).go_live_week == week


# ---------------------------------------------------------------------------
# After a save
# ---------------------------------------------------------------------------


def test_a_save_of_the_followed_report_queues_a_sync(live, monkeypatch):
    source, _ = live
    delay = MagicMock()
    monkeypatch.setattr(tasks.sync_after_save, "delay", delay)
    dao = SimpleNamespace(access_token="tok")
    assert tasks.queue_sync_after_save(dao, workflow_id=source.source_workflow_id) is True
    delay.assert_called_once_with("tok", workflow_id=source.source_workflow_id)
    assert tasks.queue_sync_after_save(dao, workflow_id=123456) is False  # nothing follows it
    assert tasks.queue_sync_after_save(SimpleNamespace(), workflow_id=source.source_workflow_id) is False  # no token


def test_queueing_never_breaks_the_save(monkeypatch):
    monkeypatch.setattr(tasks.sync_after_save, "delay", MagicMock(side_effect=RuntimeError("broker down")))
    AuditSource.objects.create(workflow_id=1, source_workflow_id=2)
    assert tasks.queue_sync_after_save(SimpleNamespace(access_token="t"), workflow_id=2) is False


def test_the_sync_after_a_save_reads_the_new_run_and_recomputes(live, monkeypatch):
    source, d = live
    runs = CASE["D"]["runs"]
    extra = {**runs[-1], "run_id": runs[-1]["run_id"] + 1, "period_start": "2026-12-28", "period_end": "2027-01-03"}
    dao = FakeDao(CASE, runs=runs + [extra])
    dao.get_definition = lambda workflow_id: d
    dao.close = lambda: None
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **kw: dao)
    report = tasks.sync_after_save("tok", workflow_id=source.source_workflow_id)
    assert report == [{"audit_workflow": source.workflow_id, "changed": True, "ok": True}]
    assert service.world(source)["runs"][-1]["run_id"] == extra["run_id"]
    assert Review.objects.filter(source=source).count() == len(service.world(source)["reviews"])


def test_a_saver_who_cannot_read_the_audit_workflow_syncs_nothing(live, monkeypatch):
    source, _ = live
    dao = MagicMock()
    dao.get_definition.return_value = None
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **kw: dao)
    report = tasks.sync_after_save("tok", workflow_id=source.source_workflow_id)
    assert "skipped" in report[0]
    dao.list_runs.assert_not_called()
