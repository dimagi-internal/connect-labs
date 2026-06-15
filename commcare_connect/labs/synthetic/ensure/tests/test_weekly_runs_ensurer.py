import datetime as dt

import pytest

from commcare_connect.labs.synthetic.ensure.engine import EnsureContext
from commcare_connect.labs.synthetic.ensure.ensurers.opp_data import ensure_opp_data
from commcare_connect.labs.synthetic.ensure.ensurers.weekly_runs import ensure_weekly_runs
from commcare_connect.labs.synthetic.ensure.env_manifest import OppDataResource, ResetFlag, WeeklyRunsResource
from commcare_connect.labs.synthetic.registry import invalidate_cache
from commcare_connect.workflow.data_access import WorkflowDataAccess

OPP_ID = 10_071
SEED = 13

# Two personas with real display names, one cohort, one anomaly flagging the
# "struggling" persona on week index 1 (a MUAC field-outlier => SAM/MAM flag).
MANIFEST_YAML = f"""
opportunity_id: {OPP_ID}
opportunity_name: PAR Weekly Runs Opp
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
    reviewer_visible_in: [chc_nutrition_analysis]
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
    weeks = _mondays(3, start=dt.date(2026, 2, 2))  # 3 Mondays
    current = (dt.date(2026, 2, 2) + dt.timedelta(weeks=3)).isoformat()
    ctx = EnsureContext(env_dir=tmp_path, weeks=weeks, current_week=current)
    ensure_opp_data(OppDataResource(kind="opp_data", opportunity_id=OPP_ID, manifest="opp.yaml"), ctx)
    return ctx, weeks, current


def _completed_chc_runs(opp_id, def_id):
    wda = WorkflowDataAccess(opportunity_id=opp_id, access_token="labs-only")
    try:
        runs = wda.list_runs(definition_id=def_id)
        return [r for r in runs if r.opportunity_id == opp_id]
    finally:
        wda.close()


@pytest.mark.django_db
def test_weekly_runs_creates_per_week_completed_and_current_in_progress(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    resource = WeeklyRunsResource(
        kind="weekly_runs",
        opportunity_ids=[OPP_ID],
        template="chc_nutrition_analysis",
        current_week=ResetFlag(reset=False),
    )

    realized = ensure_weekly_runs(resource, ctx)

    def_id = realized["workflow_def_id"]
    runs = _completed_chc_runs(OPP_ID, def_id)
    completed = [r for r in runs if r.data.get("status") == "completed"]
    in_progress = [r for r in runs if r.data.get("status") == "in_progress"]

    # One completed run per completed week, one current-week in_progress run.
    assert len(completed) == len(weeks)
    assert len(in_progress) == 1
    assert in_progress[0].data.get("period_start") == current

    # completed_at backdated to each week's Monday.
    completed_periods = sorted(r.data.get("period_start") for r in completed)
    assert completed_periods == sorted(weeks)
    for r in completed:
        assert (r.data.get("completed_at") or "").startswith(r.data.get("period_start"))

    # Rows carry real display names + an approval signal.
    sample = completed[0]
    rows = sample.data["snapshot"]["pipelines"]["data"]["rows"]
    names = {row["name"] for row in rows}
    assert {"Asha Mensah", "Dele Okonkwo"} <= names
    for row in rows:
        assert "approved_visits" in row and "total_visits" in row
        assert 0 <= row["approved_visits"] <= row["total_visits"]

    # Run ids stashed on ctx for downstream ensurers.
    for w in weeks:
        assert ctx.ids[f"run:{OPP_ID}:{w}"] is not None
    assert ctx.ids[f"run:{OPP_ID}:{current}"] is not None


@pytest.mark.django_db
def test_flagged_persona_has_flag_on_the_right_week(tmp_path):
    from commcare_connect.flags.data_access import FlagsDataAccess
    from commcare_connect.flags.models import FlagRecord

    ctx, weeks, current = _setup_ctx(tmp_path)
    resource = WeeklyRunsResource(
        kind="weekly_runs",
        opportunity_ids=[OPP_ID],
        template="chc_nutrition_analysis",
    )
    ensure_weekly_runs(resource, ctx)

    flagged_week = weeks[1]  # anomaly week index 1
    flagged_run_id = ctx.ids[f"run:{OPP_ID}:{flagged_week}"]
    unflagged_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[0]}"]

    fda = FlagsDataAccess(opportunity_id=OPP_ID, access_token="labs-only")
    try:
        all_flags = fda.labs_api.get_records(experiment="flags", type="Flag", model_class=FlagRecord)
    finally:
        fda.close()

    dele_flagged_week = [
        f for f in all_flags if f.workflow_run_id == flagged_run_id and f.data.get("flw_id") == "dele"
    ]
    dele_unflagged_week = [
        f for f in all_flags if f.workflow_run_id == unflagged_run_id and f.data.get("flw_id") == "dele"
    ]

    # Dele (struggling + anomaly on week 1) carries a SAM/MAM flag on the
    # flagged week and none on the clean week.
    assert dele_flagged_week, "expected a flag for dele on the anomaly week"
    flag_keys = {f.data.get("flag_key") for f in dele_flagged_week}
    assert {"sam_low", "mam_low"} & flag_keys
    assert not dele_unflagged_week


@pytest.mark.django_db
def test_rerun_is_idempotent(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    resource = WeeklyRunsResource(
        kind="weekly_runs",
        opportunity_ids=[OPP_ID],
        template="chc_nutrition_analysis",
    )

    realized1 = ensure_weekly_runs(resource, ctx)
    def_id = realized1["workflow_def_id"]
    runs_after_first = _completed_chc_runs(OPP_ID, def_id)

    # Re-run with a fresh ctx (manifest re-stashed) -> no duplicate runs.
    ctx2, _, _ = _setup_ctx(tmp_path)
    realized2 = ensure_weekly_runs(resource, ctx2)
    runs_after_second = _completed_chc_runs(OPP_ID, def_id)

    assert len(runs_after_first) == len(runs_after_second)
    assert realized2["workflow_def_id"] == def_id
    # Same run ids reused per week.
    for w in weeks + [current]:
        assert ctx.ids[f"run:{OPP_ID}:{w}"] == ctx2.ids[f"run:{OPP_ID}:{w}"]


@pytest.mark.django_db
def test_current_week_reset_rebuilds_only_current_run(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    resource = WeeklyRunsResource(
        kind="weekly_runs",
        opportunity_ids=[OPP_ID],
        template="chc_nutrition_analysis",
    )
    ensure_weekly_runs(resource, ctx)
    def_id = ctx.ids["chc_watched_sources"][0]["workflow_definition_id"]

    completed_ids_before = {
        r.data.get("period_start"): r.id
        for r in _completed_chc_runs(OPP_ID, def_id)
        if r.data.get("status") == "completed"
    }
    current_id_before = ctx.ids[f"run:{OPP_ID}:{current}"]

    # Re-run with reset=True on a fresh ctx.
    ctx2, _, _ = _setup_ctx(tmp_path)
    reset_resource = WeeklyRunsResource(
        kind="weekly_runs",
        opportunity_ids=[OPP_ID],
        template="chc_nutrition_analysis",
        current_week=ResetFlag(reset=True),
    )
    ensure_weekly_runs(reset_resource, ctx2)

    completed_ids_after = {
        r.data.get("period_start"): r.id
        for r in _completed_chc_runs(OPP_ID, def_id)
        if r.data.get("status") == "completed"
    }
    current_id_after = ctx2.ids[f"run:{OPP_ID}:{current}"]

    # Completed runs untouched; current-week run rebuilt (new id).
    assert completed_ids_after == completed_ids_before
    assert current_id_after != current_id_before
    # Still exactly one in_progress run.
    in_progress = [r for r in _completed_chc_runs(OPP_ID, def_id) if r.data.get("status") == "in_progress"]
    assert len(in_progress) == 1


@pytest.mark.django_db
def test_missed_week_idxs_skips_run_and_stashes_no_id(tmp_path):
    """A declared missed week creates NO completed run and stamps NO run id.

    The PAR demo's "SOP missed" story needs a watched region to genuinely skip
    a completed week. weekly_runs must (a) not create_backdated_workflow_run for
    that (opp, week) and (b) leave ctx.ids["run:{opp}:{monday}"] unset so the
    audits/tasks ensurers land nothing on a non-existent run.
    """
    ctx, weeks, current = _setup_ctx(tmp_path)
    missed_idx = 1
    resource = WeeklyRunsResource(
        kind="weekly_runs",
        opportunity_ids=[OPP_ID],
        template="chc_nutrition_analysis",
        missed_week_idxs={OPP_ID: [missed_idx]},
        current_week=ResetFlag(reset=False),
    )

    ensure_weekly_runs(resource, ctx)
    def_id = ctx.ids["chc_watched_sources"][0]["workflow_definition_id"]

    completed = [r for r in _completed_chc_runs(OPP_ID, def_id) if r.data.get("status") == "completed"]
    completed_periods = sorted(r.data.get("period_start") for r in completed)

    # One fewer completed run; the missed week's Monday is absent.
    assert len(completed) == len(weeks) - 1
    assert weeks[missed_idx] not in completed_periods
    assert all(weeks[i] in completed_periods for i in range(len(weeks)) if i != missed_idx)

    # No run id stamped for the missed week; every other week is stamped.
    assert ctx.ids.get(f"run:{OPP_ID}:{weeks[missed_idx]}") is None
    for i, monday in enumerate(weeks):
        if i == missed_idx:
            continue
        assert ctx.ids.get(f"run:{OPP_ID}:{monday}") is not None

    # The declared missed set is stashed for the rollup ensurer's snapshot.
    assert ctx.ids["missed_week_idxs"][OPP_ID] == [missed_idx]
