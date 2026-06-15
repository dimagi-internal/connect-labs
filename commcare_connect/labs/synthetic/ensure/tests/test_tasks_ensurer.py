import datetime as dt

import pytest

from commcare_connect.labs.synthetic.ensure.engine import EnsureContext
from commcare_connect.labs.synthetic.ensure.ensurers.opp_data import ensure_opp_data
from commcare_connect.labs.synthetic.ensure.ensurers.run_audits import ensure_run_audits
from commcare_connect.labs.synthetic.ensure.ensurers.tasks import ensure_tasks
from commcare_connect.labs.synthetic.ensure.ensurers.weekly_runs import ensure_weekly_runs
from commcare_connect.labs.synthetic.ensure.env_manifest import (
    OppDataResource,
    RunAuditsResource,
    TasksResource,
    WeeklyRunsResource,
)
from commcare_connect.labs.synthetic.registry import invalidate_cache
from commcare_connect.tasks.data_access import TaskDataAccess
from commcare_connect.tasks.models import TaskRecord

OPP_ID = 10_073
SEED = 23

# One rockstar + one struggling persona; an anomaly flagging the struggling
# persona on week index 1 (a MUAC field-outlier) marked AUDITED, AND a
# coaching_arc for that same persona triggered on week 2 (1-based) with a
# bot/worker transcript. follow_up_outcome_week set => the loop closed.
MANIFEST_YAML = f"""
opportunity_id: {OPP_ID}
opportunity_name: PAR Tasks Opp
random_seed: {SEED}
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 8
    stddev: 2
flw_personas:
  - id: asha
    display_name: Asha Mensah
    archetype: rockstar
    accuracy_distribution: {{ mean: 0.93, stddev: 0.02 }}
    completeness_distribution: {{ mean: 0.95, stddev: 0.03 }}
    flag_rate: 0.02
  - id: dele
    display_name: Dele Okonkwo
    archetype: struggling
    accuracy_distribution: {{ mean: 0.62, stddev: 0.05 }}
    completeness_distribution: {{ mean: 0.70, stddev: 0.05 }}
    flag_rate: 0.30
beneficiary_cohorts:
  - id: primary
    size: 100
    field_distributions:
      "form.case.update.soliciter_muac_cm":
        distribution: normal
        mean: 13.5
        stddev: 1.8
    progression: improvement_curve
anomalies:
  - id: dele_cherry_pick_wk2
    type: field_outlier
    flw_ids: [dele]
    field_path: form.case.update.soliciter_muac_cm
    week: 1
    detection_path: muac_distribution
    reviewer_visible_in: [audit]
coaching_arcs:
  - flw_id: dele
    week_triggered: 2
    persona: supportive_coach
    target_behavior: Bad MUAC distribution
    follow_up_outcome_week: 3
    transcript:
      - role: bot
        text: "Hi {{flw_name}}, your MUAC readings this week look off — let's review them together."
        ts: 2026-02-09T11:00:00Z
      - role: flw
        text: "Okay, I think the tape slipped on a few children."
        ts: 2026-02-09T11:06:00Z
      - role: bot
        text: "Let's re-measure those tomorrow and resubmit clear photos."
        ts: 2026-02-09T11:09:00Z
      - role: flw
        text: "Understood, I will redo them."
        ts: 2026-02-09T11:14:00Z
image_config:
  good_image_count: 10
  bad_image_count: 10
  default_bad_rate: 0.1
  flw_bad_rates:
    dele: 0.6
kpi_config:
  - kpi: accuracy
    field_path: form.case.update.soliciter_muac_cm
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
"""


