"""Completing a run's LAST audit session finalises the run the way the run page does.

ExperimentAuditCompleteView completes the linked workflow run once every one of
its sessions is completed. It used to do that with ``complete_run(snapshot={})``
for every workflow, so a saved-runs template whose snapshot is computed --
weekly_dual_track_audit's build_snapshot hook, which freezes ``audit_summary`` --
ended completed with an empty snapshot, and no cohort auto-publish or hand-down
was queued. A completed run is terminal, so the run page could not redo it.

It also writes the run's run-level state (avg_passed, flw_count, and phase /
completion once the run is done) on every session completion, before completing
the run -- the review page used to, from one session's numbers, after the run
was already completed.
"""

from types import SimpleNamespace
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
        self.data = {"visit_images": {"v1": [{"username": fid}]}}
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


def _definition(template_type="weekly_dual_track_audit", opportunity_ids=(OPP_ID,)):
    return WorkflowDefinitionRecord(
        {
            "id": DEFINITION_ID,
            "experiment": "workflow",
            "type": "workflow_definition",
            "data": {
                "name": "Weekly Dual-Track Image Audit",
                "config": {"templateType": template_type},
                "opportunity_ids": list(opportunity_ids),
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
        self.completed_runs = []
        self.state_writes = []
        self.get_run_hints = []

    def get_run(self, run_id, program_hint=None):
        self.get_run_hints.append(program_hint)
        return self.run

    def get_definition(self, definition_id):
        return self.definition

    def get_cached_pipeline_data(self, *a, **k):
        return {}

    def get_workers(self, opportunity_id):
        return []

    def update_run_state(self, run_id, new_state, run=None):
        self.state_writes.append(dict(new_state))
        data = {**run.data, "state": {**run.data.get("state", {}), **new_state}}
        updated = WorkflowRunRecord(
            {
                "id": run.id,
                "experiment": "workflow",
                "type": "workflow_run",
                "data": data,
                "opportunity_id": run.opportunity_id,
            }
        )
        updated.program_id = run.program_id
        return updated

    def complete_run(self, run_id, snapshot, run=None):
        self.completed.append(snapshot)
        self.completed_runs.append(run)
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


def _complete_last_session(monkeypatch, wda, sessions, post=None):
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", lambda **kw: wda)
    data_access = mock.Mock()
    data_access.get_sessions_by_workflow_run.return_value = sessions
    request = SimpleNamespace(POST=post or {})
    ExperimentAuditCompleteView()._maybe_complete_workflow_run(request, data_access, sessions[-1])


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


# -- Run-level state, written by the server (not the review page) ------------------


def test_each_session_completion_writes_the_runs_figures_not_its_own(monkeypatch, queued):
    """The review page wrote avg_passed / flw_count from the ONE session it had just
    completed, overwriting the run's figures each time. The server writes them over
    every session of the run instead -- and no phase until the run is done."""
    wda = _FakeWorkflowDataAccess(_run(), _definition())
    sessions = [
        _Session(1, "completed", fid="flw_a", stats={"pass": 9, "fail": 1}),  # 90%
        _Session(2, "in_progress", fid="flw_b", stats={"pass": 1, "fail": 1, "pending": 8}),  # 50%
        _Session(3, "completed", fid="flw_c", stats={"pass": 7, "fail": 1, "duplicate_fake": 2}),  # 70%
    ]

    _complete_last_session(monkeypatch, wda, sessions)

    assert wda.state_writes == [{"avg_passed": 70, "flw_count": 3}]
    assert wda.completed == []


def test_last_session_writes_the_run_state_before_completing_the_run(monkeypatch, queued):
    """The page's write for the last session always landed on a run this request had
    already completed, and was refused (a completed run's state is immutable). The
    server writes it first, and completes the run it just wrote."""
    wda = _FakeWorkflowDataAccess(_run(), _definition(template_type="bulk_image_audit"))
    sessions = [_Session(1, "completed", fid="flw_a"), _Session(2, "completed", fid="flw_b")]

    _complete_last_session(monkeypatch, wda, sessions)

    (written,) = wda.state_writes
    assert written["avg_passed"] == 90
    assert written["flw_count"] == 2
    assert written["phase"] == "completed"
    assert written["completion"]["completed_at"]
    (completed_run,) = wda.completed_runs
    assert completed_run.data["state"]["phase"] == "completed"
    assert completed_run.data["state"]["avg_passed"] == 90
    assert completed_run.data["state"]["window_start"] == "2026-10-11"  # existing state kept


def test_a_multi_opp_run_is_read_across_every_opportunity(monkeypatch, queued):
    """Sessions are filed per opportunity. The ambient read sees only this page's
    opportunity -- here, all done -- while another opportunity's are still open."""
    from connect_labs.audit import views

    other_opp = 822
    by_opp = {
        OPP_ID: [_Session(1, "completed", fid="flw_a")],
        other_opp: [_Session(2, "in_progress", fid="flw_b", opportunity_id=other_opp)],
    }

    def per_opp_access(*, opportunity_id=None, **_):
        dao = mock.Mock()
        dao.get_sessions_by_workflow_run.return_value = by_opp[opportunity_id]
        return dao

    monkeypatch.setattr(views, "AuditDataAccess", per_opp_access)
    wda = _FakeWorkflowDataAccess(_run(), _definition(opportunity_ids=(OPP_ID, other_opp)))

    _complete_last_session(monkeypatch, wda, by_opp[OPP_ID])

    assert wda.state_writes == [{"avg_passed": 90, "flw_count": 2}]
    assert wda.completed == []


def test_a_program_owned_run_is_found_with_the_owning_program_hint(monkeypatch, queued):
    """The review page is opportunity-scoped and cannot see a program-owned run, so
    the complete POST carries owning_program_id and the run's own work is done
    program-scoped."""
    program_run = _run()
    program_run.program_id = 217
    opp_wda = _FakeWorkflowDataAccess(program_run, None)
    program_wda = _FakeWorkflowDataAccess(program_run, _definition(template_type="bulk_image_audit"))
    program_wda.program_id = 217
    made = []

    def make_wda(**kw):
        made.append(kw)
        return program_wda if kw.get("program_id") == 217 else opp_wda

    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", make_wda)
    data_access = mock.Mock()
    session = _Session(1, "completed")
    data_access.get_sessions_by_workflow_run.return_value = [session]

    ExperimentAuditCompleteView()._maybe_complete_workflow_run(
        SimpleNamespace(POST={"owning_program_id": "217"}), data_access, session
    )

    assert opp_wda.get_run_hints == [217]
    assert made[-1]["program_id"] == 217
    assert program_wda.state_writes
    assert program_wda.completed == [{}]


def test_run_level_audit_state_rates_and_workers():
    from connect_labs.audit.data_access import run_level_audit_state

    unreviewed = _Session(9, "in_progress", fid="flw_a", stats={"pass": 0, "fail": 0, "pending": 5})
    assert run_level_audit_state([unreviewed]) == {"avg_passed": None, "flw_count": 1}

    # duplicate_fake counts against the rate, as on the review page; one worker in
    # two sessions (Track A + Track B) is one worker.
    combined = _Session(1, "completed", stats={"pass": 1, "fail": 0, "duplicate_fake": 1})  # 50%
    combined.data = {"visit_images": {"v1": [{"username": "flw_a"}, {"username": "flw_b"}], "v2": []}}
    same_worker_other_track = _Session(2, "completed", fid="flw_a", stats={"pass": 1, "fail": 0})  # 100%
    assert run_level_audit_state([combined, same_worker_other_track]) == {"avg_passed": 75, "flw_count": 2}

    # Rounded like the page's Math.round (half up), not Python's banker's round().
    assert run_level_audit_state([_Session(5, "completed", stats={"pass": 5, "fail": 3})])["avg_passed"] == 63
    assert run_level_audit_state([_Session(8, "completed", stats={"pass": 1, "fail": 7})])["avg_passed"] == 13
