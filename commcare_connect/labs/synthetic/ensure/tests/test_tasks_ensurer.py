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