def _mondays(n: int, *, start: dt.date) -> list[str]:
    return [(start + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


def _setup_ctx(tmp_path):
    (tmp_path / "opp.yaml").write_text(MANIFEST_YAML)
    invalidate_cache()
    weeks = _mondays(3, start=dt.date(2026, 2, 2))  # 3 completed Mondays
    current = (dt.date(2026, 2, 2) + dt.timedelta(weeks=3)).isoformat()
    ctx = EnsureContext(env_dir=tmp_path, weeks=weeks, current_week=current)
    ensure_opp_data(OppDataResource(kind="opp_data", opportunity_id=OPP_ID, manifest="opp.yaml"), ctx)
    ensure_weekly_runs(
        WeeklyRunsResource(kind="weekly_runs", opportunity_ids=[OPP_ID], template="chc_nutrition_analysis"),
        ctx,
    )
    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)
    return ctx, weeks, current


def _tasks_for_run(opp_id, run_id) -> list[TaskRecord]:
    tda = TaskDataAccess(opportunity_id=opp_id, access_token="labs-only")
    try:
        return tda.get_tasks_for_run(run_id)
    finally:
        tda.close()


@pytest.mark.django_db
def test_creates_coaching_task_for_flagged_flw_on_arc_run(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)

    ensure_tasks(TasksResource(kind="tasks"), ctx)

    # The arc triggered on week 2 (1-based) -> week index 1.
    arc_week = weeks[1]
    arc_run_id = ctx.ids[f"run:{OPP_ID}:{arc_week}"]

    tasks = _tasks_for_run(OPP_ID, arc_run_id)
    dele_tasks = [t for t in tasks if t.data.get("username") == "dele"]
    assert len(dele_tasks) == 1, "expected exactly one coaching task for dele on the arc's run"
    task = dele_tasks[0]

    # Hero header reads Task.flw_name (data['flw_name'] or username) — assert via
    # the model property, the exact value the page renders.
    assert task.flw_name == "Dele Okonkwo"

    # The arc's OWN transcript messages are present in the coaching panel.
    convo = task.data.get("ocs_conversation")
    assert convo and len(convo) == 4, "the arc's 4 messages must be the coaching conversation"
    texts = [m["text"] for m in convo]
    assert any("MUAC readings this week look off" in t for t in texts)
    assert any("tape slipped on a few children" in t for t in texts)
    # The {flw_name} placeholder is filled with the real display name.
    assert any("Dele Okonkwo" in t for t in texts)
    assert [m["role"] for m in convo] == ["bot", "flw", "bot", "flw"]

    # Linked to the run-audit run_audits stashed for this (run, flw).
    expected_audit_id = ctx.ids[f"audit:{arc_run_id}:dele"]
    assert task.data.get("audit_session_id") == expected_audit_id

    # Creator is a real human name (the coaching manager), not the FLW's username.
    assert task.data.get("assigned_to_name") == "Asha Mensah"

    # follow_up_outcome_week set => the loop closed.
    assert task.status == "closed"

    # Stashed for the rollup ensurer's drill-target selection.
    assert ctx.ids[f"task:{arc_run_id}:dele"] == task.id


@pytest.mark.django_db
def test_rerun_is_idempotent(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    ensure_tasks(TasksResource(kind="tasks"), ctx)

    arc_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[1]}"]
    task_id_first = ctx.ids[f"task:{arc_run_id}:dele"]
    count_first = len(_tasks_for_run(OPP_ID, arc_run_id))

    # Re-run on a fresh ctx (manifest + runs + audits re-resolved to same ids).
    ctx2, _, _ = _setup_ctx(tmp_path)
    ensure_tasks(TasksResource(kind="tasks"), ctx2)

    count_second = len(_tasks_for_run(OPP_ID, arc_run_id))
    assert count_second == count_first, "re-run must not create duplicate tasks"
    assert ctx2.ids[f"task:{arc_run_id}:dele"] == task_id_first


@pytest.mark.django_db
def test_task_description_matches_arc_target_behavior(tmp_path):
    """The task description is overlaid from the arc's target_behavior so the
    task's title + description + transcript all describe ONE case (scene-14
    coherence) — not the generic archetype description."""
    ctx, weeks, _ = _setup_ctx(tmp_path)
    ensure_tasks(TasksResource(kind="tasks"), ctx)

    arc_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[1]}"]
    task = [t for t in _tasks_for_run(OPP_ID, arc_run_id) if t.data.get("username") == "dele"][0]

    # Description == the arc's target_behavior (the manifest's one-case label).
    assert task.data.get("description") == "Bad MUAC distribution"
    # Title is the manager-grammar phrasing of the same behavior.
    assert task.data.get("title") == "Coach Dele Okonkwo — bad MUAC distribution"


