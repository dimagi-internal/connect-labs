import datetime as dt

import pytest

from commcare_connect.labs.synthetic.ensure.engine import EnsureContext
from commcare_connect.labs.synthetic.ensure.ensurers.opp_data import ensure_opp_data
from commcare_connect.labs.synthetic.ensure.ensurers.rollup import ensure_rollup
from commcare_connect.labs.synthetic.ensure.ensurers.run_audits import ensure_run_audits
from commcare_connect.labs.synthetic.ensure.ensurers.tasks import ensure_tasks
from commcare_connect.labs.synthetic.ensure.ensurers.weekly_runs import ensure_weekly_runs
from commcare_connect.labs.synthetic.ensure.env_manifest import (
    OppDataResource,
    RollupResource,
    RunAuditsResource,
    TasksResource,
    WeeklyRunsResource,
)
from commcare_connect.labs.synthetic.registry import invalidate_cache
from commcare_connect.workflow.data_access import WorkflowDataAccess

# Opp A = "Northern" — COMPLETE: a flagged FLW with an audit + a CLOSED coaching
# task (the good/resolved cluster). Opp B = "Southern" — has a flagged FLW with an
# audit + an OPEN (investigating) coaching task, AND misses a week (week idx 0).
OPP_A = 10_081
OPP_B = 10_082
SEED = 41


