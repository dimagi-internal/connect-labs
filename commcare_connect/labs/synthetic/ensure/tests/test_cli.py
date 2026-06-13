"""End-to-end test for the ensure engine + module CLI entrypoint.

Unlike ``test_engine.py`` (which monkeypatches the dispatch dict), this drives
the *real* five-ensurer chain through :func:`ensure_synthetic_data` against a
small but COMPLETE on-disk env: an env manifest plus the per-opp generator
manifests it references. It is a 2-opp env on purpose — only a good/incomplete
split (Opp A's coaching arc closes; Opp B's stays open + misses a week) produces
the full set of drill vars (``good_*`` / ``incomplete_*``), so a faithful CLI
e2e exercises everything the realized map can carry. A 1-opp env could only
assert ``par_*`` + one cluster; see the rollup ensurer test for the unit-level
good-vs-incomplete coverage.

We call ``ensure_synthetic_data`` directly (in-process) so it shares the
``django_db`` transaction, and separately assert that ``main([...])`` runs
without error and writes the same realized map.
"""

import json

import pytest

from commcare_connect.labs.synthetic.ensure.__main__ import main
from commcare_connect.labs.synthetic.ensure.engine import ensure_synthetic_data
from commcare_connect.labs.synthetic.registry import invalidate_cache

OPP_A = 10_091  # "Northern" — arc closes -> good cluster
OPP_B = 10_092  # "Southern" — arc open + misses week 0 -> incomplete cluster
SEED = 57


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


# completed_weeks=2 + include_current_week => 2 completed Mondays + 1 in-progress.
ENV_YAML = f"""
env: par_cli_test
timeline:
  completed_weeks: 2
  include_current_week: true
resources:
  - kind: opp_data
    opportunity_id: {OPP_A}
    manifest: a.yaml
  - kind: opp_data
    opportunity_id: {OPP_B}
    manifest: b.yaml
  - kind: weekly_runs
    opportunity_ids: [{OPP_A}, {OPP_B}]
    template: chc_nutrition_analysis
    missed_week_idxs:
      {OPP_B}: [0]
  - kind: run_audits
  - kind: tasks
  - kind: rollup
    opportunity_ids: [{OPP_A}, {OPP_B}]
    template: program_admin_report
"""


def _write_env(tmp_path):
    (tmp_path / "a.yaml").write_text(
        _manifest_yaml(
            OPP_A, "Northern Region Nutrition", follow_up_outcome_week=3, flw_id="dele_a", flw_name="Dele Okonkwo"
        )
    )
    (tmp_path / "b.yaml").write_text(
        _manifest_yaml(
            OPP_B, "Southern Region Nutrition", follow_up_outcome_week=None, flw_id="kofi_b", flw_name="Kofi Asare"
        )
    )
    env = tmp_path / "env.yaml"
    env.write_text(ENV_YAML)
    invalidate_cache()
    return env


@pytest.mark.django_db
def test_engine_end_to_end_realizes_drill_vars(tmp_path):
    env = _write_env(tmp_path)
    out = tmp_path / "realized.json"

    realized = ensure_synthetic_data(str(env), out=str(out))

    # opp_data readiness markers for both opps.
    assert realized[f"opp_{OPP_A}_ready"] is True
    assert realized[f"opp_{OPP_B}_ready"] is True

    # weekly_runs emits the primary chc definition id.
    assert "workflow_def_id" in realized

    # rollup emits the PAR run + its url, watching both opps.
    par_run_id = realized["par_run_id"]
    par_def_id = realized["par_def_id"]
    assert realized["par_url"] == f"/labs/workflow/{par_def_id}/run/?run_id={par_run_id}&opportunity_id={OPP_A}"
    # The PAR def id is distinct from the chc def the weekly_runs ensurer owns.
    assert par_def_id != realized["workflow_def_id"]

    # Full good/incomplete drill split (only a 2-opp env produces both).
    assert realized["good_opp_id"] == OPP_A
    assert realized["incomplete_opp_id"] == OPP_B
    assert realized["good_audit_id"]
    assert realized["good_task_id"]
    assert realized["incomplete_audit_id"]
    assert realized["incomplete_task_id"]
    assert realized["task_good_url"] == f"/tasks/{realized['good_task_id']}/edit/?opportunity_id={OPP_A}"
    assert realized["audit_good_url"] == f"/audit/{realized['good_audit_id']}/?opportunity_id={OPP_A}"

    # realized.json was written and round-trips to the same map.
    on_disk = json.loads(out.read_text())
    assert on_disk["par_run_id"] == par_run_id
    assert on_disk["good_task_id"] == realized["good_task_id"]


@pytest.mark.django_db
def test_main_cli_runs_and_writes_realized(tmp_path):
    env = _write_env(tmp_path)
    out = tmp_path / "cli_realized.json"

    rc = main([str(env), "--out", str(out)])

    assert rc == 0
    on_disk = json.loads(out.read_text())
    # The CLI realized the same headline vars.
    assert on_disk["par_run_id"]
    assert on_disk["good_opp_id"] == OPP_A
    assert on_disk["incomplete_opp_id"] == OPP_B