@pytest.mark.django_db
def test_reconciles_stale_investigating_task_to_closed_on_reuse(tmp_path):
    """A pre-existing seeded task in the WRONG state (investigating) is rebuilt
    in place to the arc's CURRENT state (closed, since follow_up_outcome_week is
    set) on reuse — same id, no duplicate. The stale-reuse bug: reuse must be
    keyed on (run, flw), then reconcile the archetype, not key on archetype and
    orphan the stale record."""
    from commcare_connect.labs.synthetic.walkthrough_kit import generate_task_from_archetype

    ctx, weeks, _ = _setup_ctx(tmp_path)
    arc_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[1]}"]

    # Seed a STALE investigating task for dele (the arc has actually resolved, so
    # the correct archetype is closed_satisfactory).
    tda = TaskDataAccess(opportunity_id=OPP_ID, access_token="labs-only")
    try:
        stale_id = generate_task_from_archetype(
            tda=tda,
            opportunity_id=OPP_ID,
            workflow_run_id=arc_run_id,
            audit_session_id=None,
            flw_id="dele",
            monday_iso=weeks[1],
            title="Coach Dele Okonkwo — bad MUAC distribution",
            task_archetype="investigating",
            creator_name="Asha Mensah",
            flw_name="Dele Okonkwo",
        )
        # Tag it as investigating so the ensurer sees an archetype MISMATCH.
        stale = tda.get_task(stale_id)
        stale.data["synthetic_archetype"] = "investigating"
        tda.save_task(stale)
    finally:
        tda.close()

    pre = [t for t in _tasks_for_run(OPP_ID, arc_run_id) if t.data.get("username") == "dele"]
    assert len(pre) == 1 and pre[0].status == "investigating"

    # Re-ensure: the stale task must be reconciled to closed, in place.
    ensure_tasks(TasksResource(kind="tasks"), ctx)

    after = [t for t in _tasks_for_run(OPP_ID, arc_run_id) if t.data.get("username") == "dele"]
    assert len(after) == 1, "reconcile must NOT mint a duplicate task"
    rebuilt = after[0]
    assert rebuilt.id == stale_id, "reconcile keeps the same record id"
    assert rebuilt.status == "closed", "resolved arc must reconcile the task to closed"
    assert rebuilt.data.get("synthetic_archetype") == "closed_satisfactory"
    # The arc transcript is re-overlaid on the rebuilt task.
    assert rebuilt.data.get("ocs_conversation"), "reconciled task keeps the arc transcript"
    assert ctx.ids[f"task:{arc_run_id}:dele"] == stale_id


@pytest.mark.django_db
def test_reconciles_stale_audit_to_completed_for_resolved_arc(tmp_path):
    """The companion audit-reconcile path: a flagged FLW whose arc RESOLVED must
    end with a COMPLETED audit (the grid's 'All resolved' week requires it). A
    stale in-progress audit seeded first is reconciled to completed on reuse."""
    from commcare_connect.audit.data_access import AuditDataAccess
    from commcare_connect.labs.synthetic.walkthrough_kit import generate_audit_from_archetype

    # Build the ctx WITHOUT run_audits so we can plant a stale audit first.
    (tmp_path / "opp.yaml").write_text(MANIFEST_YAML)
    invalidate_cache()
    weeks = _mondays(3, start=dt.date(2026, 2, 2))
    current = (dt.date(2026, 2, 2) + dt.timedelta(weeks=3)).isoformat()
    ctx = EnsureContext(env_dir=tmp_path, weeks=weeks, current_week=current)
    ensure_opp_data(OppDataResource(kind="opp_data", opportunity_id=OPP_ID, manifest="opp.yaml"), ctx)
    ensure_weekly_runs(
        WeeklyRunsResource(kind="weekly_runs", opportunity_ids=[OPP_ID], template="chc_nutrition_analysis"),
        ctx,
    )

    flagged_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[1]}"]
    ada = AuditDataAccess(opportunity_id=OPP_ID, access_token="labs-only")
    try:
        stale_id = generate_audit_from_archetype(
            ada=ada,
            opportunity_id=OPP_ID,
            opportunity_name="PAR Tasks Opp",
            workflow_run_id=flagged_run_id,
            flw_id="dele",
            monday_iso=weeks[1],
            audit_archetype="pending_all_clean",  # stale: in_progress
            visit_id=9_200_000,
            flw_name="Dele Okonkwo",
        )
        seeded = [s for s in ada.get_sessions_by_workflow_run(flagged_run_id) if s.id == stale_id]
        assert seeded and seeded[0].status == "in_progress"
    finally:
        ada.close()

    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)

    ada = AuditDataAccess(opportunity_id=OPP_ID, access_token="labs-only")
    try:
        sessions = [s for s in ada.get_sessions_by_workflow_run(flagged_run_id) if s.data.get("username") == "dele"]
        assert len(sessions) == 1, "reconcile must NOT mint a duplicate audit"
        assert sessions[0].id == stale_id
        assert sessions[0].status == "completed", "resolved arc must reconcile the audit to completed"
    finally:
        ada.close()
