"""Completing a run's LAST audit session finalises the run the way the run page does.

ExperimentAuditCompleteView completes the linked workflow run once every one of
its sessions is completed. It used to do that with ``complete_run(snapshot={})``
for every workflow, so a saved-runs template whose snapshot is computed --
weekly_dual_track_audit's build_snapshot hook, which freezes ``audit_summary`` --
ended completed with an empty snapshot, and no cohort auto-publish or hand-down
was queued. A completed run is terminal, so the run page could not redo it.
"""

from unittest import mock

import pytest

from connect_labs.audit.views import ExperimentAuditCompleteView
from connect_labs.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord
from connect_labs.workflow.snapshot_runtime import SnapshotBuildError

RUN_ID = 55
DEFINITION_ID = 7
OPP_ID = 814


class _Session:
    def __init__(self, session_id, status, *, tag="muac", fid="flw_a", opportunity_id=OPP_ID, stats=None):
        self.id = session_id
        self.status = status
        self.tag = tag
        self.labs_record_id = RUN_ID
        self.opportunity_id = opportunity_id
        self.flw_username = fid
        self.flw_display_name = fid
        self.image_count = 10
        self._stats = stats or {"pass": 9, "fail": 1, "duplicate_fake": 0, "pending": 0, "ai_no_match": 1}

    def get_assessment_stats(self):
        return dict(self._stats)


def _run(state=None):
    return WorkflowRunRecord(
        {
            "id": RUN_ID,
            "experiment": "workflow",
            "type": "workflow_run",
            "data": {
                "definition_id": DEFINITION_ID,
                "status": "in_progress",
                "state": state or {"window_start": "2026-10-11", "window_end": "2026-10-17"},
            },
            "opportunity_id": OPP_ID,
        }
    )


def _definition(template_type="weekly_dual_track_audit"):
    return WorkflowDefinitionRecord(
        {
            "id": DEFINITION_ID,
            "experiment": "workflow",
            "type": "workflow_definition",
            "data": {
                "name": "Weekly Dual-Track Image Audit",
                "config": {"templateType": template_type},
                "opportunity_ids": [OPP_ID],
            },
            "opportunity_id": OPP_ID,
        }
    )


class _FakeWorkflowDataAccess:
    """Just enough of WorkflowDataAccess for the completion path."""

    def __init__(self, run, definition):
        self.run = run
        self.definition = definition
        self.access_token = "t"
        self.opportunity_id = OPP_ID
        self.program_id = None
        self.completed = []

    def get_run(self, run_id, **_):
        return self.run

    def get_definition(self, definition_id):
        return self.definition

    def get_cached_pipeline_data(self, *a, **k):
        return {}

    def get_workers(self, opportunity_id):
        return []

    def complete_run(self, run_id, snapshot, run=None):
        self.completed.append(snapshot)
        return mock.Mock(status="completed")

    def close(self):
        pass


@pytest.fixture
def queued(monkeypatch):
    calls = {"auto_publish": [], "hand_down": []}
    monkeypatch.setattr(
        "connect_labs.benchmarks.tasks.queue_auto_publish",
        lambda dao, **kw: calls["auto_publish"].append(kw) or True,
    )
    monkeypatch.setattr(
        "connect_labs.workflow.hand_down.queue_hand_down",
        lambda dao, **kw: calls["hand_down"].append(kw) or True,
    )
    return calls


def _complete_last_session(monkeypatch, wda, sessions):
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **kw: wda)
    data_access = mock.Mock()
    data_access.get_sessions_by_workflow_run.return_value = sessions
    ExperimentAuditCompleteView()._maybe_complete_workflow_run(mock.Mock(), data_access, sessions[-1])


def test_last_session_freezes_the_dual_track_audit_summary(monkeypatch, queued):
    """The real snapshot runtime and the real weekly_dual_track_audit hook: the run
    completes WITH its audit_summary, and is published / handed down after."""
    from connect_labs.workflow.templates import weekly_dual_track_audit as template

    sessions = [_Session(1, "completed", tag="muac"), _Session(2, "completed", tag="rest")]
    hook_dao = mock.Mock()
    hook_dao.get_sessions_by_workflow_run.return_value = sessions
    monkeypatch.setattr(template, "AuditDataAccess", lambda **kw: hook_dao)
    wda = _FakeWorkflowDataAccess(_run(), _definition())

    _complete_last_session(monkeypatch, wda, sessions)

    assert len(wda.completed) == 1
    snapshot = wda.completed[0]
    assert snapshot["completed_counts"] == {"total": 2, "incomplete": 0}
    assert snapshot["audit_summary"]["by_tag"]["muac"]["pass"] == 9
    assert snapshot["window_start"] == "2026-10-11"
    assert queued["auto_publish"] == [{"workflow_id": DEFINITION_ID, "run_id": RUN_ID}]
    assert queued["hand_down"] == [
        {"workflow_id": DEFINITION_ID, "run_id": RUN_ID, "template_type": "weekly_dual_track_audit"}
    ]


def test_a_run_without_a_completion_contract_still_completes_empty(monkeypatch, queued):
    """Action-shaped runs (bulk_image_audit: no saved-runs contract) keep the old
    behaviour: completed with an empty snapshot, nothing published."""
    wda = _FakeWorkflowDataAccess(_run(), _definition(template_type="bulk_image_audit"))

    _complete_last_session(monkeypatch, wda, [_Session(1, "completed")])

    assert wda.completed == [{}]
    assert queued == {"auto_publish": [], "hand_down": []}


@pytest.mark.parametrize(
    "failure",
    [
        SnapshotBuildError("cache_miss", "no cached data"),
        SnapshotBuildError("definition_not_found", "workflow definition 7 not found"),
        ValueError("1 of 3 audits still open"),
    ],
)
def test_a_snapshot_that_cannot_be_built_leaves_the_run_open(monkeypatch, queued, failure):
    """A contract exists but its snapshot cannot be built now (the hook's gate, an
    unreadable definition, a cold cache): leave the run in progress for the run
    page to finish, rather than freezing it empty for good."""
    wda = _FakeWorkflowDataAccess(_run(), _definition())

    def _raise(*a, **k):
        raise failure

    monkeypatch.setattr("connect_labs.workflow.snapshot_runtime.build_snapshot_for_run", _raise)

    _complete_last_session(monkeypatch, wda, [_Session(1, "completed")])

    assert wda.completed == []
    assert queued == {"auto_publish": [], "hand_down": []}


def test_an_open_session_still_blocks_completion(monkeypatch, queued):
    wda = _FakeWorkflowDataAccess(_run(), _definition())

    _complete_last_session(monkeypatch, wda, [_Session(1, "in_progress"), _Session(2, "completed")])

    assert wda.completed == []
