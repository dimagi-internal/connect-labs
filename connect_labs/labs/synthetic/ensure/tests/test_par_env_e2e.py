"""PAR env end-to-end + idempotency — the real Program Admin Report manifests.

Unlike ``test_cli.py`` (which builds a small *synthetic* 2-opp env inline), this
test drives the REAL, checked-in PAR env manifest
(``envs/program-admin-report.yaml`` + its two per-opp manifests
``manifests/par-northern.yaml`` / ``par-southern.yaml``) through the full
five-ensurer chain via :func:`ensure_synthetic_data`. It is the proof that the
real manifests Task 10 authored produce a coherent PAR demo: both opps
registered, weekly chc runs (Southern missing a week), run-linked completable
audits, coaching tasks with real names + transcripts, a cross-opp PAR rollup,
and EVERY ``${...}`` var the walkthrough spec interpolates present + non-empty.

The env's timeline is dynamic (``completed_weeks: 4`` from today +
``include_current_week``), so the window is computed at run time — the test
asserts the SHAPE (counts, ids, cluster assignment), not absolute dates.

Resolves the env path off the ``connect_labs`` package dir so it runs from
any cwd, and calls ``ensure_synthetic_data`` in-process so it shares the
``django_db`` transaction with the assertions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import connect_labs
from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.labs.synthetic.ensure.engine import ensure_synthetic_data
from connect_labs.labs.synthetic.registry import get_synthetic_opp, invalidate_cache
from connect_labs.tasks.data_access import TaskDataAccess
from connect_labs.workflow.data_access import WorkflowDataAccess

# --- The real, checked-in PAR env manifest. ---
_PKG_DIR = Path(connect_labs.__file__).resolve().parent
ENV_PATH = _PKG_DIR / "labs" / "synthetic" / "envs" / "program-admin-report.yaml"

GOOD_OPP = 10_000  # Northern — complete, both flagged FLWs coached to a close.
INCOMPLETE_OPP = 10_001  # Southern — misses week 1; carries the open investigation.
COMPLETED_WEEKS = 4  # env timeline.completed_weeks
SOUTHERN_MISSED = {1}  # env weekly_runs.missed_week_idxs[10001]

# Labs-only opps short-circuit to the in-process local-records backend; the token
# is never sent anywhere (mirrors the ensurers).
_LABS_ONLY_TOKEN = "labs-only"  # noqa: S105 (not a secret)

# Every ${...} var the PAR walkthrough spec references. The spec
# (docs/walkthroughs/program-admin-report.yaml) interpolates a subset directly,
# but the full contract — par_*, the good_*/incomplete_* drill clusters, the
# manager-flow FLW, the chc def/run/url vars — is what the recorder + spec build
# on. Each must be present AND non-empty in the realized map, or a responsible
# ensurer has a gap. (Hardcoded from the task spec / regenerate.py contract
# rather than re-grepped at runtime so the test states the contract explicitly.)
REQUIRED_VARS = [
    "par_run_id",
    "par_def_id",
    "par_url",
    "good_run_id",
    "good_audit_id",
    "good_task_id",
    "good_opp_id",
    "good_opp_label",
    "good_week_idx",
    "flagged_flw_good",
    "chc_good_url",
    "audit_good_url",
    "task_good_url",
    "incomplete_run_id",
    "incomplete_audit_id",
    "incomplete_task_id",
    "incomplete_opp_id",
    "incomplete_opp_label",
    "incomplete_week_idx",
    "flagged_flw_incomplete",
    "audit_incomplete_url",
    "task_incomplete_url",
    "flagged_flw_manager",
    "workflow_def_id",
    "opp_id",
    "wk4_run_id",
    "wk4_url",
]


def _completed_runs(wda, definition_id):
    return [r for r in wda.list_runs(definition_id=definition_id) if r.data.get("status") == "completed"]


def _in_progress_runs(wda, definition_id):
    return [r for r in wda.list_runs(definition_id=definition_id) if r.data.get("status") == "in_progress"]


def _chc_def_id(realized, opp_id):
    return realized[f"workflow_def_id:{opp_id}"]


@pytest.mark.django_db
def test_par_env_end_to_end_and_idempotent(tmp_path):
    invalidate_cache()
    out = tmp_path / "realized.json"

    realized = ensure_synthetic_data(str(ENV_PATH), out=str(out))

    # ------------------------------------------------------------------ #
    # 1. Both opps registered as enabled labs-only synthetic opportunities.
    # ------------------------------------------------------------------ #
    for opp_id, label in ((GOOD_OPP, "Northern Cluster"), (INCOMPLETE_OPP, "Southern Cluster")):
        opp = get_synthetic_opp(opp_id)
        assert opp is not None, f"opp {opp_id} not registered"
        assert opp.labs_only is True
        assert opp.enabled is True
        assert opp.label == label
        assert realized[f"opp_{opp_id}_ready"] is True

    # ------------------------------------------------------------------ #
    # 2. Per opp: a chc def + N completed weekly runs + 1 in_progress run.
    #    Northern runs every completed week (4); Southern misses week 1 (3).
    # ------------------------------------------------------------------ #
    expected_completed = {
        GOOD_OPP: COMPLETED_WEEKS,
        INCOMPLETE_OPP: COMPLETED_WEEKS - len(SOUTHERN_MISSED),
    }
    completed_run_ids = {}  # opp -> sorted list of completed run ids (idempotency baseline)
    first_in_progress = {}  # opp -> the single in_progress run id (current-week reset baseline)
    for opp_id in (GOOD_OPP, INCOMPLETE_OPP):
        wda = WorkflowDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            def_id = _chc_def_id(realized, opp_id)
            completed = _completed_runs(wda, def_id)
            in_progress = _in_progress_runs(wda, def_id)
            assert (
                len(completed) == expected_completed[opp_id]
            ), f"opp {opp_id}: expected {expected_completed[opp_id]} completed runs, got {len(completed)}"
            assert len(in_progress) == 1, f"opp {opp_id}: expected exactly one in_progress (current-week) run"
            completed_run_ids[opp_id] = sorted(r.id for r in completed)
            first_in_progress[opp_id] = in_progress[0].id
        finally:
            wda.close()

    # ------------------------------------------------------------------ #
    # 3. Audits: the GOOD week's audit is COMPLETED (its coaching arc closed, so
    #    the grid renders "All resolved" — which requires the audit completed, not
    #    just the task closed). The INCOMPLETE week's audit is still in_progress
    #    AND shows a genuine MIX — some photos decided (pass/fail), some still
    #    pending — the scene-13 "still in review" drill (not "5 pending / 0
    #    decided"). "mix" == at least one decided photo AND at least one pending.
    # ------------------------------------------------------------------ #
    good_audit_id = realized["good_audit_id"]
    incomplete_audit_id = realized["incomplete_audit_id"]
    for opp_id, run_id, audit_id, flw, expected_status, expect_mix in (
        (GOOD_OPP, realized["good_run_id"], good_audit_id, realized["flagged_flw_good"], "completed", False),
        (
            INCOMPLETE_OPP,
            realized["incomplete_run_id"],
            incomplete_audit_id,
            realized["flagged_flw_incomplete"],
            "in_progress",
            True,
        ),
    ):
        ada = AuditDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            sessions = {s.id: s for s in ada.get_sessions_by_workflow_run(run_id)}
            assert audit_id in sessions, f"audit {audit_id} not linked to run {run_id} (opp {opp_id})"
            session = sessions[audit_id]
            # Run-linked: workflow_run_id resolves back to the run.
            assert session.workflow_run_id == run_id
            assert session.data.get("username") == flw
            assert session.status == expected_status, f"audit {audit_id} status {session.status} != {expected_status}"
            decided = [v for v in session.visit_results.values() if v.get("result")]
            pending = [v for v in session.visit_results.values() if not v.get("result")]
            if expected_status == "completed":
                assert decided, f"completed audit {audit_id} should have decided photos"
            if expect_mix:
                # The in-review MIX: a reviewer mid-decision — some cleared/failed,
                # some still pending. Both sides must be non-empty.
                assert decided, f"in-review audit {audit_id} should have ≥1 decided photo (the mix)"
                assert pending, f"in-review audit {audit_id} should have ≥1 pending photo (the mix)"
        finally:
            ada.close()

    # ------------------------------------------------------------------ #
    # 4. Coaching tasks exist with real flw_name + the arc transcript.
    # ------------------------------------------------------------------ #
    for opp_id, run_id, task_id, flw, expected_status in (
        (GOOD_OPP, realized["good_run_id"], realized["good_task_id"], realized["flagged_flw_good"], "closed"),
        (
            INCOMPLETE_OPP,
            realized["incomplete_run_id"],
            realized["incomplete_task_id"],
            realized["flagged_flw_incomplete"],
            "investigating",
        ),
    ):
        tda = TaskDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            task = tda.get_task(task_id)
            assert task is not None, f"task {task_id} missing (opp {opp_id})"
            assert task.workflow_run_id == run_id
            assert task.task_username == flw
            assert task.status == expected_status
            # Real human flw_name (not the raw username id).
            assert task.flw_name, f"task {task_id} has empty flw_name"
            assert task.flw_name != flw, f"task {task_id} flw_name fell back to the username id"
            # The arc transcript is present as the coaching conversation.
            conversation = task.data.get("ocs_conversation") or []
            assert conversation, f"task {task_id} has no ocs_conversation transcript"
            assert any(m.get("role") == "bot" for m in conversation)
            assert any(m.get("role") == "flw" for m in conversation)
        finally:
            tda.close()

    # ------------------------------------------------------------------ #
    # 5. The PAR rollup run watches BOTH opps with the required state keys.
    # ------------------------------------------------------------------ #
    par_def_id = realized["par_def_id"]
    par_run_id = realized["par_run_id"]
    par_wda = WorkflowDataAccess(opportunity_id=GOOD_OPP, access_token=_LABS_ONLY_TOKEN)
    try:
        par_run = par_wda.get_run(par_run_id)
        assert par_run is not None
        assert par_run.data.get("status") == "completed"
        # The run's top-level state carries the window inputs (window_start/end,
        # watched_sources, weeks); the snapshot's state carries the frozen rollup
        # output + grid keys (watched_summary, expected_weeks, display_window_*).
        # This split mirrors the production program_admin_demo seeder exactly —
        # the snapshot_inputs.state_keys manifest declares the union of both.
        state = par_run.data.get("state", {})
        watched_opp_ids = sorted(s.get("opportunity_id") for s in state.get("watched_sources", []))
        assert watched_opp_ids == [GOOD_OPP, INCOMPLETE_OPP]
        for key in ("window_start", "window_end", "watched_sources", "weeks"):
            assert key in state, f"PAR run state missing window key {key!r}"
        snapshot_state = par_run.data.get("snapshot", {}).get("state", {})
        for key in (
            "watched_summary",
            "window_start",
            "window_end",
            "watched_sources",
            "expected_weeks",
            "display_window_start",
            "display_window_end",
        ):
            assert key in snapshot_state, f"PAR snapshot missing required state key {key!r}"
    finally:
        par_wda.close()
    # PAR def is distinct from the chc defs the weekly_runs ensurer owns.
    assert par_def_id != realized["workflow_def_id"]

    # ------------------------------------------------------------------ #
    # 6. Cluster assignment: good == Northern (10000), incomplete == Southern.
    # ------------------------------------------------------------------ #
    assert realized["good_opp_id"] == GOOD_OPP
    assert realized["incomplete_opp_id"] == INCOMPLETE_OPP

    # ------------------------------------------------------------------ #
    # 7. Every ${...} walkthrough var is present + non-empty.
    # ------------------------------------------------------------------ #
    missing = [v for v in REQUIRED_VARS if v not in realized]
    assert not missing, f"realized map is MISSING required walkthrough vars: {missing}"
    empty = [v for v in REQUIRED_VARS if not realized.get(v) and realized.get(v) != 0]
    assert not empty, f"realized map has EMPTY required walkthrough vars: {empty}"

    # realized.json round-trips to the same headline map.
    on_disk = json.loads(out.read_text())
    assert on_disk["par_run_id"] == par_run_id
    for v in REQUIRED_VARS:
        assert on_disk.get(v) == realized.get(v)

    # ------------------------------------------------------------------ #
    # 8. Idempotency: a second ensure does not duplicate or churn ids.
    # ------------------------------------------------------------------ #
    invalidate_cache()
    out2 = tmp_path / "realized2.json"
    realized2 = ensure_synthetic_data(str(ENV_PATH), out=str(out2))

    # PAR run id is stable (reused, not re-minted).
    assert realized2["par_run_id"] == par_run_id
    assert realized2["par_def_id"] == par_def_id

    # No duplication of runs / audits / tasks; completed run ids stable; the
    # current-week in_progress run was RESET (reset: true) — exactly one remains
    # per opp, and its id changed.
    for opp_id in (GOOD_OPP, INCOMPLETE_OPP):
        wda = WorkflowDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            def_id = _chc_def_id(realized2, opp_id)
            completed = _completed_runs(wda, def_id)
            in_progress = _in_progress_runs(wda, def_id)
            assert (
                len(completed) == expected_completed[opp_id]
            ), f"opp {opp_id}: completed runs duplicated on re-run ({len(completed)})"
            assert (
                sorted(r.id for r in completed) == completed_run_ids[opp_id]
            ), f"opp {opp_id}: completed run ids churned on re-run"
            assert len(in_progress) == 1, f"opp {opp_id}: current-week run duplicated on re-run"
            assert (
                in_progress[0].id != first_in_progress[opp_id]
            ), f"opp {opp_id}: current-week run id did NOT change despite reset: true"
        finally:
            wda.close()

    # No duplicate audits / tasks on the drill-target runs.
    for opp_id, run_id in (
        (GOOD_OPP, realized2["good_run_id"]),
        (INCOMPLETE_OPP, realized2["incomplete_run_id"]),
    ):
        ada = AuditDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        tda = TaskDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            flw = realized2["flagged_flw_good"] if opp_id == GOOD_OPP else realized2["flagged_flw_incomplete"]
            run_audits = [s for s in ada.get_sessions_by_workflow_run(run_id) if s.data.get("username") == flw]
            assert len(run_audits) == 1, f"opp {opp_id} run {run_id}: audit duplicated on re-run ({len(run_audits)})"
            run_tasks = [t for t in tda.get_tasks_for_run(run_id) if t.task_username == flw]
            assert len(run_tasks) == 1, f"opp {opp_id} run {run_id}: task duplicated on re-run ({len(run_tasks)})"
        finally:
            ada.close()
            tda.close()

    # Drill-target ids are stable across re-runs (completed-week targets don't move).
    for v in (
        "good_run_id",
        "good_audit_id",
        "good_task_id",
        "incomplete_run_id",
        "incomplete_audit_id",
        "incomplete_task_id",
    ):
        assert realized2[v] == realized[v], f"drill-target var {v} changed on re-run"