def _manifest_yaml(opp_id: int, name: str, *, follow_up_outcome_week, flw_id: str, flw_name: str) -> str:
    fu = "" if follow_up_outcome_week is None else f"\n    follow_up_outcome_week: {follow_up_outcome_week}"
    return f"""
opportunity_id: {opp_id}
opportunity_name: {name}
random_seed: {SEED}
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 8
    stddev: 2
flw_personas:
  - id: rockstar_{opp_id}
    display_name: Asha Mensah
    archetype: rockstar
    accuracy_distribution: {{ mean: 0.93, stddev: 0.02 }}
    completeness_distribution: {{ mean: 0.95, stddev: 0.03 }}
    flag_rate: 0.02
  - id: {flw_id}
    display_name: {flw_name}
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
  - id: cherry_pick_wk2
    type: field_outlier
    flw_ids: [{flw_id}]
    field_path: form.case.update.soliciter_muac_cm
    week: 1
    detection_path: muac_distribution
    reviewer_visible_in: [audit]
coaching_arcs:
  - flw_id: {flw_id}
    week_triggered: 2
    persona: supportive_coach
    target_behavior: Bad MUAC distribution{fu}
    transcript:
      - role: bot
        text: "Hi {{flw_name}}, your MUAC readings look off — let's review."
        ts: 2026-02-09T11:00:00Z
      - role: flw
        text: "Okay, I think the tape slipped."
        ts: 2026-02-09T11:06:00Z
image_config:
  good_image_count: 10
  bad_image_count: 10
  default_bad_rate: 0.1
  flw_bad_rates:
    {flw_id}: 0.6
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
    # Opp A: arc closes (follow_up_outcome_week set) -> good cluster.
    (tmp_path / "a.yaml").write_text(
        _manifest_yaml(
            OPP_A, "Northern Region Nutrition", follow_up_outcome_week=3, flw_id="dele_a", flw_name="Dele Okonkwo"
        )
    )
    # Opp B: arc stays open (no outcome) -> incomplete cluster; misses week 0.
    (tmp_path / "b.yaml").write_text(
        _manifest_yaml(
            OPP_B, "Southern Region Nutrition", follow_up_outcome_week=None, flw_id="kofi_b", flw_name="Kofi Asare"
        )
    )
    invalidate_cache()
    weeks = _mondays(3, start=dt.date(2026, 2, 2))  # 3 completed Mondays
    current = (dt.date(2026, 2, 2) + dt.timedelta(weeks=3)).isoformat()
    ctx = EnsureContext(env_dir=tmp_path, weeks=weeks, current_week=current)

    for opp, fname in ((OPP_A, "a.yaml"), (OPP_B, "b.yaml")):
        ensure_opp_data(OppDataResource(kind="opp_data", opportunity_id=opp, manifest=fname), ctx)

    ensure_weekly_runs(
        WeeklyRunsResource(
            kind="weekly_runs",
            opportunity_ids=[OPP_A, OPP_B],
            template="chc_nutrition_analysis",
            missed_week_idxs={OPP_B: [0]},
        ),
        ctx,
    )
    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)
    ensure_tasks(TasksResource(kind="tasks"), ctx)
    return ctx, weeks, current


def _par_runs(opp_id):
    wda = WorkflowDataAccess(opportunity_id=opp_id, access_token="labs-only")
    try:
        return [
            r
            for r in wda.list_runs()
            if r.opportunity_id == opp_id
            and (r.data.get("definition_id") is not None)
            and r.data.get("snapshot", {}).get("state", {}).get("watched_summary") is not None
        ]
    finally:
        wda.close()


@pytest.mark.django_db
def test_creates_par_run_with_state_and_drill_vars(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)

    realized = ensure_rollup(
        RollupResource(kind="rollup", opportunity_ids=[OPP_A, OPP_B], template="program_admin_report"),
        ctx,
    )

    # ---- PAR run exists watching both opps with the required state keys ----
    par_run_id = realized["par_run_id"]
    par_def_id = realized["par_def_id"]
    # workflow_def_id is owned by weekly_runs (the PRIMARY chc def, not PAR's) —
    # the rollup must NOT clobber it.
    assert "workflow_def_id" not in realized
    assert realized["par_url"] == f"/labs/workflow/{par_def_id}/run/?run_id={par_run_id}&opportunity_id={OPP_A}"

    wda = WorkflowDataAccess(opportunity_id=OPP_A, access_token="labs-only")
    try:
        run = wda.get_run(par_run_id)
    finally:
        wda.close()
    assert run.data["status"] == "completed"
    state = run.data["state"]
    # Required state keys the template snapshot_inputs declares (run-level state).
    for key in ("window_start", "window_end", "watched_sources", "weeks"):
        assert key in state, f"missing run state key {key}"
    watched_opps = sorted(s["opportunity_id"] for s in state["watched_sources"])
    assert watched_opps == sorted([OPP_A, OPP_B]), "PAR must watch both opps"

    # Snapshot state carries the grid-driving keys the render reads.
    snap_state = run.data["snapshot"]["state"]
    for key in (
        "watched_summary",
        "window_start",
        "window_end",
        "expected_weeks",
        "display_window_start",
        "display_window_end",
    ):
        assert key in snap_state, f"missing snapshot state key {key}"
    assert snap_state["expected_weeks"] == weeks
    summary_opps = sorted(s["opportunity_id"] for s in snap_state["watched_summary"])
    assert summary_opps == sorted([OPP_A, OPP_B])

    # ---- drill-target vars present ----
    # good cluster = Opp A (closed task).
    assert realized["good_opp_id"] == OPP_A
    assert realized["good_opp_label"] == "Northern"
    assert realized["flagged_flw_good"] == "dele_a"
    assert realized["good_audit_id"] == ctx.ids[f"audit:{realized['good_run_id']}:dele_a"]
    assert realized["good_task_id"] == ctx.ids[f"task:{realized['good_run_id']}:dele_a"]
    assert realized["task_good_url"] == f"/tasks/{realized['good_task_id']}/edit/?opportunity_id={OPP_A}"
    assert realized["audit_good_url"] == f"/audit/{realized['good_audit_id']}/?opportunity_id={OPP_A}"

    # incomplete cluster = Opp B (investigating task).
    assert realized["incomplete_opp_id"] == OPP_B
    assert realized["incomplete_opp_label"] == "Southern"
    assert realized["flagged_flw_incomplete"] == "kofi_b"
    assert realized["incomplete_audit_id"] == ctx.ids[f"audit:{realized['incomplete_run_id']}:kofi_b"]
    assert realized["incomplete_task_id"] == ctx.ids[f"task:{realized['incomplete_run_id']}:kofi_b"]
    assert realized["task_incomplete_url"] == f"/tasks/{realized['incomplete_task_id']}/edit/?opportunity_id={OPP_B}"


@pytest.mark.django_db
def test_rerun_is_idempotent(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    realized_first = ensure_rollup(
        RollupResource(kind="rollup", opportunity_ids=[OPP_A, OPP_B], template="program_admin_report"),
        ctx,
    )
    par_run_id_first = realized_first["par_run_id"]
    count_first = len(_par_runs(OPP_A))

    # Re-run on a fresh ctx (manifest + runs + audits + tasks re-resolved to the
    # same ids), then re-roll up.
    ctx2, _, _ = _setup_ctx(tmp_path)
    realized_second = ensure_rollup(
        RollupResource(kind="rollup", opportunity_ids=[OPP_A, OPP_B], template="program_admin_report"),
        ctx2,
    )

    count_second = len(_par_runs(OPP_A))
    assert count_second == count_first, "re-run must not create a duplicate PAR run"
    assert realized_second["par_run_id"] == par_run_id_first, "the same PAR run must be reused"
    assert realized_second["par_def_id"] == realized_first["par_def_id"]


def test_select_drill_targets_uses_snapshot_audit_and_task_status(monkeypatch):
    """Regression for the scene-8 mismatch. A week whose coaching TASKS are closed
    but whose AUDITS are still in_progress renders 'N open' in the PAR grid
    (openCount counts audits too), so it must NOT be chosen as the `good`
    ('All resolved') drill week. `good` must be the week the grid marks resolved
    (every audit completed AND every task closed); `incomplete` an open week
    (an investigating task) in a different opp. Selection is from the SAME snapshot
    the report renders, so the realized vars can never disagree with the grid.
    """
    from commcare_connect.labs.synthetic.ensure.engine import EnsureContext
    from commcare_connect.labs.synthetic.ensure.ensurers import rollup as R

    class _M:
        def __init__(self, oid, name):
            self.opportunity_id, self.opportunity_name = oid, name

    ctx = EnsureContext(
        weeks=["2026-05-18", "2026-05-25"],
        ids={
            "run:10000:2026-05-18": 4233,
            "run:10000:2026-05-25": 4241,
            "run:10001:2026-05-18": 4269,
            "manifest:10000": _M(10000, "Northern Region"),
            "manifest:10001": _M(10001, "Southern Region"),
        },
    )
    snapshot = {
        "state": {
            "watched_summary": [
                {
                    "opportunity_id": 10000,
                    "runs": [
                        # May-18 CLEAN: audit completed + task closed -> grid "All resolved".
                        {
                            "id": 4233,
                            "flw_rows": [
                                {
                                    "flw_id": "hawa_n",
                                    "flags": [{"id": 1}, {"id": 2}],
                                    "audits": [{"id": 4237, "status": "completed"}],
                                    "tasks": [{"id": 4278, "status": "closed"}],
                                },
                            ],
                        },
                        # May-25 task closed BUT audit in_progress -> grid "1 open" (NOT good).
                        {
                            "id": 4241,
                            "flw_rows": [
                                {
                                    "flw_id": "hawa_n",
                                    "flags": [],
                                    "audits": [{"id": 4355, "status": "in_progress"}],
                                    "tasks": [{"id": 4363, "status": "closed"}],
                                },
                            ],
                        },
                    ],
                },
                {
                    "opportunity_id": 10001,
                    "runs": [
                        {
                            "id": 4269,
                            "flw_rows": [
                                {
                                    "flw_id": "ola_s",
                                    "flags": [],
                                    "audits": [{"id": 4358, "status": "in_progress"}],
                                    "tasks": [{"id": 4366, "status": "investigating"}],
                                },
                            ],
                        },
                    ],
                },
            ]
        }
    }

    good, incomplete = R._select_drill_targets(ctx, snapshot)
    assert good is not None and good["run_id"] == 4233, "good = the All-resolved week, not the in-progress-audit one"
    assert good["opp_id"] == 10000 and good["audit_id"] == 4237 and good["task_id"] == 4278
    assert incomplete is not None and incomplete["run_id"] == 4269, "incomplete = the other opp's open week"
    assert incomplete["opp_id"] == 10001

    # Each cluster carries its week's Monday + the grid-formatted date label, so a
    # scene can click the EXACT incomplete-week cell ("May 18") rather than the
    # fragile "first cell that says open".
    assert incomplete["monday"] == "2026-05-18"
    assert R._fmt_week_label(incomplete["monday"]) == "May 18"
    assert R._fmt_week_label(good["monday"]) == "May 18"
    assert R._fmt_week_label("2026-06-01") == "Jun 1"  # no leading zero, matches fmtDate
